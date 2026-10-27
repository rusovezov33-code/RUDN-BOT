"""Главное меню. Собирается из реестра: сводки и кнопки дают сами фичи."""
from __future__ import annotations

import logging
from html import escape

from . import timeutil
from .registry import REGISTRY, CommandSpec, Feature, MenuButton
from .ui import Ctx, b, chunk

log = logging.getLogger(__name__)


def menu_buttons(ctx: Ctx) -> list[list]:
    collected: list[MenuButton] = []
    for feature in REGISTRY.features:
        collected.extend(feature.menu)
        if feature.menu_dynamic:
            try:
                collected.extend(feature.menu_dynamic(ctx))
            except Exception:
                log.exception("menu_dynamic %s", feature.name)
    groups: dict[str, list[tuple[int, object]]] = {}
    order_of: dict[str, int] = {}
    for index, button in enumerate(collected):
        if button.owner_only and not ctx.is_owner:
            continue
        try:
            text = button.text(ctx) if callable(button.text) else button.text
        except Exception:
            log.exception("menu text %s", button.callback)
            continue
        if not text:
            continue
        key = button.row or f"_solo{index}"
        groups.setdefault(key, []).append((button.order, b(text, button.callback)))
        order_of[key] = min(order_of.get(key, button.order), button.order)
    rows: list[list] = []
    for key in sorted(groups, key=lambda k: order_of[k]):
        items = [btn for _, btn in sorted(groups[key], key=lambda x: x[0])]
        rows.extend(chunk(items, 3 if len(items) == 3 else 2))
    return rows


def home_text(ctx: Ctx) -> str:
    today = timeutil.today()
    lines = [
        f"🎓 <b>РУДН</b> · {escape(timeutil.fmt_day(today))}",
        f"{timeutil.week_label(timeutil.week_type(today)).capitalize()} неделя",
    ]
    if ctx.is_tester:
        lines.append("👀 <i>Режим тестера: только просмотр</i>")
    for summary in REGISTRY.summaries():
        try:
            part = summary(ctx)
        except Exception:
            log.exception("summary failed")
            continue
        if part:
            lines.append("")
            lines.extend(part)
    return "\n".join(lines)


CANCELLED = "✖️ <i>Ввод отменён</i>"


def stray_hint(ctx: Ctx) -> str:
    if ctx.is_owner:
        return (
            "💡 <i>Сейчас бот не ждёт текста. Пользуйся кнопками: «➕ Добавить» — задание, "
            "«📊 Баллы» → ✏️ — ввести баллы.</i>"
        )
    return "💡 <i>Бот управляется кнопками меню ниже.</i>"


async def screen_home(ctx: Ctx, args: list[str] | None = None, notice: str | None = None) -> None:
    ctx.clear_pending()
    text = home_text(ctx)
    await ctx.show(f"{notice}\n\n{text}" if notice else text, menu_buttons(ctx))


async def cmd_start(ctx: Ctx) -> None:
    await screen_home(ctx)


async def cmd_cancel(ctx: Ctx) -> None:
    had = ctx.pending() is not None
    ctx.clear_pending()
    # ctx.answer() у команды ничего не показывает — пишем прямо в меню
    await screen_home(ctx, notice=CANCELLED if had else None)


async def on_cancel(ctx: Ctx, args: list[str]) -> None:
    ctx.clear_pending()
    await ctx.answer("Отменено")
    await screen_home(ctx)


feature = Feature(
    name="home",
    callbacks={"home": screen_home, "x": on_cancel},
    commands=[
        CommandSpec("start", "Главное меню", cmd_start),
        CommandSpec("menu", "Главное меню", cmd_start, hidden=True),
        CommandSpec("cancel", "Отменить ввод", cmd_cancel, hidden=True),
    ],
)
