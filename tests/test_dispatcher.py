"""Смоук-тест диспетчера aiogram с фейковым токеном и подменённой сессией —
без сети. Проверяем, что апдейты доходят до наших обработчиков."""
import asyncio
from datetime import datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Message, Update

from conftest import LIVE_BACKUP
from rudn.core import app


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def make_request(self, bot, method: TelegramMethod, timeout=None):
        self.requests.append(method)
        name = type(method).__name__
        if name in ("SendMessage",):
            return Message.model_validate(
                {"message_id": 777, "date": int(datetime.now().timestamp()), "chat": {"id": method.chat_id, "type": "private"}, "text": method.text}
            ).as_(bot)
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""


def test_dispatcher_feed_update(use_data):
    use_data(LIVE_BACKUP)
    session = FakeSession()
    bot = Bot("123456:ABCDEFabcdef-fake", session=session)
    dispatcher = app.build_dispatcher()

    async def go():
        await app.set_commands(bot)
        update = Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": 1,
                    "chat": {"id": 111, "type": "private"},
                    "from": {"id": 111, "is_bot": False, "first_name": "O"},
                    "text": "/start",
                    "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
                },
            }
        )
        await dispatcher.feed_update(bot, update)
        cb = Update.model_validate(
            {
                "update_id": 2,
                "callback_query": {
                    "id": "q1",
                    "chat_instance": "c",
                    "from": {"id": 111, "is_bot": False, "first_name": "O"},
                    "data": "dl",
                    "message": {"message_id": 777, "date": 1, "chat": {"id": 111, "type": "private"}, "text": "menu"},
                },
            }
        )
        await dispatcher.feed_update(bot, cb)

    asyncio.run(go())
    names = [type(m).__name__ for m in session.requests]
    assert "SetMyCommands" in names
    cmds = next(m for m in session.requests if type(m).__name__ == "SetMyCommands").commands
    assert [c.command for c in cmds] == ["start", "today", "tasks", "add"]
    assert "DeleteMessage" in names and "SendMessage" in names
    assert "AnswerCallbackQuery" in names and "EditMessageText" in names
    assert names.index("AnswerCallbackQuery") < names.index("EditMessageText")
