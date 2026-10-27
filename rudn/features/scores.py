"""📊 Баллы: БРС по каждому предмету из документов ТУИС и ручной ввод своих баллов
(хранится в data.json → "scores"), сумма, текущая оценка по шкале и сколько осталось до зачёта."""
from __future__ import annotations

from html import escape

from rudn import tuis
from rudn.core import timeutil as tu
from rudn.core.registry import Feature, MenuButton
from rudn.core.ui import Ctx, b, maybe, nav, row_of, short

STATE = "sc_input"


def subject_index(data: dict, name: str) -> int | None:
    for index, subject in enumerate(data.get("subjects") or []):
        if isinstance(subject, dict) and tu.subjects_match(subject.get("name") or "", name):
            return index
    return None


async def screen_list(ctx: Ctx) -> None:
    data = ctx.data
    lines = ["📊 <b>Баллы по БРС</b>", "Максимум и правила — из документов ТУИС; свои баллы вносишь сам.", ""]
    rows = []
    for c in tuis.courses():
        got, done_max = tuis.total(data, c)
        mark = f"{tuis.fmt_num(got)}/{tuis.fmt_num(c.get('total_max'))}" if done_max else "—"
        lines.append(f"• <b>{escape(c['name'])}</b>: {mark} · {tuis.brs_short(c)}")
        rows.append([b(f"{short(tu.short_name(c['name']), 34)} · {mark}", f"sc:c:{c['key']}")])
    missing = sorted((tuis.load().get("no_data") or {}).keys())
    if missing:
        lines += ["", "Нет БРС в документах: " + escape(", ".join(missing))]
    rows.append(nav())
    await ctx.show("\n".join(lines), rows)


async def screen_course(ctx: Ctx, key: str, error: str | None = None) -> None:
    data = ctx.data
    c = tuis.course(key)
    if c is None:
        await ctx.show("Предмет не найден.", [nav("sc")])
        return
    scores = tuis.scores_of(data, key)
    back = ctx.back if ctx.back and not ctx.back.startswith("sc") else None
    lines = [f"📊 <b>{escape(c['name'])}</b>", tuis.brs_short(c)]
    if c.get("admission_min"):
        lines.append(f"Допуск: {escape(c['admission_min'])}")
    lines.append("")
    rows = []
    for item in c.get("items") or []:
        value = scores.get(item["key"])
        maximum = tuis.item_max(c, item)
        lines.append(f"• {escape(item['name'])}: <b>{tuis.fmt_num(value)}</b> / {tuis.fmt_num(maximum)}")
        if ctx.is_owner:
            rows.append([b(f"✏️ {short(item['name'], 34)} ({tuis.fmt_num(value)}/{tuis.fmt_num(maximum)})", f"sc:e:{key}:{item['key']}")])
    lines.append("")
    lines += tuis.status_lines(data, c)
    if c.get("note"):
        lines.append(f"⚠️ {escape(c['note'])}")
    if error:
        lines.append(f"\n⚠️ {error}")
    si = subject_index(data, c.get("bot_subject") or "")
    extra = [maybe("📘 Предмет", f"s:{si}") if si is not None else None]
    if ctx.is_owner and scores:
        extra.append(b("🗑 Сбросить", f"sc:r:{key}"))
    rows.append(row_of(*extra))
    rows.append(nav(back or "sc"))
    await ctx.show("\n".join(lines), rows)


async def screen_edit(
    ctx: Ctx, key: str, item_key: str, error: str | None = None, saved: bool = False
) -> None:
    """Экран ввода баллов за пункт. Остаётся в режиме ввода после каждого сохранения
    (можно жать +1 много раз), пока не нажата «⬅ Назад»."""
    c = tuis.course(key)
    item = next((i for i in (c or {}).get("items") or [] if i["key"] == item_key), None)
    if c is None or item is None:
        await ctx.show("Пункт не найден.", [nav("sc")])
        return
    current = tuis.scores_of(ctx.data, key).get(item_key)
    maximum = tuis.item_max(c, item)
    ctx.wait_text(STATE, key=key, item=item_key)
    got, _ = tuis.total(ctx.data, c)
    lines = [
        f"✏️ <b>{escape(c['name'])}</b>",
        f"{escape(item['name'])}: сейчас <b>{tuis.fmt_num(current)}</b> из {tuis.fmt_num(maximum)}"
        + (" ✅ сохранено" if saved else ""),
        f"Всего по предмету: <b>{tuis.fmt_num(got)}</b> из {tuis.fmt_num(c.get('total_max'))}"
        f" · ещё можно набрать {tuis.fmt_num(tuis.remaining(ctx.data, c))}",
    ]
    if item.get("note"):
        lines.append(f"<i>{escape(item['note'])}</i>")
    lines += [
        "",
        "Напиши баллы сообщением: <code>3</code> — поставить, <code>+1</code> / <code>-1</code> — прибавить/убавить, <code>-</code> — очистить.",
        "Можно вводить несколько раз подряд; закончить — «⬅ Назад». <i>(ожидаю 15 минут)</i>",
    ]
    if error:
        lines.append(f"\n⚠️ {error}")
    rows = []
    step = [b(label, f"sc:v:{key}:{item_key}:{raw}") for label, raw in (("−1", "m1"), ("+1", "p1"))]
    rows.append(step)
    quick = [b(tuis.fmt_num(v), f"sc:v:{key}:{item_key}:{tuis.fmt_num(v).replace(',', '.')}") for v in _quick_values(maximum)]
    if quick:
        rows.append(quick)
    rows.append(nav(f"sc:c:{key}"))
    await ctx.show("\n".join(lines), rows)


def _quick_values(maximum: float) -> list[float]:
    if maximum <= 5:
        return [float(v) for v in range(0, int(maximum) + 1)]
    return [0.0, round(maximum / 2, 1), maximum]


def _apply(ctx: Ctx, key: str, item_key: str, raw: str) -> str | None:
    c = tuis.course(key)
    item = next((i for i in (c or {}).get("items") or [] if i["key"] == item_key), None)
    if c is None or item is None:
        return "Пункт не найден."
    current = tuis.scores_of(ctx.data, key).get(item_key)
    raw = {"p1": "+1", "m1": "-1"}.get(raw, raw)
    value, error = tuis.parse_score(raw, current, tuis.item_max(c, item))
    if error:
        return error
    tuis.set_score(ctx.data, key, item_key, value)
    ctx.save()
    return None


async def on_scores(ctx: Ctx, args: list[str]) -> None:
    action = args[0] if args else ""
    key = args[1] if len(args) > 1 else ""
    if action == "c":
        await screen_course(ctx, key)
    elif action == "e" and len(args) > 2:
        if not await ctx.require_owner():
            return
        await screen_edit(ctx, key, args[2])
    elif action == "v" and len(args) > 3:
        if not await ctx.require_owner():
            return
        error = _apply(ctx, key, args[2], args[3])
        await ctx.answer(error and "Не получилось" or "✅ Сохранено")
        await screen_edit(ctx, key, args[2], error, saved=not error)
    elif action == "r":
        if not await ctx.require_owner():
            return
        await ctx.show(
            "🗑 Сбросить все введённые баллы по этому предмету?",
            [[b("Да, сбросить", f"sc:R:{key}"), b("Нет", f"sc:c:{key}")], nav("sc")],
        )
    elif action == "R":
        if not await ctx.require_owner():
            return
        tuis.reset_scores(ctx.data, key)
        ctx.save()
        await screen_course(ctx, key)
    else:
        await screen_list(ctx)


async def on_score_text(ctx: Ctx, text: str) -> None:
    state = ctx.pending()
    if state is None:
        return
    key, item_key = state.data.get("key", ""), state.data.get("item", "")
    error = _apply(ctx, key, item_key, text)
    await screen_edit(ctx, key, item_key, error, saved=not error)


def summary(ctx: Ctx) -> list[str]:
    parts = []
    for c in tuis.courses():
        if tuis.has_points(ctx.data, c):
            got, _ = tuis.total(ctx.data, c)
            name = tu.short_name(c.get("bot_subject") or c["name"])
            parts.append(f"{escape(short(name, 18))} {tuis.fmt_num(got)}")
    return [f"📊 Баллы: {' · '.join(parts[:4])}"] if parts else []


def startup(target) -> None:
    tuis.startup(target)


feature = Feature(
    name="scores",
    callbacks={"sc": on_scores},
    menu=[MenuButton("📊 Баллы", "sc", order=31, row="subjects")],
    summary=summary,
    summary_order=40,
    text_states={STATE: on_score_text},
    on_startup=startup,
)
