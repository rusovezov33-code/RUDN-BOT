"""🎓 Экзамены и зачёты — отдельный экран с обратным отсчётом."""
from __future__ import annotations

from datetime import date
from html import escape

from rudn.core import tasks, timeutil as tu
from rudn.core.registry import CommandSpec, Feature, MenuButton
from rudn.core.ui import Ctx, b, maybe, nav, row_of, short


def countdown(due: date | None, today: date) -> str:
    if due is None:
        return "дата не указана"
    delta = (due - today).days
    if delta < 0:
        return f"{-delta} {tu.days_word(delta)} назад"
    if delta == 0:
        return "сегодня!"
    if delta == 1:
        return "завтра!"
    return f"через {delta} {tu.days_word(delta)}"


def exam_lines(item: dict, data: dict, today: date) -> list[str]:
    due = tasks.item_date(item)
    subject = tasks.subject_name(item, data) or "—"
    when = tu.fmt_dm(due) if due else "—"
    lines = [f"• <b>{when}</b> · {countdown(due, today)} — {escape(tasks.kind_label(item))} · <b>{escape(subject)}</b>"]
    details = ", ".join(escape(str(item[key])) for key in ("time", "place") if item.get(key))
    if details:
        lines.append(f"   🕘 {details}")
    if item.get("text"):
        lines.append(f"   {escape(str(item['text']))}")
    return lines


async def render(ctx: Ctx, past: bool = False) -> None:
    data = ctx.data
    today = tu.today()
    items = tasks.past_exams(data, today) if past else tasks.upcoming_exams(data, today)
    title = "🎓 <b>Прошедшие / сданные</b>" if past else "🎓 <b>Экзамены и зачёты</b>"
    lines = [title, ""]
    rows: list[list] = []
    if not items:
        lines.append("Пока пусто." if past else "Ближайших экзаменов и зачётов нет.")
        if not past and ctx.is_owner:
            lines.append("Добавь: «➕ Экзамен/зачёт» ниже.")
    for item in items[:30]:
        lines.extend(exam_lines(item, data, today))
        if ctx.is_owner:
            label = f"{tasks.subject_name(item, data)} {tu.fmt_short(tasks.item_date(item)) if tasks.item_date(item) else ''}"
            if past:
                rows.append([b(f"↩ {short(label, 30)}", f"ex:un:{item['id']}"), b("🗑", f"ex:rm:{item['id']}")])
            else:
                rows.append([b(f"✅ Сдал: {short(label, 34)}", f"ex:ok:{item['id']}")])
    if past:
        rows.append(nav("ex"))
    else:
        add = maybe("➕ Экзамен/зачёт", "add:k:exam") if ctx.is_owner else None
        rows.append(row_of(add, b("🗂 Прошедшие", "ex:p")))
        rows.append(nav(ctx.back))
    await ctx.show("\n".join(lines).strip(), rows)


async def on_exams(ctx: Ctx, args: list[str]) -> None:
    action = args[0] if args else ""
    if action in ("ok", "un", "rm"):
        if not await ctx.require_owner():
            return
        try:
            item = tasks.find(ctx.data, int(args[1]))
        except (IndexError, ValueError):
            item = None
        if item is None:
            await ctx.answer("Не найдено", alert=True)
        elif action == "rm":
            ctx.data["homework"].remove(item)
            ctx.save()
            await ctx.answer("🗑 Удалено")
        else:
            tasks.mark_done(item, action == "ok")
            ctx.save()
            await ctx.answer("🎉 Отмечено как сдано" if action == "ok" else "↩ Вернул")
        await render(ctx, past=action != "ok")
        return
    await render(ctx, past=action == "p")


def menu_text(ctx: Ctx) -> str:
    count = len(tasks.upcoming_exams(ctx.data, tu.today()))
    return f"🎓 Экзамены ({count})" if count else "🎓 Экзамены"


def summary(ctx: Ctx) -> list[str]:
    data = ctx.data
    today = tu.today()
    items = tasks.upcoming_exams(data, today)
    if not items:
        return []
    item = items[0]
    due = tasks.item_date(item)
    subject = tasks.subject_name(item, data) or "—"
    when = tu.fmt_dm(due) if due else "—"
    return [
        f"🎓 Ближайший: {escape(tasks.kind_label(item))} · <b>{escape(short(subject, 40))}</b> — {when} ({countdown(due, today)})"
    ]


async def cmd_exams(ctx: Ctx) -> None:
    await render(ctx)


feature = Feature(
    name="exams",
    callbacks={"ex": on_exams},
    menu=[MenuButton(menu_text, "ex", order=21, row="tasks")],
    commands=[CommandSpec("exams", "Экзамены и зачёты", cmd_exams)],
    summary=summary,
    summary_order=30,
)
