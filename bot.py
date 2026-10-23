import asyncio
import json
import logging
import os
from html import escape
from pathlib import Path

from aiogram import Bot, BaseMiddleware, Dispatcher, Router
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
ALLOWED = {int(x) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip()}
DATA_FILE = Path(__file__).with_name("data.json")

router = Router()


def load() -> dict:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if ALLOWED and (not user or user.id not in ALLOWED):
            if isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return
        return await handler(event, data)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def nav(back=None):
    row = []
    if back and back != "home":
        row.append(Btn(text="⬅️ Назад", callback_data=back))
    row.append(Btn(text="🏠 Меню", callback_data="home"))
    return row


def link_rows(links):
    return [[Btn(text=f"🔗 {l['title']}", url=l["url"])] for l in links]

async def show(event, text, markup):
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                raise
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


async def screen_home(event):
    d = load()
    rows = [[Btn(text="📚 Предметы", callback_data="subs")]]
    pages = [Btn(text=p["title"], callback_data=f"p:{i}") for i, p in enumerate(d["pages"])]
    rows += [pages[i : i + 2] for i in range(0, len(pages), 2)]
    await show(event, "🎓 <b>РУДН: учебное пространство</b>\nВыбери раздел:", kb(rows))


async def screen_subjects(event):
    d = load()
    rows = [[Btn(text=s["name"], callback_data=f"s:{i}")] for i, s in enumerate(d["subjects"])]
    rows.append(nav())
    text = "📚 <b>Предметы</b>" if d["subjects"] else "📚 <b>Предметы</b>\n\nПока ничего нет."
    await show(event, text, kb(rows))


async def screen_subject(event, si):
    s = load()["subjects"][si]
    lines = [f"<b>{escape(s['name'])}</b>"]
    if s.get("teacher"):
        lines.append(f"👤 {escape(s['teacher'])}")
    if s.get("semester"):
        lines.append(f"🗓 Семестр: {escape(str(s['semester']))}")
    if s.get("contacts"):
        lines.append(f"✉️ {escape(s['contacts'])}")
    rows = [[Btn(text=sec["title"], callback_data=f"s:{si}:{j}")] for j, sec in enumerate(s["sections"])]
    rows.append(nav("subs"))
    await show(event, "\n".join(lines), kb(rows))

async def screen_section(event, si, j):
    s = load()["subjects"][si]
    sec = s["sections"][j]
    body = escape(sec["text"]) if sec.get("text") else ""
    has_any = body or sec.get("links") or sec.get("files")
    text = f"<b>{escape(s['name'])}</b>\n{escape(sec['title'])}\n\n" + (body if has_any else "Пока ничего нет.")
    rows = link_rows(sec.get("links", []))
    if sec.get("files"):
        rows.append([Btn(text=f"📎 Получить файлы ({len(sec['files'])})", callback_data=f"f:{si}:{j}")])
    rows.append(nav(f"s:{si}"))
    await show(event, text, kb(rows))


async def screen_page(event, pi):
    p = load()["pages"][pi]
    text = f"<b>{escape(p['title'])}</b>\n\n" + (escape(p["text"]) if p.get("text") else "Пока ничего нет.")
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
                for fid in files:
                    await c.message.answer_document(fid)
                await c.answer("Отправлено")
            case _:
                await c.answer()
    except (IndexError, KeyError, ValueError):
        await c.answer("Меню устарело, открываю заново")
        await screen_home(c)


async def main():
    logging.basicConfig(level=logging.INFO)
    if not TOKEN:
        raise SystemExit("Задай BOT_TOKEN в переменных окружения")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.update.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
