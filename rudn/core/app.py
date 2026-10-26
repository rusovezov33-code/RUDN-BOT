"""Сборка бота: реестр фич → роутеры aiogram → запуск (polling + HTTP)."""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    CallbackQuery,
    Message,
)

from . import config, home, pending, timeutil
from .registry import REGISTRY, CommandSpec, Registry
from .storage import store
from .ui import Ctx

log = logging.getLogger(__name__)


def build_registry(feature_modules: list[str] | None = None, registry: Registry = REGISTRY) -> Registry:
    """Подключает home + фичи из rudn.features.FEATURES. Ошибки — сразу и громко."""
    if registry.features:
        return registry
    if feature_modules is None:
        from rudn.features import FEATURES

        feature_modules = FEATURES
    registry.add(home.feature)
    for name in feature_modules:
        module = importlib.import_module(f"rudn.features.{name}")
        registry.add(module.feature)
    registry.validate()
    return registry


def _command_handler(spec: CommandSpec):
    async def handler(message: Message) -> None:
        ctx = Ctx.from_message(message)
        await ctx.delete_incoming()
        try:
            await spec.handler(ctx)
        except Exception:
            log.exception("command /%s failed", spec.name)
            await home.screen_home(ctx)

    handler.__name__ = f"cmd_{spec.name}"
    return handler


async def on_unknown_command(message: Message) -> None:
    ctx = Ctx.from_message(message)
    await ctx.delete_incoming()
    await home.screen_home(ctx)


async def on_text(message: Message) -> None:
    ctx = Ctx.from_message(message)
    expired = pending.is_expired(ctx.user_id)
    state = ctx.pending()
    if state is not None and ctx.is_owner:
        found = REGISTRY.text_states.get(state.name)
        if found:
            ctx.edit_live = True
            await ctx.delete_incoming()
            try:
                await found[1](ctx, (message.text or "").strip())
            except Exception:
                log.exception("text state %s failed", state.name)
                ctx.clear_pending()
                await home.screen_home(ctx)
            return
    if expired:
        await message.answer("⌛ Ввод устарел (прошло больше 15 минут). Начни заново из меню.")
    await home.screen_home(ctx)


async def on_callback(callback: CallbackQuery) -> None:
    raw = callback.data or ""
    main, _, back = raw.partition("|")
    parts = main.split(":")
    ctx = Ctx.from_callback(callback, args=parts[1:], back=back or None)
    state = ctx.pending()
    if state is not None and parts[0] not in state.data.get("keep", ()):
        ctx.clear_pending()
    handler = REGISTRY.handler(parts[0])
    if handler is None:
        await ctx.answer("Меню обновилось")
        await home.screen_home(ctx)
        return
    try:
        await handler(ctx, ctx.args)
    except TelegramBadRequest as error:
        if "not modified" not in str(error).lower():
            log.exception("callback %r failed", raw)
            await ctx.answer("Не получилось, открыл меню", alert=True)
            await _safe_home(ctx)
    except Exception:
        log.exception("callback %r failed", raw)
        await ctx.answer("Ошибка, открыл меню", alert=True)
        await _safe_home(ctx)
    finally:
        await ctx.answer()


async def _safe_home(ctx: Ctx) -> None:
    try:
        await home.screen_home(ctx)
    except Exception:
        log.exception("home failed")


def build_dispatcher(registry: Registry | None = None) -> Dispatcher:
    registry = registry or build_registry()
    dispatcher = Dispatcher()
    for feature in registry.features:
        if feature.router is not None:
            dispatcher.include_router(feature.router)
    core = Router(name="core")
    for _, spec in registry.commands.values():
        core.message.register(_command_handler(spec), Command(spec.name))
    core.message.register(on_unknown_command, F.text.startswith("/"))
    core.message.register(on_text, F.text)
    core.callback_query.register(on_callback)
    dispatcher.include_router(core)
    return dispatcher


async def set_commands(bot: Bot, registry: Registry = REGISTRY) -> None:
    commands = [BotCommand(command=spec.name, description=spec.description) for spec in registry.visible_commands()]
    stale: list[dict[str, Any]] = [
        {"scope": BotCommandScopeAllPrivateChats()},
        {"scope": BotCommandScopeDefault(), "language_code": "ru"},
        {"scope": BotCommandScopeDefault(), "language_code": "en"},
    ]
    owner = config.owner_id()
    if owner:
        stale.append({"scope": BotCommandScopeChat(chat_id=owner)})
    for kwargs in stale:
        try:
            await bot.delete_my_commands(**kwargs)
        except TelegramAPIError:
            pass
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    except TelegramAPIError:
        log.exception("set_my_commands failed")


async def main() -> None:
    from aiohttp import ClientSession, ClientTimeout, web

    from .web import build_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    token = config.token()
    if not token:
        raise SystemExit("Задай BOT_TOKEN")
    data = store.load()
    registry = build_registry()
    files = sum(len(sec.get("files") or []) for s in data["subjects"] for sec in s.get("sections") or [])
    log.info(
        "data=%s subjects=%s files=%s tasks=%s week=%s features=%s",
        store.path,
        len(data["subjects"]),
        files,
        len(data["homework"]),
        timeutil.week_label(timeutil.week_type(timeutil.today())),
        ",".join(f.name for f in registry.features),
    )
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = build_dispatcher(registry)
    await set_commands(bot, registry)
    http = ClientSession(timeout=ClientTimeout(total=120))
    runner = web.AppRunner(build_app(bot, http))
    await runner.setup()
    port = int(os.getenv("PORT") or "8080")
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("listening on %s", port)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await http.close()
        await runner.cleanup()
        await bot.session.close()
