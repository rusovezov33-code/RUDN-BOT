import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    Message,
)

TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0")) or None
DATA_FILE = Path(__file__).with_name("data.json")

router = Router()
pending_uploads: dict[int, dict[str, Any]] = {}
pending_events: dict[int, str] = {}
pending_homework: dict[int, dict[str, Any]] = {}

SCHEDULE = {
    "ПН": [
        ("12:00–13:20", "Русский язык для иностранцев", "ЕГФ ауд. 375", "both"),
        ("13:30–14:50", "Русский язык для иностранцев", "ЕГФ ауд. 375", "both"),
        ("19:30–20:50", "Введение в специальность", "лек (ДОТ)", "lower"),
    ],
    "ВТ": [
        ("9:00–10:20", "Латинский язык", "ЕГФ ауд. 284", "both"),
        ("10:30–11:50", "Латинский язык", "ЕГФ ауд. 284/281", "both"),
        ("13:30–14:50", "Химия", "ул. Орджоникидзе 3, ауд. 623", "both"),
        ("19:30–20:50", "Физика", "лек н.н. (ДОТ)", "lower"),
    ],
    "СР": [
        ("15:00–16:20", "РКИ", "ЕГФ ауд. 359", "both"),
        ("16:30–17:50", "РКИ", "ЕГФ ауд. 359", "both"),
        ("19:30–20:50", "Основы российской государственности", "лек н.н. (ДОТ)", "lower"),
    ],
    "ЧТ": [
        ("10:30–11:50", "Основы военной подготовки / БЖД", "ВПМ", "both"),
        ("12:00–13:20", "Медицинская информатика", "ЕГФ ауд. 452/451", "both"),
        ("13:30–14:10", "Медицинская информатика", "ЕГФ ауд. 452/451", "both"),
        ("18:00–19:20", "Химия биогенных элементов", "(ДОТ)", "both"),
    ],
    "ПТ": [
        ("9:00–10:20", "Медицинская элементология", "АТИ ауд. 7", "both"),
        ("15:00–16:20", "РЯВАС", "ИРЯ ауд. 303", "both"),
        ("16:30–17:50", "РЯВАС", "ИРЯ ауд. 303", "both"),
    ],
    "СБ": [
        ("9:00–10:20", "Математика", "ЕГФ ауд. 331", "lower"),
        ("11:10–13:20", "Физическая культура", "ФОК", "both"),
        ("15:00–16:20", "Основы российской государственности", "ЕГФ ауд. 285", "both"),
    ],
}

DAY_MAP = {0: "ПН", 1: "ВТ", 2: "СР", 3: "ЧТ", 4: "ПТ", 5: "СБ", 6: "ВС"}
DAY_NAMES = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]


def load() -> dict:
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        data.setdefault("events", [])
        data.setdefault("homework", [])
        return data
    except Exception:
        return {"pages": [], "subjects": [], "events": [], "homework": []}


def save(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_owner(user_id: int | None) -> bool:
    return OWNER_ID is not None and user_id == OWNER_ID


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def nav(back=None):
    row = []
    if back and back != "home":
        row.append(Btn(text="⬅️ Назад", callback_data=back))
    row.append(Btn(text="🏠 Меню", callback_data="home"))
    return row


def link_rows(links):
    return [[Btn(text=f"🔗 {l['title']}", url=l["url"])] for l in links if l.get("url")]


def get_current_week_type() -> str:
    return "lower"


def get_moscow_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=3)


def get_subject_days(subject_name: str) -> set[str]:
    days = set()
    for day, lessons in SCHEDULE.items():
        for _, subj, _, week in lessons:
            if subject_name.lower() in subj.lower() or subj.lower() in subject_name.lower():
                days.add(day)
    return days


def get_possible_dates_for_subject(subject_name: str) -> list[int]:
    now = get_moscow_now()
    year, month = now.year, now.month
    subject_days = get_subject_days(subject_name)
    if not subject_days:
        return []
    possible = []
    for day in range(1, 32):
        try:
            dt = datetime(year, month, day)
        except ValueError:
            continue
        weekday_name = DAY_NAMES[dt.weekday()]
        if weekday_name not in subject_days:
            continue
        week_num = dt.isocalendar()[1]
        week_type = "lower" if week_num % 2 == 0 else "upper"
        for _, subj, _, w in SCHEDULE.get(weekday_name, []):
            if subject_name.lower() in subj.lower() and w in ("both", week_type):
                possible.append(day)
                break
    return sorted(set(possible))


async def show(event, text: str, markup: InlineKeyboardMarkup):
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def screen_home(event):
    d = load()
    rows = [[Btn(text="📚 Предметы", callback_data="subs")]]
    pages = [Btn(text=p["title"], callback_data=f"p:{i}") for i, p in enumerate(d.get("pages", []))]
    rows += [pages[i : i + 2] for i in range(0, len(pages), 2)]
    if is_owner(event.from_user.id if hasattr(event, "from_user") and event.from_user else None):
        rows.append([Btn(text="➕ Добавить событие", callback_data="addevent")])
        rows.append([Btn(text="📝 Добавить Д/з", callback_data="addhw")])
    await show(event, "🎓 <b>РУДН: учебное пространство</b>\nВыбери раздел:", kb(rows))


async def screen_subjects(event):
    d = load()
    subjects = d.get("subjects", [])
    rows = [[Btn(text=s["name"], callback_data=f"s:{i}")] for i, s in enumerate(subjects)]
    rows.append(nav())
    text = "📚 <b>Предметы</b>" if subjects else "📚 <b>Предметы</b>\n\nПока ничего нет."
    await show(event, text, kb(rows))


async def screen_subject(event, si: int):
    d = load()
    try:
        s = d["subjects"][si]
    except (IndexError, KeyError):
        await show(event, "Предмет не найден.", kb([nav()]))
        return
    lines = [f"<b>{escape(s.get('name', 'Без названия'))}</b>"]
    if s.get("teacher") and s["teacher"] != "Не указано":
        lines.append(f"👤 {escape(s['teacher'])}")
    if s.get("semester"):
        lines.append(f"🗓 Семестр: {escape(str(s['semester']))}")
    if s.get("contacts") and s["contacts"] != "Не указано":
        lines.append(f"✉️ {escape(s['contacts'])}")
    if s.get("description"):
        lines.append(f"\n{escape(s['description'])}")
    rows = [[Btn(text=sec["title"], callback_data=f"s:{si}:{j}")] for j, sec in enumerate(s.get("sections", []))]
    if not any("Д/з" in sec.get("title", "") or "д/з" in sec.get("title", "").lower() for sec in s.get("sections", [])):
        rows.append([Btn(text="📝 Д/з", callback_data=f"hwview:{si}")])
    rows.append(nav("subs"))
    await show(event, "\n".join(lines), kb(rows))


async def screen_section(event, si: int, j: int):
    d = load()
    try:
        s = d["subjects"][si]
        sec = s["sections"][j]
    except (IndexError, KeyError):
        await show(event, "Раздел не найден.", kb([nav()]))
        return
    body = escape(sec.get("text") or "")
    has_any = body or sec.get("links") or sec.get("files")
    text = (
        f"<b>{escape(s.get('name', ''))}</b>\n"
        f"{escape(sec.get('title', ''))}\n\n"
        + (body if has_any else "Пока ничего нет.")
    )
    rows = link_rows(sec.get("links", []))
    files = sec.get("files", [])
    for fi, f in enumerate(files):
        name = f.get("name", "файл") if isinstance(f, dict) else "файл"
        btn_name = name if len(name) <= 40 else name[:37] + "..."
        rows.append([Btn(text=f"📄 {btn_name}", callback_data=f"file:{si}:{j}:{fi}")])
    rows.append(nav(f"s:{si}"))
    await show(event, text, kb(rows))


async def screen_homework(event, si: int):
    d = load()
    try:
        subject = d["subjects"][si]
    except (IndexError, KeyError):
        await show(event, "Предмет не найден.", kb([nav()]))
        return
    now = get_moscow_now()
    today = now.day
    hws = [h for h in d.get("homework", []) if h.get("subject_idx") == si and h.get("day", 0) >= today and not h.get("done")]
    lines = [f"📝 <b>Д/з по предмету: {escape(subject['name'])}</b>\n"]
    if not hws:
        lines.append("Актуальных заданий нет.")
    else:
        for h in hws:
            lines.append(f"• До {h['day']} числа: {escape(h.get('text', ''))}")
    rows = []
    for h in hws:
        rows.append([Btn(text=f"✅ Выполнено: {h.get('text', '')[:30]}...", callback_data=f"hwdone:{h.get('id', 0)}")])
    rows.append(nav(f"s:{si}"))
    await show(event, "\n".join(lines), kb(rows))


async def screen_page(event, pi: int):
    d = load()
    try:
        p = d["pages"][pi]
    except (IndexError, KeyError):
        await show(event, "Страница не найдена.", kb([nav()]))
        return
    if "Расписание" in p.get("title", ""):
        await screen_schedule_choice(event)
        return
    text = (
        f"<b>{escape(p.get('title', ''))}</b>\n\n"
        + (escape(p.get("text") or "") if p.get("text") else "Пока ничего нет.")
    )
    rows = link_rows(p.get("links", []))
    rows.append(nav())
    await show(event, text, kb(rows))


async def screen_schedule_choice(event):
    rows = [
        [Btn(text="📅 Сегодня", callback_data="sched:today")],
        [Btn(text="📅 Завтра", callback_data="sched:tomorrow")],
        nav(),
    ]
    await show(event, "📅 <b>Расписание</b>\nВыбери день:", kb(rows))


async def screen_schedule(event, offset_days: int = 0):
    now = get_moscow_now() + timedelta(days=offset_days)
    weekday = now.weekday()
    day_code = DAY_MAP.get(weekday, "ПН")
    week_type = get_current_week_type()
    week_label = "нижняя" if week_type == "lower" else "верхняя"
    target_day = now.day
    label = "сегодня" if offset_days == 0 else "завтра"

    lessons = SCHEDULE.get(day_code, [])
    filtered = [l for l in lessons if l[3] in ("both", week_type)]

    lines = [f"📅 <b>Расписание на {label} ({day_code}, {week_label} неделя)</b>\n"]
    if not filtered:
        lines.append("Занятий нет или выходной.")
    else:
        d = load()
        subjects = d.get("subjects", [])
        for time, subject, place, _ in filtered:
            lines.append(f"• {time} — {subject}")
            if place:
                lines.append(f"  📍 {place}")
            for h in d.get("homework", []):
                if h.get("done"):
                    continue
                if h.get("day") != target_day:
                    continue
                si = h.get("subject_idx")
                if si is not None and 0 <= si < len(subjects):
                    subj_name = subjects[si].get("name", "")
                    if subject.lower() in subj_name.lower() or subj_name.lower() in subject.lower():
                        lines.append(f"  📝 Д/з: {escape(h.get('text', ''))}")

    d = load()
    today_events = [e for e in d.get("events", []) if e.get("day") == target_day]
    if today_events:
        lines.append("\n🔔 <b>Напоминания:</b>")
        for e in today_events:
            lines.append(f"• {escape(e.get('text', ''))}")

    rows = [
        [Btn(text="📅 Сегодня", callback_data="sched:today")],
        [Btn(text="📅 Завтра", callback_data="sched:tomorrow")],
        nav(),
    ]
    await show(event, "\n".join(lines), kb(rows))


@router.callback_query(F.data == "addhw")
async def start_add_homework(c: CallbackQuery):
    if not is_owner(c.from_user.id if c.from_user else None):
        await c.answer("Нет доступа", show_alert=True)
        return
    pending_homework[c.from_user.id] = {}
    await c.message.answer("Напиши текст домашнего задания:")
    await c.answer()


@router.message(F.text)
async def handle_homework_text(m: Message):
    if not is_owner(m.from_user.id if m.from_user else None):
        return
    if m.from_user.id not in pending_homework:
        return
    text = (m.text or "").strip()
    if not text:
        await m.answer("Текст пустой.")
        return
    pending_homework[m.from_user.id]["text"] = text
    d = load()
    subjects = d.get("subjects", [])
    rows = [[Btn(text=s["name"], callback_data=f"hwsel:s:{i}")] for i, s in enumerate(subjects)]
    rows.append([Btn(text="❌ Отмена", callback_data="hwsel:cancel")])
    await m.answer("Выбери предмет:", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("hwsel:"))
async def handle_hw_selection(c: CallbackQuery):
    if not is_owner(c.from_user.id if c.from_user else None):
        await c.answer("Нет доступа", show_alert=True)
        return
    user_id = c.from_user.id
    if user_id not in pending_homework:
        await c.answer("Сессия истекла.", show_alert=True)
        return
    parts = c.data.split(":")
    if parts[1] == "cancel":
        pending_homework.pop(user_id, None)
        await c.message.edit_text("Отменено.")
        await c.answer()
        return
    if parts[1] == "s":
        si = int(parts[2])
        pending_homework[user_id]["si"] = si
        d = load()
        subject_name = d["subjects"][si]["name"]
        dates = get_possible_dates_for_subject(subject_name)
        if not dates:
            await c.message.edit_text("Не нашёл дней с этим предметом в расписании.")
            pending_homework.pop(user_id, None)
            await c.answer()
            return
        rows = []
        row = []
        for day in dates:
            row.append(Btn(text=str(day), callback_data=f"hwday:{day}"))
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Btn(text="❌ Отмена", callback_data="hwsel:cancel")])
        await c.message.edit_text(
            f"Предмет: <b>{escape(subject_name)}</b>\nВыбери день (только когда есть пары):",
            reply_markup=kb(rows),
            parse_mode=ParseMode.HTML,
        )
        await c.answer()
        return
    await c.answer()


@router.callback_query(F.data.startswith("hwday:"))
async def handle_hw_day(c: CallbackQuery):
    if not is_owner(c.from_user.id if c.from_user else None):
        await c.answer("Нет доступа", show_alert=True)
        return
    user_id = c.from_user.id
    if user_id not in pending_homework:
        await c.answer("Сессия истекла.", show_alert=True)
        return
    day = int(c.data.split(":")[1])
    info = pending_homework.pop(user_id)
    d = load()
    hw_id = len(d.get("homework", []))
    d.setdefault("homework", []).append({
        "id": hw_id,
        "subject_idx": info.get("si"),
        "text": info.get("text"),
        "day": day,
        "done": False,
        "created": get_moscow_now().isoformat(),
    })
    save(d)
    await c.message.edit_text(f"✅ Д/з добавлено до {day} числа:\n{escape(info.get('text', ''))}", parse_mode=ParseMode.HTML)
    await c.answer()


@router.callback_query(F.data.startswith("hwdone:"))
async def mark_homework_done(c: CallbackQuery):
    if not is_owner(c.from_user.id if c.from_user else None):
        await c.answer("Нет доступа", show_alert=True)
        return
    hw_id = int(c.data.split(":")[1])
    d = load()
    for h in d.get("homework", []):
        if h.get("id") == hw_id:
            h["done"] = True
            break
    save(d)
    await c.answer("Отмечено как выполненное")
    si = None
    for h in d.get("homework", []):
        if h.get("id") == hw_id:
            si = h.get("subject_idx")
            break
    if si is not None:
        await screen_homework(c, si)


@router.callback_query(F.data.startswith("hwview:"))
async def view_homework(c: CallbackQuery):
    si = int(c.data.split(":")[1])
    await screen_homework(c, si)


@router.callback_query(F.data == "addevent")
async def start_add_event(c: CallbackQuery):
    if not is_owner(c.from_user.id if c.from_user else None):
        await c.answer("Нет доступа", show_alert=True)
        return
    pending_events[c.from_user.id] = ""
    await c.message.answer("Напиши текст события:")
    await c.answer()


@router.message(F.text)
async def handle_event_text(m: Message):
    if not is_owner(m.from_user.id if m.from_user else None):
        return
    if m.from_user.id not in pending_events:
        return
    text = (m.text or "").strip()
    if not text:
        return
    pending_events[m.from_user.id] = text
    rows = []
    row = []
    for day in range(1, 32):
        row.append(Btn(text=str(day), callback_data=f"evday:{day}"))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Btn(text="❌ Отмена", callback_data="evday:cancel")])
    await m.answer("Выбери день месяца:", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("evday:"))
async def handle_event_day(c: CallbackQuery):
    if not is_owner(c.from_user.id if c.from_user else None):
        await c.answer("Нет доступа", show_alert=True)
        return
    user_id = c.from_user.id
    if user_id not in pending_events or not pending_events[user_id]:
        await c.answer("Сессия истекла.", show_alert=True)
        return
    if c.data == "evday:cancel":
        pending_events.pop(user_id, None)
        await c.message.edit_text("Отменено.")
        await c.answer()
        return
    day = int(c.data.split(":")[1])
    text = pending_events.pop(user_id)
    d = load()
    d.setdefault("events", []).append({"day": day, "text": text, "created": get_moscow_now().isoformat()})
    save(d)
    await c.message.edit_text(f"✅ Событие на {day} число:\n{escape(text)}", parse_mode=ParseMode.HTML)
    await c.answer()


@router.message(F.document)
async def handle_document(m: Message):
    if not is_owner(m.from_user.id if m.from_user else None):
        return
    if not m.document:
        return
    file_id = m.document.file_id
    file_name = m.document.file_name or "document.pdf"
    pending_uploads[m.from_user.id] = {"file_id": file_id, "file_name": file_name}
    d = load()
    subjects = d.get("subjects", [])
    if not subjects:
        await m.answer("Нет предметов.")
        return
    rows = [[Btn(text=s["name"], callback_data=f"addsel:s:{i}")] for i, s in enumerate(subjects)]
    rows.append([Btn(text="❌ Отмена", callback_data="addsel:cancel")])
    await m.answer(f"📎 Файл <b>{escape(file_name)}</b> получен.\nВыбери предмет:", reply_markup=kb(rows), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("addsel:"))
async def handle_add_selection(c: CallbackQuery):
    if not is_owner(c.from_user.id if c.from_user else None):
        await c.answer("Нет доступа", show_alert=True)
        return
    user_id = c.from_user.id
    if user_id not in pending_uploads:
        await c.answer("Сессия истекла.", show_alert=True)
        return
    parts = c.data.split(":")
    if parts[1] == "cancel":
        pending_uploads.pop(user_id, None)
        await c.message.edit_text("Отменено.")
        await c.answer()
        return
    if parts[1] == "s":
        si = int(parts[2])
        pending_uploads[user_id]["si"] = si
        d = load()
        subject = d["subjects"][si]
        sections = subject.get("sections", [])
        rows = [[Btn(text=sec["title"], callback_data=f"addsel:sec:{si}:{j}")] for j, sec in enumerate(sections)]
        rows.append([Btn(text="❌ Отмена", callback_data="addsel:cancel")])
        await c.message.edit_text(f"Предмет: <b>{escape(subject['name'])}</b>\nВыбери раздел:", reply_markup=kb(rows), parse_mode=ParseMode.HTML)
        await c.answer()
        return
    if parts[1] == "sec":
        si = int(parts[2])
        j = int(parts[3])
        info = pending_uploads.pop(user_id)
        d = load()
        try:
            section = d["subjects"][si]["sections"][j]
            section.setdefault("files", []).append({"file_id": info["file_id"], "name": info["file_name"]})
            save(d)
            await c.message.edit_text(f"✅ Файл добавлен: <b>{escape(info['file_name'])}</b>", parse_mode=ParseMode.HTML)
        except Exception:
            await c.message.edit_text("Ошибка.")
        await c.answer()
        return
    await c.answer()


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(m: Message):
    try:
        await m.delete()
    except TelegramBadRequest:
        pass
    await screen_home(m)


@router.callback_query()
async def on_callback(c: CallbackQuery):
    if not c.data or c.data.startswith(("addsel:", "evday:", "hwsel:", "hwday:", "hwdone:", "hwview:")) or c.data in ("addevent", "addhw"):
        return
    parts = c.data.split(":")
    try:
        match parts:
            case ["home"]:
                await screen_home(c)
            case ["subs"]:
                await screen_subjects(c)
            case ["s", si]:
                await screen_subject(c, int(si))
            case ["s", si, j]:
                await screen_section(c, int(si), int(j))
            case ["p", pi]:
                await screen_page(c, int(pi))
            case ["sched", "today"]:
                await screen_schedule(c, 0)
            case ["sched", "tomorrow"]:
                await screen_schedule(c, 1)
            case ["file", si, j, fi]:
                try:
                    files = load()["subjects"][int(si)]["sections"][int(j)].get("files", [])
                    f = files[int(fi)]
                    fid = f["file_id"] if isinstance(f, dict) else f
                    await c.message.answer_document(fid)
                    await c.answer()
                except Exception:
                    await c.answer("Не удалось отправить", show_alert=True)
            case _:
                await c.answer("Меню устарело", show_alert=True)
                await screen_home(c)
    except Exception:
        await c.answer("Ошибка", show_alert=True)
        await screen_home(c)


async def main():
    logging.basicConfig(level=logging.INFO)
    if not TOKEN:
        raise SystemExit("Задай BOT_TOKEN")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
