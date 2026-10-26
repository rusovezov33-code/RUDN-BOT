"""Поддельные bot/message/callback — без сети и без токена."""
import itertools
from types import SimpleNamespace

from aiogram.types import InlineKeyboardMarkup

_ids = itertools.count(1000)


class FakeBot:
    def __init__(self):
        self.calls = []
        self.messages = {}

    def _record(self, kind, text=None, markup=None, **kw):
        self.calls.append(SimpleNamespace(kind=kind, text=text, markup=markup, kw=kw))

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        mid = next(_ids)
        self.messages[mid] = (text, reply_markup)
        self._record("send", text, reply_markup)
        return SimpleNamespace(message_id=mid)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None, **kw):
        self.messages[message_id] = (text, reply_markup)
        self._record("edit", text, reply_markup, message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.messages.pop(message_id, None)
        self._record("delete", message_id=message_id)

    async def send_document(self, chat_id, document, **kw):
        self._record("document", document=document)

    def last_screen(self):
        for call in reversed(self.calls):
            if call.kind in ("send", "edit"):
                return call
        return None


def buttons(markup):
    if not isinstance(markup, InlineKeyboardMarkup):
        return []
    return [btn for row in markup.inline_keyboard for btn in row]


def callbacks(markup):
    return [btn.callback_data for btn in buttons(markup) if btn.callback_data]


class FakeCallback:
    def __init__(self, bot, data, user_id=111, message_id=None):
        self.bot = bot
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=user_id), message_id=message_id or next(_ids))
        self.answers = []

    async def answer(self, text=None, show_alert=False, **kw):
        self.answers.append((text, show_alert))


class FakeMessage:
    def __init__(self, bot, text, user_id=111):
        self.bot = bot
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=user_id)
        self.message_id = next(_ids)
        self.document = None
        self.photo = None
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append(text)
