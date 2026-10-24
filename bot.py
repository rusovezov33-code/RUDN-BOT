import asyncio
import json
import logging
import os
from html import escape
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
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
DATA_FILE = Path(__file__).with_name("data.json")

router = Router()


def load() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"pages": [], "subjects": []}


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
    if s.get("teacher"):
        lines.append(f"👤 {escape(s['teacher'])}")
    if s.get("semester"):
        lines.append(f"🗓 Семестр: {escape(str(s['semester']))}")
    if s.get("contacts"):
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
    text = (
        f"<b>{escape(p.get('title', ''))}</b>\n\n"
        + (escape(p.get("text") or "") if p.get("text") else "Пока ничего нет.")
    )
    rows = link_rows(p.get("links", []))
    rows.append(nav())
    await show(event, text, kb(rows))


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
    if not c.data:
        await c.answer()
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
                for fid in files:
                    try:
                        await c.message.answer_document(fid)
                    except Exception:
                        await c.message.answer(f"Не удалось отправить файл: {fid}")
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
