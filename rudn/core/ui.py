"""UI-помощники: кнопки с проверкой, навигация, безопасная обрезка HTML,
контекст экрана Ctx и «живое» меню (одно сообщение-меню на чат)."""
from __future__ import annotations

import asyncio
import logging
import re
from html import escape, unescape
from typing import Any, Iterable

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from . import config, pending
from .registry import REGISTRY, RegistryError
from .storage import Store, store as default_store

log = logging.getLogger(__name__)
TEXT_LIMIT = 3900
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
HOME = "home"

# chat_id -> message_id последнего сообщения-меню
LIVE_MENU: dict[int, int] = {}


def b(text: str, data: str) -> InlineKeyboardButton:
    """Кнопка с callback_data. Проверяет длину (≤64 байт) и наличие обработчика."""
    try:
        if REGISTRY.sealed or config.strict():
            REGISTRY.check_callback(data)
        elif len(data.encode()) > 64:
            raise RegistryError(f"callback_data длиннее 64 байт: {data!r}")
    except RegistryError:
        if config.strict():
            raise
        log.exception("битая кнопка %r", text)
        data = HOME
    return InlineKeyboardButton(text=text, callback_data=data)


def is_url(url: object) -> bool:
    """Telegram отклоняет всё сообщение, если у кнопки кривой URL — такие ссылки прячем."""
    return isinstance(url, str) and " " not in url.strip() and url.strip().lower().startswith(("http://", "https://", "tg://"))


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def with_back(data: str, back: str | None) -> str:
    """Добавляет «куда вернуться» (`|back`) к callback_data, если влезает в 64 байта."""
    if not back or back == HOME:
        return data
    back = back.split("|", 1)[0]
    full = f"{data}|{back}"
    return full if len(full.encode()) <= 64 else data


def nav(back: str | None = None) -> list[InlineKeyboardButton]:
    return [b("⬅ Назад", back or HOME), b("🏠 Меню", HOME)]


def chunk(buttons: Iterable[InlineKeyboardButton], size: int) -> list[list[InlineKeyboardButton]]:
    items = list(buttons)
    return [items[i : i + size] for i in range(0, len(items), size)]


def kb(rows: Iterable[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


def short(text: str, limit: int) -> str:
    """Обрезка до limit символов по границе слова (если граница не слишком далеко)."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit * 0.5:
        cut = cut[:space]
    return cut.rstrip(" ,.;:—–-(") + "…"


_TAG = re.compile(r"<[^>]+>")


def clip_html(text: str, limit: int = TEXT_LIMIT) -> str:
    """Обрезает по строкам, чтобы не разрезать HTML-тег (каждая строка сама по себе
    сбалансирована). Слишком длинная строка обрезается как обычный текст."""
    if len(text) <= limit:
        return text
    out: list[str] = []
    size = 0
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if size + len(line) + 1 > limit - 40:
            if not out:
                # убираем теги, раскрываем сущности и режем уже чистый текст,
                # чтобы не разрезать «&amp;» пополам и не экранировать дважды
                plain = unescape(_TAG.sub("", line))
                budget = limit - 40
                cut = plain[:budget]
                while cut and len(escape(cut)) > budget:
                    cut = cut[:-1]
                out.append(escape(cut))
            rest = len(lines) - index
            out.append(f"… (ещё {rest} строк)")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


class Ctx:
    """Контекст одного действия пользователя (нажатие кнопки, команда или текст)."""

    def __init__(
        self,
        bot: Any,
        user_id: int | None,
        chat_id: int,
        *,
        callback: Any = None,
        message: Any = None,
        args: list[str] | None = None,
        back: str | None = None,
        store: Store | None = None,
        edit_live: bool = False,
    ) -> None:
        self.bot = bot
        self.user_id = user_id
        self.chat_id = chat_id
        self.callback = callback
        self.message = message
        self.args = args or []
        self.back = back
        self.store = store or default_store
        self.edit_live = edit_live
        self._answered = False
        # всплывающее уведомление к ответу на нажатие: уходит вместе с показом
        # экрана (параллельно правке), а не отдельным запросом перед ним
        self.toast: str | None = None
        # True — в обработчике нажатия/текста: ctx.save() откладывается до flush()
        # после показа экрана (fsync data.json не задерживает ответ)
        self.defer_save = False
        self._dirty = False
        # короткая строка-уведомление над следующим экраном (напр. «Ввод отменён»)
        self.notice: str | None = None

    @classmethod
    def from_message(cls, message: Any, **kw: Any) -> "Ctx":
        user = getattr(message, "from_user", None)
        return cls(message.bot, user.id if user else None, message.chat.id, message=message, **kw)

    @classmethod
    def from_callback(cls, callback: Any, **kw: Any) -> "Ctx":
        msg = callback.message
        chat_id = msg.chat.id if msg is not None else callback.from_user.id
        return cls(callback.bot, callback.from_user.id, chat_id, callback=callback, **kw)

    # --- данные / права
    @property
    def data(self) -> dict:
        return self.store.data

    def save(self) -> None:
        if self.defer_save:
            self._dirty = True
            return
        self.store.save()

    async def flush(self) -> None:
        """Записать отложенные изменения (после того как экран уже показан)."""
        if not self._dirty:
            return
        self._dirty = False
        save_async = getattr(self.store, "save_async", None)
        if save_async is not None:
            await save_async()
        else:
            self.store.save()

    @property
    def is_owner(self) -> bool:
        owner = config.owner_id()
        return owner is not None and self.user_id == owner

    async def require_owner(self) -> bool:
        if self.is_owner:
            return True
        await self.answer("Нет доступа", alert=True)
        return False

    # --- ответы
    async def answer(self, text: str | None = None, alert: bool = False) -> None:
        """Ответ на нажатие (убирает «часики»). Повторные вызовы игнорируются."""
        if self.callback is None:
            if text and alert:
                await self.bot.send_message(self.chat_id, text)
            return
        if self._answered:
            return
        self._answered = True
        if text is None:
            text, self.toast = self.toast, None
        try:
            await self.callback.answer(text=text, show_alert=alert)
        except TelegramAPIError:
            pass  # запрос устарел — не страшно

    async def show(self, text: str, rows: Iterable[list[InlineKeyboardButton]], *, new: bool = False) -> None:
        """Показать экран: правим текущее сообщение-меню, иначе отправляем новое,
        а старое меню удаляем — в чате всегда одно живое меню."""
        if self.notice:
            text = f"{self.notice}\n\n{text}"
            self.notice = None
        text = clip_html(text)
        markup = kb(rows)
        # ответ на нажатие и правку сообщения шлём параллельно: на медленной сети
        # это экономит целый запрос к Telegram (особенно заметно на «⬅ Назад»)
        answering = None
        if self.callback is not None and not self._answered:
            answering = asyncio.ensure_future(self.answer())
            await asyncio.sleep(0)  # пусть ответ уйдёт первым, правка — следом, не дожидаясь
        try:
            await self._show(text, markup, new)
        finally:
            if answering is not None:
                await answering

    async def _show(self, text: str, markup: Any, new: bool) -> None:
        target = None
        if not new:
            if self.callback is not None and self.callback.message is not None:
                target = self.callback.message.message_id
            elif self.edit_live:
                target = LIVE_MENU.get(self.chat_id)
        if target is not None:
            try:
                await self.bot.edit_message_text(
                    text=text,
                    chat_id=self.chat_id,
                    message_id=target,
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML,
                    link_preview_options=NO_PREVIEW,
                )
                await self._set_live(target)
                return
            except TelegramBadRequest as error:
                if "not modified" in str(error).lower():
                    await self._set_live(target)
                    return
                log.warning("edit of %s failed, sending new: %s", target, error)
        sent = await self.bot.send_message(
            self.chat_id, text, reply_markup=markup, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
        )
        await self._set_live(sent.message_id)

    async def _set_live(self, message_id: int) -> None:
        old = LIVE_MENU.get(self.chat_id)
        LIVE_MENU[self.chat_id] = message_id
        if old is not None and old != message_id:
            await self.delete(old)

    async def delete(self, message_id: int) -> None:
        try:
            await self.bot.delete_message(self.chat_id, message_id)
        except TelegramAPIError:
            pass

    async def delete_incoming(self) -> None:
        """Удалить сообщение пользователя (команду/введённый текст), чтобы не копился мусор."""
        if self.message is not None:
            await self.delete(self.message.message_id)

    # --- ожидание ввода
    def wait_text(self, state: str, **data: Any) -> None:
        if self.user_id is not None:
            pending.start(self.user_id, state, **data)

    def pending(self) -> pending.State | None:
        return pending.get(self.user_id)

    def clear_pending(self) -> None:
        pending.clear(self.user_id)


def cancel_row(data: str = "x") -> list[InlineKeyboardButton]:
    return [b("❌ Отмена", data)]


def maybe(text: str, data: str) -> InlineKeyboardButton | None:
    """Кнопка на другую фичу — только если та фича подключена (иначе None)."""
    from .registry import prefix_of

    return b(text, data) if REGISTRY.handler(prefix_of(data)) else None


def row_of(*buttons: InlineKeyboardButton | None) -> list[InlineKeyboardButton]:
    return [button for button in buttons if button is not None]
