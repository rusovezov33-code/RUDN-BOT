"""Запрос доступа от чужих и экран владельца «👥 Тестеры» (/testers)."""
from __future__ import annotations

import logging
from html import escape
from typing import Any

from aiogram.exceptions import TelegramAPIError

from . import access, config, home
from .registry import CommandSpec, Feature
from aiogram.enums import ParseMode

from .ui import Ctx, b, kb, nav

log = logging.getLogger(__name__)

APPROVE = "✅ Пустить как тестера"
DENY = "❌ Отказать"
REVOKE = "🚫 Отключить"
WELCOME = (
    "✅ <b>Доступ открыт!</b> Режим тестера: можно смотреть всё — расписание, предметы, "
    "книги и файлы, дедлайны, баллы. Уроки и тесты проходи сколько хочешь — прогресс свой. "
    "Изменять данные нельзя."
)
TESTER_BANNER = "👀 <i>Режим тестера: только просмотр</i>"


def _who(info: dict, user_id: Any) -> str:
    name = escape(str(info.get("name") or "без имени"))
    username = info.get("username")
    at = f" (@{escape(str(username))})" if username else ""
    return f"{name}{at} · id <code>{user_id}</code>"


def request_text(info: dict, user_id: int) -> str:
    username = info.get("username")
    return "\n".join(
        [
            "🔔 <b>Запрос на доступ к боту</b>",
            f"Имя: <b>{escape(str(info.get('name') or 'без имени'))}</b>",
            f"Username: {'@' + escape(str(username)) if username else 'нет'}",
            f"ID: <code>{user_id}</code>",
            "",
            "Тестер сможет только смотреть (книги, файлы, дедлайны, баллы, уроки — со своим прогрессом). "
            "Изменить твои данные он не сможет.",
        ]
    )


def request_rows(user_id: int) -> list[list]:
    return [[b(APPROVE, f"acc:ok:{user_id}"), b(DENY, f"acc:no:{user_id}")]]


async def stranger_start(ctx: Ctx, user: Any) -> None:
    """/start от незнакомца: один запрос владельцу, на спам — тишина."""
    owner = config.owner_id()
    if owner is None or user is None:
        return
    verdict = access.open_request(user)
    if verdict == "quiet":
        return
    if verdict == "repeat":
        await ctx.bot.send_message(ctx.chat_id, "⏳ Запрос уже отправлен владельцу — дождись ответа.")
        return
    info = access.user_info(user)
    try:
        await ctx.bot.send_message(owner, request_text(info, user.id), reply_markup=kb(request_rows(user.id)), parse_mode=ParseMode.HTML)
    except TelegramAPIError:
        log.exception("не удалось отправить запрос владельцу")
    await ctx.bot.send_message(ctx.chat_id, REQUEST_SENT_TEXT)


REQUEST_SENT_TEXT = f"{access.REQUEST_SENT}. Как только он подтвердит — бот откроется."


async def _edit_here(ctx: Ctx, text: str, rows: list[list] | None = None) -> None:
    """Правим само сообщение-запрос, не трогая живое меню владельца."""
    msg = getattr(ctx.callback, "message", None)
    if msg is None:
        return
    try:
        await ctx.bot.edit_message_text(
            text=text, chat_id=ctx.chat_id, message_id=msg.message_id, reply_markup=kb(rows or []),
            parse_mode=ParseMode.HTML,
        )
    except TelegramAPIError:
        pass


async def screen_testers(ctx: Ctx) -> None:
    data = access.ACCESS.data
    ids, names = access.preallow()
    lines = ["👥 <b>Тестеры</b> — видят всё, но ничего не могут изменить; уроки у них свои.", ""]
    rows: list[list] = []
    testers = data["testers"]
    if testers:
        for uid, info in testers.items():
            lines.append(f"• {_who(info, uid)}")
            rows.append([b(f"{REVOKE} {str(info.get('name') or uid)[:24]}", f"acc:rv:{uid}:l")])
    else:
        lines.append("Тестеров пока нет.")
    pre = [str(x) for x in sorted(ids) if str(x) not in testers and str(x) not in data["revoked"]] + [
        "@" + n for n in sorted(names)
    ]
    if pre:
        lines.append("")
        lines.append("Из config/testers.json: " + escape(", ".join(pre)))
    requests = data["requests"]
    if requests:
        lines += ["", "🔔 <b>Ждут ответа:</b>"]
        for uid, info in requests.items():
            lines.append(f"• {_who(info, uid)}")
            rows.append([b(f"✅ {str(info.get('name') or uid)[:20]}", f"acc:ok:{uid}:l"), b("❌", f"acc:no:{uid}:l")])
    lines += ["", "Новый человек: пусть напишет боту /start — сюда придёт запрос."]
    rows.append(nav())
    await ctx.show("\n".join(lines), rows)


async def _greet_tester(ctx: Ctx, user_id: int) -> None:
    tester = Ctx(ctx.bot, user_id, user_id)
    try:
        await home.screen_home(tester, notice=WELCOME)
    except TelegramAPIError:
        log.exception("не удалось написать тестеру %s", user_id)


async def on_access(ctx: Ctx, args: list[str]) -> None:
    if not await ctx.require_owner():
        return
    action = args[0] if args else "l"
    try:
        user_id = int(args[1]) if len(args) > 1 else None
    except ValueError:
        user_id = None
    if action in ("ok", "no", "rv") and (user_id is None or user_id == config.owner_id()):
        await ctx.answer("Не найдено", alert=True)
        return
    from_list = len(args) > 2 and args[2] == "l"
    if action == "ok":
        data = access.ACCESS.data
        if str(user_id) not in data["requests"] and str(user_id) not in data["testers"]:
            await ctx.answer("Запрос уже обработан", alert=True)
            return
        already = str(user_id) in data["testers"]
        info = access.approve(user_id)
        ctx.toast = "✅ Пустил как тестера"
        if from_list:
            await screen_testers(ctx)
        else:
            await _edit_here(ctx, f"✅ Тестер: {_who(info, user_id)}\nТолько просмотр. Отключить — /testers.")
        if not already:
            await _greet_tester(ctx, user_id)
    elif action == "no":
        data = access.ACCESS.data
        if str(user_id) not in data["requests"]:
            await ctx.answer("Запрос уже обработан", alert=True)
            return
        info = access.deny(user_id)
        ctx.toast = "❌ Отказано"
        if from_list:
            await screen_testers(ctx)
        else:
            await _edit_here(ctx, f"❌ Отказано: {_who(info, user_id)}")
    elif action == "rv":
        access.revoke(user_id)
        from . import pending

        pending.clear(user_id)
        ctx.toast = "🚫 Доступ отключён"
        await screen_testers(ctx)
    else:
        await screen_testers(ctx)


async def cmd_testers(ctx: Ctx) -> None:
    if await ctx.require_owner():
        await screen_testers(ctx)


feature = Feature(
    name="access",
    callbacks={"acc": on_access},
    commands=[CommandSpec("testers", "Тестеры (только для владельца)", cmd_testers, hidden=True)],
)
