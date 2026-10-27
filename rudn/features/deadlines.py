"""🔥 Дедлайны: все невыполненные Д/з и лабы по сроку, отметка одним нажатием."""
from __future__ import annotations

from datetime import date
from html import escape

from rudn.core import tasks, timeutil as tu
from rudn.core.registry import CommandSpec, Feature, MenuButton
from rudn.core.ui import Ctx, b, maybe, nav, row_of, short

MAX_BUTTONS = 25
BUCKET_ICON = {"overdue": "⚠️", "today": "🔥", "tomorrow": "⏰", "week": "📆", "later": "🗓", "nodate": "❔"}


def item_line(item: dict, data: dict, today: date, with_subject: bool = True) -> str:
    subject = tu.short_name(tasks.subject_name(item, data)) if with_subject else ""
    who = f"{escape(short(subject, 32))}: " if subject else ""
    extra = []
    if item.get("time"):
        extra.append(escape(str(item["time"])))
    if item.get("place"):
        extra.append(escape(str(item["place"])))
    tail = f" ({', '.join(extra)})" if extra else ""
    return (
        f"• <b>{escape(tasks.due_text(item, today))}</b> · {tasks.kind_icon(item)} "
        f"{who}{escape(item.get('text') or '—')}{tail}"
    )


def done_button(item: dict, data: dict, view: str, with_subject: bool = True) -> object:
    subject = tu.short_name(tasks.subject_name(item, data)) if with_subject else ""
    label = f"{subject}: {item.get('text') or ''}" if subject else str(item.get("text") or "задание")
    return b(f"✅ {tasks.kind_icon(item)} {short(label, 38)}", f"dl:ok:{item['id']}:{view}")


def parse_view(view: str) -> int | None:
    if view.startswith("s") and view[1:].isdigit():
        return int(view[1:])
    return None


async def render(ctx: Ctx, view: str = "a", undo: dict | None = None) -> None:
    data = ctx.data
    today = tu.today()
    si = parse_view(view)
    rows: list[list] = []
    if undo is not None:
        rows.append([b(f"↩ Вернуть: {short(undo.get('text') or '', 30)}", f"dl:un:{undo['id']}:{view}")])
    if si is not None:
        subjects = data.get("subjects") or []
        if not 0 <= si < len(subjects):
            await ctx.show("Предмет не найден.", [nav("subs")])
            return
        title = f"📝 <b>Задания · {escape(subjects[si].get('name') or '')}</b>"
        items = tasks.open_items(data, tasks.ALL_KINDS, si=si)
        with_subject = False
        back = f"s:{si}"
    else:
        title = "🔥 <b>Дедлайны</b> — Д/з, лабы, контрольные"
        items = tasks.open_items(data)
        with_subject = True
        back = ctx.back or "home"
    lines = [title, ""]
    groups = tasks.group_deadlines(items, today)
    if not groups:
        lines.append("✅ Невыполненных заданий нет.")
    buttons = 0
    for key, header, group in groups:
        lines.append(f"<b>{header}</b> ({len(group)})")
        for item in group:
            lines.append(item_line(item, data, today, with_subject))
            if ctx.is_owner and buttons < MAX_BUTTONS:
                rows.append([done_button(item, data, view, with_subject)])
                buttons += 1
        lines.append("")
    exams_link = None
    if si is None:
        exams = len(tasks.upcoming_exams(data, today))
        if exams:
            lines.append(f"🎓 Экзамены и зачёты ({exams}) — отдельно, в «🎓 Экзамены».")
            exams_link = maybe(f"🎓 Экзамены ({exams})", "ex")
    if ctx.is_owner and items:
        lines.append("Нажми ✅, когда сделаешь.")
        if len(items) > MAX_BUTTONS:
            lines.append(f"Кнопки показаны для первых {MAX_BUTTONS}.")
    add = None
    if ctx.is_owner:
        add = maybe("➕ Добавить", f"add:s:{si}" if si is not None else "add")
    rows.append(row_of(add, b("☑️ Выполненные", f"dl:h:{view}")))
    if exams_link is not None:
        rows.append([exams_link])
    rows.append(nav(back))
    await ctx.show("\n".join(lines).strip(), rows)


async def render_history(ctx: Ctx, view: str) -> None:
    data = ctx.data
    today = tu.today()
    si = parse_view(view)
    done = [
        item
        for item in data.get("homework") or []
        if item.get("done")
        and (
            tasks.kind_of(item) in tasks.DEADLINE_KINDS
            if si is None
            # в карточке предмета — то же, что в его списке заданий (с экзаменами)
            else tasks.kind_of(item) in tasks.ALL_KINDS and tasks.belongs_to(item, data, si)
        )
    ]
    done.sort(key=lambda item: str(item.get("done_at") or item.get("created") or ""), reverse=True)
    done = done[:15]
    lines = ["☑️ <b>Выполненные</b> (последние 15)", ""]
    rows: list[list] = []
    if not done:
        lines.append("Пока пусто.")
    for item in done:
        lines.append(item_line(item, data, today, si is None))
        if ctx.is_owner:
            rows.append(
                [
                    b(f"↩ {short(item.get('text') or tasks.kind_label(item), 26)}", f"dl:un:{item['id']}:{view}"),
                    b("🗑 Удалить", f"dl:rm:{item['id']}:{view}"),
                ]
            )
    if ctx.is_owner and done:
        lines.append("\n↩ — вернуть в список, «🗑 Удалить» — удалить навсегда.")
    rows.append(nav(f"dl:{view}" if view != "a" else "dl"))
    await ctx.show("\n".join(lines), rows)


async def on_deadlines(ctx: Ctx, args: list[str]) -> None:
    action = args[0] if args else "a"
    if action in ("ok", "un", "rm"):
        if not await ctx.require_owner():
            return
        try:
            item_id, view = int(args[1]), (args[2] if len(args) > 2 else "a")
        except (IndexError, ValueError):
            await ctx.answer("Не найдено", alert=True)
            return
        item = tasks.find(ctx.data, item_id)
        if item is None:
            await ctx.answer("Задание уже удалено", alert=True)
            await render(ctx, view)
            return
        if action == "ok":
            tasks.mark_done(item, True)
            ctx.save()
            await ctx.answer("✅ Готово")
            await render(ctx, view, undo=item)
        elif action == "un":
            tasks.mark_done(item, False)
            ctx.save()
            await ctx.answer("↩ Вернул в список")
            await render(ctx, view)
        else:
            ctx.data["homework"].remove(item)
            ctx.save()
            await ctx.answer("🗑 Удалено")
            await render_history(ctx, view)
        return
    if action == "h":
        await render_history(ctx, args[1] if len(args) > 1 else "a")
        return
    if action == "s" and len(args) > 1 and args[1].isdigit():
        await render(ctx, f"s{args[1]}")
        return
    view = action if parse_view(action) is not None else "a"
    await render(ctx, view)


def menu_text(ctx: Ctx) -> str:
    counts = tasks.urgent_counts(ctx.data, tu.today())
    total = sum(counts.values())
    urgent = counts["overdue"] + counts["today"] + counts["tomorrow"]
    if not total:
        return "🔥 Дедлайны"
    return f"🔥 Дедлайны ({urgent}❗)" if urgent else f"🔥 Дедлайны ({total})"


def summary(ctx: Ctx) -> list[str]:
    data = ctx.data
    today = tu.today()
    counts = tasks.urgent_counts(data, today)
    if not sum(counts.values()):
        return ["✅ Дедлайнов нет"]
    parts = []
    for key, label in (("overdue", "просрочено"), ("today", "сегодня"), ("tomorrow", "завтра"), ("week", "на неделе")):
        if counts[key]:
            parts.append(f"{BUCKET_ICON[key]} {label} {counts[key]}")
    lines = ["🔥 <b>Дедлайны:</b> " + (" · ".join(parts) if parts else "срочных нет")]
    urgent = [
        item
        for item in sorted(tasks.open_items(data), key=tasks.sort_key)
        if tasks.bucket_of(tasks.item_date(item), today) in ("overdue", "today", "tomorrow")
    ]
    if not urgent:
        nearest = sorted(tasks.open_items(data), key=tasks.sort_key)[:1]
        urgent = nearest
    for item in urgent[:3]:
        icon = BUCKET_ICON[tasks.bucket_of(tasks.item_date(item), today)]
        subject = tu.short_name(tasks.subject_name(item, data))
        who = f"{escape(short(subject, 28))}: " if subject else ""
        lines.append(
            f"   {icon} {tasks.kind_icon(item)} {who}{escape(short(item.get('text') or '', 50))}"
            f" — {escape(tasks.due_text(item, today))}"
        )
    if len(urgent) > 3:
        lines.append(f"   … и ещё {len(urgent) - 3}")
    return lines


async def cmd_deadlines(ctx: Ctx) -> None:
    await render(ctx, "a")


feature = Feature(
    name="deadlines",
    callbacks={"dl": on_deadlines},
    menu=[MenuButton(menu_text, "dl", order=20, row="tasks")],
    commands=[CommandSpec("deadlines", "Дедлайны: Д/з, лабы, контрольные", cmd_deadlines)],
    summary=summary,
    summary_order=20,
)
