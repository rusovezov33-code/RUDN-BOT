"""Сборка бота: реестр фич → роутеры aiogram → запуск (polling + HTTP)."""
from __future__ import annotations

import importlib
import logging
import os
import time
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

from . import access, config, home, pending, testers, timeutil
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
    registry.add(testers.feature)
    for name in feature_modules:
        module = importlib.import_module(f"rudn.features.{name}")
        registry.add(module.feature)
    registry.validate()
    return registry


def run_startup_hooks(registry: Registry = REGISTRY, target: Any = None) -> None:
    """Хуки запуска фич (например, подмешать контрольные из ТУИС). Ошибка — только в лог."""
    target = target or store
    for feature in registry.features:
        if feature.on_startup is None:
            continue
        try:
            feature.on_startup(target)
        except Exception:
            log.exception("on_startup %s failed", feature.name)


async def guard_message(ctx: Ctx, message: Message, command: str | None) -> bool:
    """Центральная проверка доступа для сообщений. True — можно обрабатывать дальше.
    Чужим бот не показывает ничего; на /start — запрос доступа владельцу."""
    if ctx.role == access.OWNER:
        return True
    if ctx.role is None:
        if command == "start":
            await testers.stranger_start(ctx, getattr(message, "from_user", None))
        return False
    if command is not None and command not in access.TESTER_COMMANDS:
        await ctx.delete_incoming()
        ctx.clear_pending()
        await home.screen_home(ctx, notice=access.TESTER_DENIED)
        return False
    return True


def _command_handler(spec: CommandSpec):
    async def handler(message: Message) -> None:
        ctx = Ctx.from_message(message)
        if not await guard_message(ctx, message, spec.name):
            return
        await ctx.delete_incoming()
        if spec.name != "cancel" and ctx.pending() is not None:
            # любая команда прерывает незаконченный ввод — иначе следующий текст
            # молча сохранится как д/з; об этом коротко сообщаем
            ctx.clear_pending()
            ctx.notice = home.CANCELLED
        try:
            await spec.handler(ctx)
        except Exception:
            log.exception("command /%s failed", spec.name)
            await home.screen_home(ctx)

    handler.__name__ = f"cmd_{spec.name}"
    return handler


async def on_unknown_command(message: Message) -> None:
    ctx = Ctx.from_message(message)
    if ctx.role is None:
        return
    await ctx.delete_incoming()
    had = ctx.pending() is not None
    await home.screen_home(ctx, notice=home.CANCELLED if had else None)


async def on_text(message: Message) -> None:
    ctx = Ctx.from_message(message)
    if ctx.role is None:
        return
    ctx.defer_save = True
    try:
        await _on_text(ctx, message)
    finally:
        try:
            await ctx.flush()
        except Exception:
            log.exception("save after text failed")


async def _on_text(ctx: Ctx, message: Message) -> None:
    expired = pending.is_expired(ctx.user_id)
    state = ctx.pending()
    if state is not None and (ctx.is_owner or (ctx.is_tester and state.name in access.TESTER_TEXT_STATES)):
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
        await message.answer("⌛ Ввод устарел (слишком долго не было ответа). Начни заново из меню.")
        await home.screen_home(ctx)
        return
    # текст без ожидания ввода — не молчим, а подсказываем
    await home.screen_home(ctx, notice=home.stray_hint(ctx))


SLOW_UPDATE_SECONDS = 0.7


def _log_slow(kind: str, what: str, started: float, shown: float | None = None) -> None:
    total = time.monotonic() - started
    if total >= SLOW_UPDATE_SECONDS:
        part = f", до показа {shown - started:.2f}s" if shown is not None else ""
        log.warning("slow %s %r: %.2fs%s", kind, what, total, part)


SLOW_API_SECONDS = 1.0


async def log_slow_api(make_request, bot, method):
    """Логирует медленные запросы к Telegram API (видно, где теряется время)."""
    started = time.monotonic()
    try:
        return await make_request(bot, method)
    finally:
        spent = time.monotonic() - started
        if spent >= SLOW_API_SECONDS and type(method).__name__ != "GetUpdates":
            log.warning("slow telegram %s: %.2fs", type(method).__name__, spent)


async def on_callback(callback: CallbackQuery) -> None:
    started = time.monotonic()
    raw = callback.data or ""
    main, _, back = raw.partition("|")
    parts = main.split(":")
    ctx = Ctx.from_callback(callback, args=parts[1:], back=back or None)
    ctx.defer_save = True
    if ctx.role is None:
        await ctx.answer()  # чужим — ничего, только гасим «часики»
        return
    if ctx.role == access.TESTER and not access.tester_may(parts[0], ctx.args):
        await ctx.answer(access.TESTER_DENIED, alert=True)
        return
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
        shown = time.monotonic()
        try:
            await ctx.flush()
        except Exception:
            log.exception("save after %r failed", raw)
        _log_slow("callback", raw, started, shown)


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
    run_startup_hooks(registry, store)
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
    bot.session.middleware(log_slow_api)
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
