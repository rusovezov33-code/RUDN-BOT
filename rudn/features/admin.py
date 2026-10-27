"""Инструменты владельца: быстрое добавление (Д/з · Лаба · Экзамен · Зачёт ·
Событие), загрузка файлов в разделы, статус, бэкап и восстановление data.json."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from html import escape
from io import BytesIO

from aiogram import F, Router
from aiogram.types import BufferedInputFile, Message

from rudn.core import config, tasks, timeutil as tu
from rudn.core.registry import CommandSpec, Feature, MenuButton
from rudn.core.storage import encode
from rudn.core.ui import Ctx, b, cancel_row, chunk, maybe, nav, row_of, short

KIND_BUTTONS = [
    ("hw", "📝 Д/з"),
    ("lab", "🧪 Лаба / практика"),
    ("ctrl", "✍️ Контрольная / тест"),
    ("exam", "🎓 Экзамен"),
    ("credit", "✅ Зачёт"),
    ("ev", "🔔 Напоминание"),
]
KIND_TITLES = dict(KIND_BUTTONS)
ADD_STATE = "add_input"
CAL_DAYS = 28


# ---------------------------------------------------------------- добавление
def _state(ctx: Ctx):
    state = ctx.pending()
    return state if state is not None and state.name == ADD_STATE else None


def _start(ctx: Ctx, **data) -> None:
    ctx.wait_text(ADD_STATE, keep=("add",), **data)


def _header(info: dict, data: dict) -> list[str]:
    lines = [f"➕ <b>{KIND_TITLES.get(info.get('kind'), 'Добавить')}</b>"]
    si = info.get("si")
    if isinstance(si, int) and 0 <= si < len(data.get("subjects") or []):
        lines.append(f"Предмет: <b>{escape(data['subjects'][si]['name'])}</b>")
    if info.get("date"):
        lines.append(f"Срок: <b>{tu.fmt_dm(date.fromisoformat(info['date']))}</b>")
    return lines


async def screen_kinds(ctx: Ctx, si: int | None) -> None:
    data = ctx.data
    _start(ctx, si=si)
    suffix = f":{si}" if si is not None else ""
    buttons = [
        b(title, f"add:k:{kind}{suffix}")
        for kind, title in KIND_BUTTONS
        if not (kind == "ev" and si is not None)
    ]
    lines = ["➕ <b>Что добавить?</b>"]
    if si is not None:
        lines.append(f"Предмет: <b>{escape(data['subjects'][si]['name'])}</b>")
    rows = chunk(buttons, 2)
    rows.append(nav(f"s:{si}" if si is not None else None))
    await ctx.show("\n".join(lines), rows)


async def screen_pick_subject(ctx: Ctx) -> None:
    state = _state(ctx)
    data = ctx.data
    buttons = [b(short(tu.short_name(s.get("name") or "?"), 26), f"add:su:{i}") for i, s in enumerate(data.get("subjects") or [])]
    rows = chunk(buttons, 2)
    rows.append(cancel_row())
    await ctx.show("\n".join(_header(state.data, data) + ["", "Выбери предмет:"]), rows)


def _cal_rows(page: int) -> list[list]:
    start = tu.today() + timedelta(days=page * CAL_DAYS)
    days = [start + timedelta(days=i) for i in range(CAL_DAYS)]
    rows = chunk([b(tu.fmt_dm(day), f"add:d:{day.isoformat()}") for day in days], 4)
    nav_row = []
    if page > 0:
        nav_row.append(b("◀ Раньше", f"add:cal:{page - 1}"))
    if page < 12:
        nav_row.append(b("Позже ▶", f"add:cal:{page + 1}"))
    rows.append(nav_row)
    return rows


async def screen_pick_date(ctx: Ctx, page: int | None = None, error: str | None = None) -> None:
    state = _state(ctx)
    data = ctx.data
    info = state.data
    info["step"] = "date"
    state.touch()
    today = tu.today()
    rows: list[list] = []
    si = info.get("si")
    lines = _header(info, data) + [""]
    if error:
        lines.append(f"⚠️ {error}")
    quick_mode = page is None and info.get("kind") in ("hw", "lab", "ctrl") and isinstance(si, int)
    if quick_mode:
        name = data["subjects"][si]["name"]
        lessons = [day for day in tu.upcoming_lesson_dates(name, today + timedelta(days=1), 60)][:6]
        if lessons:
            lines.append("К какой паре? (или выбери другую дату)")
            rows += chunk([b(f"🗓 {tu.fmt_dm(day)}", f"add:d:{day.isoformat()}") for day in lessons], 3)
        else:
            lines.append("Выбери срок:")
        rows.append(
            [
                b("Сегодня", f"add:d:{today.isoformat()}"),
                b("Завтра", f"add:d:{(today + timedelta(days=1)).isoformat()}"),
                b("📅 Другая", "add:cal:0"),
            ]
        )
        rows.append([b("Без срока", "add:d:none")])
    else:
        lines.append("Выбери дату:")
        rows += _cal_rows(page or 0)
        if info.get("kind") != "ev":
            rows.append([b("Без даты", "add:d:none")])
    lines.append("Или напиши дату сообщением: <code>15.01</code> или <code>15.01.2027</code>")
    rows.append(cancel_row())
    await ctx.show("\n".join(lines), rows)


async def screen_ask_text(ctx: Ctx, error: str | None = None) -> None:
    state = _state(ctx)
    info = state.data
    info["step"] = "text"
    state.touch()
    kind = info.get("kind")
    lines = _header(info, ctx.data) + [""]
    if error:
        lines.append(f"⚠️ {error}")
    rows: list[list] = []
    if kind in ("exam", "credit"):
        lines.append("Напиши детали: время и аудиторию, например <code>10:00, ЕГФ ауд. 375</code>.")
        rows.append([b("Без деталей", "add:nd")])
    elif kind == "ev":
        lines.append("Напиши текст напоминания:")
    else:
        lines.append("Напиши текст задания:")
    lines.append("<i>(ожидаю 15 минут)</i>")
    rows.append(cancel_row())
    await ctx.show("\n".join(lines), rows)


_EXAM_DETAILS = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*[,;]?\s*(.*)$", re.S)


def save_item(ctx: Ctx, info: dict, text: str) -> tuple[str, list[list]]:
    data = ctx.data
    kind = info.get("kind")
    due = date.fromisoformat(info["date"]) if info.get("date") else None
    si = info.get("si")
    if kind == "ev":
        due = due or tu.today()
        data.setdefault("events", []).append(
            {
                "day": due.day,
                "month": due.month,
                "year": due.year,
                "date": due.isoformat(),
                "text": text,
                "created": tu.now().isoformat(),
            }
        )
        ctx.save()
        msg = f"✅ Напоминание на {tu.fmt_dm(due)}:\n{escape(text)}"
        return msg, [row_of(b("➕ Ещё", "add"), maybe("📅 Открыть день", f"d:{due.isoformat()}")), nav()]
    extra: dict = {}
    body = text
    if kind in ("exam", "credit"):
        extra["exam_type"] = kind
        match = _EXAM_DETAILS.match(text or "")
        if match:
            extra["time"] = f"{int(match.group(1)):02d}:{match.group(2)}"
            extra["place"] = match.group(3).strip()
            body = ""
        kind = "exam"
    item = tasks.new_item(data, kind, si, body, due, **extra)
    ctx.save()
    when = f" · до {tu.fmt_dm(due)}" if due and kind != "exam" else (f" · {tu.fmt_dm(due)}" if due else "")
    subject = tasks.subject_name(item, data)
    lines = [f"✅ Добавлено: {tasks.kind_icon(item)} {escape(tasks.kind_label(item))}{when}"]
    if subject:
        lines.append(f"Предмет: <b>{escape(subject)}</b>")
    details = ", ".join(escape(str(item[k])) for k in ("time", "place") if item.get(k))
    if details:
        lines.append(f"🕘 {details}")
    if item.get("text"):
        lines.append(escape(item["text"]))
    more = f"add:s:{si}" if isinstance(si, int) else "add"
    target = maybe("🎓 Экзамены", "ex") if kind == "exam" else maybe("🔥 Дедлайны", "dl")
    return "\n".join(lines), [row_of(b("➕ Ещё", more), target), nav(f"s:{si}" if isinstance(si, int) else None)]


async def on_add(ctx: Ctx, args: list[str]) -> None:
    if not await ctx.require_owner():
        return
    action = args[0] if args else ""
    if action == "":
        await screen_kinds(ctx, None)
        return
    if action == "s":
        si = int(args[1])
        if not 0 <= si < len(ctx.data.get("subjects") or []):
            await ctx.answer("Предмет не найден", alert=True)
            return
        # сразу гасим «часики» с понятным текстом — кнопка не выглядит «мёртвой»
        await ctx.answer(f"➕ {tu.short_name(ctx.data['subjects'][si].get('name') or '')}")
        await screen_kinds(ctx, si)
        return
    state = _state(ctx)
    if state is None and action == "k":
        # «➕ Экзамен» / «➕ Зачёт» с экрана экзаменов — это начало нового ввода,
        # а не устаревший шаг
        _start(ctx, si=None)
        state = _state(ctx)
    if state is None:
        await ctx.answer("⌛ Ввод устарел, начни заново")
        await screen_kinds(ctx, None)
        return
    info = state.data
    subject_count = len(ctx.data.get("subjects") or [])
    if action == "k":
        kind = args[1] if len(args) > 1 else ""
        if kind not in KIND_TITLES:
            await screen_kinds(ctx, None)
            return
        info["kind"] = kind
        if len(args) > 2 and args[2].isdigit():
            if not 0 <= int(args[2]) < subject_count:
                await ctx.answer("Предмет не найден", alert=True)
                await screen_kinds(ctx, None)
                return
            info["si"] = int(args[2])
        if kind == "ev":
            info["si"] = None
        if kind == "ev" or isinstance(info.get("si"), int):
            await screen_pick_date(ctx)
        else:
            await screen_pick_subject(ctx)
    elif action == "su":
        si = int(args[1])
        if not 0 <= si < subject_count:
            await ctx.answer("Предмет не найден", alert=True)
            await screen_pick_subject(ctx)
            return
        info["si"] = si
        await screen_pick_date(ctx)
    elif action == "cal":
        await screen_pick_date(ctx, page=max(0, int(args[1])))
    elif action == "d":
        info["date"] = None if args[1] == "none" else date.fromisoformat(args[1]).isoformat()
        await screen_ask_text(ctx)
    elif action == "nd":
        ctx.clear_pending()
        text, rows = save_item(ctx, info, "")
        await ctx.answer("Добавлено")
        await ctx.show(text, rows)
    else:
        await screen_kinds(ctx, info.get("si"))


async def on_add_text(ctx: Ctx, text: str) -> None:
    state = _state(ctx)
    if state is None:
        return
    info = state.data
    if not info.get("kind"):
        await screen_kinds(ctx, info.get("si"))
        return
    if info.get("step") == "date":
        picked = tu.parse_user_date(text)
        if picked is None:
            await screen_pick_date(ctx, error="Не понял дату. Пример: 15.01 или 15.01.2027")
            return
        info["date"] = picked.isoformat()
        await screen_ask_text(ctx)
        return
    if info.get("step") != "text":
        if info.get("kind") == "ev" or isinstance(info.get("si"), int):
            await screen_pick_date(ctx)
        else:
            await screen_pick_subject(ctx)
        return
    if not text:
        await screen_ask_text(ctx, "Пустой текст")
        return
    ctx.clear_pending()
    message, rows = save_item(ctx, info, text[:2000])
    await ctx.show(message, rows)


# ---------------------------------------------------------------- сервис
def count_files(data: dict) -> int:
    return sum(len(sec.get("files") or []) for s in data.get("subjects") or [] for sec in s.get("sections") or [])


async def screen_service(ctx: Ctx) -> None:
    data = ctx.data
    today = tu.today()
    base = config.public_base()
    homework = data.get("homework") or []
    open_count = sum(1 for item in homework if not item.get("done"))
    lines = [
        "⚙️ <b>Сервис</b>",
        f"Данные: <code>{escape(str(ctx.store.path))}</code>",
        f"Неделя: {tu.week_label(tu.week_type(today))}",
        f"Предметов: {len(data['subjects'])} · файлов: {count_files(data)}",
        f"Заданий: {len(homework)} (открытых {open_count}) · событий: {len(data.get('events') or [])}",
        f"Публичный адрес: {escape(base or 'нет')}",
        "",
        "Восстановить базу: пришли файл <code>data.json</code> документом.",
        "Добавить файл в предмет: пришли файл документом.",
    ]
    rows = [[b("💾 Скачать бэкап", "adm:bk")], nav()]
    await ctx.show("\n".join(lines), rows)


async def send_backup(ctx: Ctx) -> None:
    payload = encode(ctx.data).encode("utf-8")
    stamp = tu.now().strftime("%Y-%m-%d-%H%M")
    await ctx.bot.send_document(
        ctx.chat_id,
        BufferedInputFile(payload, filename="data.json"),
        caption=f"Копия data.json · {stamp} МСК",
    )


async def on_admin(ctx: Ctx, args: list[str]) -> None:
    if not await ctx.require_owner():
        return
    if args and args[0] == "bk":
        await ctx.answer("Отправляю бэкап…")
        await send_backup(ctx)
        return
    await screen_service(ctx)


async def cmd_status(ctx: Ctx) -> None:
    if await ctx.require_owner():
        await screen_service(ctx)


async def cmd_backup(ctx: Ctx) -> None:
    if await ctx.require_owner():
        await send_backup(ctx)


# ---------------------------------------------------------------- файлы и восстановление
router = Router(name="admin-files")


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    ctx = Ctx.from_message(message)
    if ctx.is_owner:
        await message.answer("Отправь это файлом, без сжатия: скрепка → Файл.")


@router.message(F.document)
async def handle_document(message: Message) -> None:
    ctx = Ctx.from_message(message)
    if not ctx.is_owner or not message.document:
        return
    document = message.document
    file_name = document.file_name or "document"
    parsed = None
    looks_like_db = file_name.lower().endswith(".json") and "data" in file_name.lower()
    if looks_like_db and not (document.file_size and document.file_size > 2_000_000):
        # если файл не база бота — это обычный файл, предлагаем положить в раздел
        buffer = BytesIO()
        await message.bot.download(document, destination=buffer)
        raw = buffer.getvalue().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("subjects"), list):
                raise ValueError
        except Exception:
            parsed = None
    if parsed is not None:
        ctx.wait_text("restore", keep=("rs",), raw=raw)
        text = (
            "♻️ <b>Заменить текущую базу этим файлом?</b>\n"
            f"Предметов: {len(parsed['subjects'])}, файлов: {count_files(parsed)}, "
            f"заданий: {len(parsed.get('homework') or [])}, событий: {len(parsed.get('events') or [])}.\n"
            "Текущая база сначала придёт тебе бэкапом."
        )
        await ctx.show(text, [[b("✅ Заменить", "rs:yes"), b("❌ Отмена", "x")]])
        return
    ctx.wait_text("upload", keep=("up",), file_id=document.file_id, file_name=file_name, mime=document.mime_type)
    buttons = [b(short(tu.short_name(s.get("name") or "?"), 26), f"up:s:{i}") for i, s in enumerate(ctx.data.get("subjects") or [])]
    rows = chunk(buttons, 2)
    rows.append(cancel_row())
    await ctx.show(f"📎 Файл <b>{escape(file_name)}</b> получен.\nВ какой предмет?", rows)


async def on_upload(ctx: Ctx, args: list[str]) -> None:
    if not await ctx.require_owner():
        return
    state = ctx.pending()
    if state is None or state.name != "upload":
        await ctx.answer("⌛ Сессия истекла, пришли файл ещё раз", alert=True)
        return
    info = state.data
    data = ctx.data
    try:
        if args[0] == "s":
            si = int(args[1])
            subject = data["subjects"][si]
            rows = chunk(
                [b(short(sec.get("title") or "Раздел", 30), f"up:sec:{si}:{j}") for j, sec in enumerate(subject["sections"])],
                1,
            )
            rows.append([b("⬅ Другой предмет", "up:back"), *cancel_row()])
            state.touch()
            await ctx.show(f"📎 <b>{escape(info['file_name'])}</b>\nПредмет: <b>{escape(subject['name'])}</b>\nВыбери раздел:", rows)
            return
        if args[0] == "back":
            buttons = [b(short(tu.short_name(s.get("name") or "?"), 26), f"up:s:{i}") for i, s in enumerate(data.get("subjects") or [])]
            await ctx.show(f"📎 <b>{escape(info['file_name'])}</b>\nВ какой предмет?", chunk(buttons, 2) + [cancel_row()])
            return
        if args[0] == "sec":
            si, j = int(args[1]), int(args[2])
            section = data["subjects"][si]["sections"][j]
        else:
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError):
        await ctx.answer("Не найдено", alert=True)
        return
    ctx.clear_pending()
    section.setdefault("files", []).append({"file_id": info["file_id"], "name": info["file_name"], "mime": info.get("mime")})
    ctx.save()
    await ctx.answer("✅ Файл добавлен")
    await ctx.show(
        f"✅ Файл добавлен: <b>{escape(info['file_name'])}</b>",
        [row_of(maybe("📄 Открыть раздел", f"s:{si}:{j}")), nav()],
    )


async def on_restore(ctx: Ctx, args: list[str]) -> None:
    if not await ctx.require_owner():
        return
    state = ctx.pending()
    if not args or args[0] != "yes" or state is None or state.name != "restore":
        ctx.clear_pending()
        await ctx.answer("⌛ Сессия истекла" if args and args[0] == "yes" else "Отменено")
        await ctx.show("Восстановление отменено.", [nav()])
        return
    raw = state.data["raw"]
    ctx.clear_pending()
    try:
        parsed = json.loads(raw)
    except Exception:
        await ctx.show("Файл не похож на data.json.", [nav()])
        return
    await send_backup(ctx)
    data = ctx.store.replace(parsed)
    await ctx.answer("База заменена")
    await ctx.show(
        f"✅ База заменена. Предметов: {len(data['subjects'])}, файлов: {count_files(data)}, "
        f"заданий: {len(data['homework'])}, событий: {len(data['events'])}.",
        [nav()],
    )


feature = Feature(
    name="admin",
    callbacks={"add": on_add, "adm": on_admin, "up": on_upload, "rs": on_restore},
    menu=[
        MenuButton("➕ Добавить", "add", order=90, row="admin", owner_only=True),
        MenuButton("⚙️ Сервис", "adm", order=91, row="admin", owner_only=True),
    ],
    commands=[
        CommandSpec("status", "Статус бота", cmd_status, hidden=True),
        CommandSpec("backup", "Бэкап data.json", cmd_backup, hidden=True),
    ],
    text_states={ADD_STATE: on_add_text},
    router=router,
)
