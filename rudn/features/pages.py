"""Информационные страницы из data.json → pages (например, «🏛 МФЦ / деканат»).

Страницы, которые заменены отдельными фичами (расписание, дедлайны), в меню не
показываются — их данные не трогаем, чтобы откат на stable-v1 был без потерь.
"""
from __future__ import annotations

from html import escape

from rudn.core.registry import Feature, MenuButton
from rudn.core.ui import Ctx, is_url, nav, short, url_btn

# Заголовки, содержащие эти слова, заменены фичами schedule/deadlines/exams.
SUPERSEDED = ("Расписание", "Дедлайны")


def visible_pages(data: dict) -> list[tuple[int, dict]]:
    return [
        (index, page)
        for index, page in enumerate(data.get("pages") or [])
        if not any(word in str(page.get("title") or "") for word in SUPERSEDED)
    ]


def dynamic_menu(ctx: Ctx) -> list[MenuButton]:
    return [
        MenuButton(short(page.get("title") or "Страница", 30), f"p:{index}", order=40 + i, row="pages")
        for i, (index, page) in enumerate(visible_pages(ctx.data))
    ]


async def on_page(ctx: Ctx, args: list[str]) -> None:
    try:
        page = ctx.data["pages"][int(args[0])]
    except (IndexError, KeyError, TypeError, ValueError):
        await ctx.show("Страница не найдена.", [nav()])
        return
    text = f"<b>{escape(page.get('title') or '')}</b>\n\n" + (escape(page.get("text") or "") or "Пока ничего нет.")
    rows = [
        [url_btn(f"🔗 {short(link.get('title') or link['url'], 40)}", link["url"])]
        for link in page.get("links") or []
        if isinstance(link, dict) and is_url(link.get("url"))
    ]
    rows.append(nav(ctx.back))
    await ctx.show(text, rows)


feature = Feature(name="pages", callbacks={"p": on_page}, menu_dynamic=dynamic_menu)
