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


def load() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"pages": [], "subjects": []}


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
    if sec.get("files"):
        rows.append([Btn(text=f"📎 Получить файлы ({len(sec['files'])})", callback_data=f"f:{si}:{j}")])
    rows.append(nav(f"s:{si}"))
    await show(event, text, kb(rows))


async def screen_page(event, pi: int):
    d = load()
    try:
        p = d["pages"][pi]
    except (IndexError, KeyError):
        await show(event, "Страница не найдена.", kb([nav()]))
        return
    if "Расписание" in p.get("title", ""):
        await screen_schedule(event)
        return
    text = (
        f"<b>{escape(p.get('title', ''))}</b>\n\n"
        + (escape(p.get("text") or "") if p.get("text") else "Пока ничего нет.")
    )
    rows = link_rows(p.get("links", []))
    rows.append(nav())
    await show(event, text, kb(rows))


async def screen_schedule(event):
    now = datetime.now()
    moscow_now = now + timedelta(hours=3)
    weekday = moscow_now.weekday()
    day_code = DAY_MAP.get(weekday, "ПН")
    week_type = get_current_week_type()
    week_label = "нижняя" if week_type == "lower" else "верхняя"
    lessons = SCHEDULE.get(day_code, [])
    filtered = [l for l in lessons if l[3] in ("both", week_type)]
    lines = [f"📅 <b>Расписание на сегодня ({day_code}, {week_label} неделя)</b>\n"]
    if not filtered:
        lines.append("Сегодня занятий нет или выходной.")
    else:
        for time, subject, place, _ in filtered:
            lines.append(f"• {time} — {subject}")
            if place:
                lines.append(f"  📍 {place}")
    rows = [nav()]
    await show(event, "\n".join(lines), kb(rows))


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
    await m.answer(
        f"📎 Файл <b>{escape(file_name)}</b> получен.\nВыбери предмет:",
        reply_markup=kb(rows),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("addsel:"))
async def handle_add_selection(c: CallbackQuery):
    if not is_owner(c.from_user.id if c.from_user else None):
        await c.answer("Нет доступа", show_alert=True)
        return
    user_id = c.from_user.id
    if user_id not in pending_uploads:
        await c.answer("Сессия истекла. Отправь файл заново.", show_alert=True)
        return
    parts = c.data.split(":")
    if parts[1] == "cancel":
        pending_uploads.pop(user_id, None)
        await c.message.edit_text("Добавление отменено.")
        await c.answer()
        return
    if parts[1] == "s":
        si = int(parts[2])
        d = load()
        try:
            subject = d["subjects"][si]
        except (IndexError, KeyError):
            await c.answer("Предмет не найден", show_alert=True)
            return
        pending_uploads[user_id]["si"] = si
        sections = subject.get("sections", [])
        rows = [[Btn(text=sec["title"], callback_data=f"addsel:sec:{si}:{j}")] for j, sec in enumerate(sections)]
        rows.append([Btn(text="❌ Отмена", callback_data="addsel:cancel")])
        await c.message.edit_text(
            f"Предмет: <b>{escape(subject['name'])}</b>\nВыбери раздел:",
            reply_markup=kb(rows),
            parse_mode=ParseMode.HTML,
        )
        await c.answer()
        return
    if parts[1] == "sec":
        si = int(parts[2])
        j = int(parts[3])
        info = pending_uploads.pop(user_id)
        d = load()
        try:
            section = d["subjects"][si]["sections"][j]
            if "files" not in section:
                section["files"] = []
            section["files"].append({"file_id": info["file_id"], "name": info["file_name"]})
            save(d)
            await c.message.edit_text(
                f"✅ Файл <b>{escape(info['file_name'])}</b> добавлен.",
                parse_mode=ParseMode.HTML,
            )
        except (IndexError, KeyError):
            await c.message.edit_text("Ошибка при сохранении.")
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
    if not c.data or c.data.startswith("addsel:"):
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
            case ["f", si, j]:
                files = load()["subjects"][int(si)]["sections"][int(j)].get("files", [])
                if not files:
                    await c.answer("Файлов нет", show_alert=True)
                    return
                for f in files:
                    fid = f["file_id"] if isinstance(f, dict) else f
                    try:
                        await c.message.answer_document(fid)
                    except Exception:
                        await c.message.answer("Не удалось отправить файл")
                await c.answer("Отправлено")
            case _:
                await c.answer("Меню устарело, открываю заново", show_alert=True)
                await screen_home(c)
    except (IndexError, KeyError, ValueError):
        await c.answer("Меню устарело, открываю заново", show_alert=True)
        await screen_home(c)


async def main():
    logging.basicConfig(level=logging.INFO)
    if not TOKEN:
        raise SystemExit("Задай BOT_TOKEN в переменных окружения")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
