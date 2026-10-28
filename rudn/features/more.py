"""⋯ Ещё: всё, что редко нужно — страницы (МФЦ / деканат), ссылка ТУИС,
у владельца — «➕ Добавить» и «⚙️ Сервис». Тестер видит то же, кроме кнопок владельца."""
from __future__ import annotations

from rudn.core import config
from rudn.core.registry import Feature, MenuButton
from rudn.core.ui import Ctx, b, chunk, is_url, maybe, nav, row_of, short, url_btn
from rudn.features.pages import visible_pages

PREFIX = "mo"


async def screen_more(ctx: Ctx, args: list[str] | None = None) -> None:
    rows: list[list] = []
    pages = [b(short(page.get("title") or "Страница", 20), f"p:{index}|{PREFIX}") for index, page in visible_pages(ctx.data)]
    rows += chunk(pages, 2)
    links = [url_btn(f"🔗 {short(link['title'], 17)}", link["url"]) for link in config.subjects().common_links if is_url(link.get("url"))]
    rows.append(row_of(*links))
    if ctx.is_owner:
        rows.append(row_of(maybe("➕ Добавить", "add"), maybe("⚙️ Сервис", "adm")))
    rows.append([b("⬅ Назад", "home")])
    await ctx.show("<b>Ещё</b>", [row for row in rows if row])


feature = Feature(
    name="more",
    callbacks={PREFIX: screen_more},
    menu=[MenuButton("⋯ Ещё", PREFIX, order=31, row="m3")],
)
