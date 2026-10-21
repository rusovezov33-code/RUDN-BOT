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
