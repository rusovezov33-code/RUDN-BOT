"""Расписание: сегодня / завтра / день / неделя + сводка «сейчас/далее» в меню."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape

from rudn.core import tasks, timeutil as tu
from rudn.core.registry import CommandSpec, Feature, MenuButton
from rudn.core.ui import Ctx, b, chunk, nav, short, with_back


def _due_on(data: dict, day: date) -> list[dict]:
    return [
        item
        for item in data.get("homework") or []
        if not item.get("done") and tasks.item_on_date(item, day)
    ]


def day_lines(data: dict, day: date, moment: datetime, detailed: bool) -> list[str]:
    lines: list[str] = []
    is_today = day == moment.date()
    due = _due_on(data, day)
    shown: set[int] = set()
    lessons = tu.lessons_for(day)
    for lesson in lessons:
        state = tu.lesson_state(lesson.time, moment) if is_today else "future"
        name = escape(lesson.subject)
        if state == "now":
            left = tu.minutes_until(lesson.time, moment, end=True) or 0
            lines.append(f"▶️ <b>{escape(lesson.time)} {name}</b> ← сейчас, ещё {tu.human_minutes(left)}")
        elif state == "past":
            lines.append(f"▫️ <s>{escape(lesson.time)}</s> {name}")
        else:
            lines.append(f"• {escape(lesson.time)} {name}")
        if detailed and lesson.place:
            lines.append(f"   📍 {escape(lesson.place)}")
        for item in due:
            if id(item) in shown:
                continue
            if tu.subjects_match(tasks.subject_name(item, data), lesson.subject):
                shown.add(id(item))
                text = escape(item.get("text") or "") if detailed else escape(short(item.get("text") or "", 40))
                lines.append(f"   {tasks.kind_icon(item)} {escape(tasks.kind_label(item))}: {text}")
    if not lessons:
        lines.append("Занятий нет.")
    rest = [item for item in due if id(item) not in shown]
    if rest:
        lines.append("")
        lines.append("🔥 <b>Сроки в этот день:</b>")
        for item in rest:
            subject = tasks.subject_name(item, data)
            prefix = f"{escape(short(subject, 30))}: " if subject else ""
            body = escape(item.get("text") or "") if detailed else escape(short(item.get("text") or "", 40))
            lines.append(f"{tasks.kind_icon(item)} {prefix}{body}")
    events = tasks.events_on(data, day)
    if events:
        lines.append("")
        lines.append("🔔 <b>Напоминания:</b>")
        for item in events:
            for index, part in enumerate(str(item.get("text") or "").split("\n")):
                lines.append(("• " if index == 0 else "  ") + escape(part))
    return lines


def next_study_day(after: date, limit: int = 14) -> date | None:
    for offset in range(1, limit + 1):
        day = after + timedelta(days=offset)
        if tu.lessons_for(day):
            return day
    return None


async def screen_day(ctx: Ctx, day: date) -> None:
    moment = tu.now()
    today = moment.date()
    tag = {0: " · сегодня", 1: " · завтра", -1: " · вчера"}.get((day - today).days, "")
    lines = [
        f"📅 <b>{escape(tu.fmt_day(day))}</b>{tag}",
        f"{tu.week_label(tu.week_type(day)).capitalize()} неделя"
        + (f" · обновлено {moment:%H:%M}" if day == today else ""),
        "",
    ]
    lines += day_lines(ctx.data, day, moment, detailed=True)
    back = ctx.back
    prev_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
    middle = (
        b("🔄 Обновить", with_back(f"d:{day.isoformat()}", back))
        if day == today
        else b("Сегодня", with_back("d:t", back))
    )
    rows = [
        [
            b(f"◀ {tu.fmt_dm(prev_day)}", with_back(f"d:{prev_day.isoformat()}", back)),
            middle,
            b(f"{tu.fmt_dm(next_day)} ▶", with_back(f"d:{next_day.isoformat()}", back)),
        ]
    ]
    extra = [b("🗓 Неделя", f"w:{tu.monday_of(day).isoformat()}")]
    if not tu.lessons_for(day):
        nearest = next_study_day(day)
        if nearest:
            extra.insert(0, b(f"⏭ Пары {tu.fmt_dm(nearest)}", with_back(f"d:{nearest.isoformat()}", back)))
    rows.append(extra)
    rows.append(nav(back))
    await ctx.show("\n".join(lines), rows)


async def screen_week(ctx: Ctx, monday: date) -> None:
    moment = tu.now()
    today = moment.date()
    monday = tu.monday_of(monday)
    sunday = monday + timedelta(days=6)
    current = tu.monday_of(today)
    note = " · эта" if monday == current else (" · следующая" if monday == current + timedelta(days=7) else "")
    lines = [
        f"🗓 <b>{tu.week_label(tu.week_type(monday)).capitalize()} неделя</b>{note}",
        f"{tu.fmt_short(monday)}–{tu.fmt_short(sunday)}",
        "",
    ]
    data = ctx.data
    for offset in range(7):
        day = monday + timedelta(days=offset)
        body = day_lines(data, day, moment, detailed=False)
        if offset == 6 and body == ["Занятий нет."]:
            continue
        mark = " · сегодня" if day == today else ""
        lines.append(f"<b>{tu.fmt_dm(day)}</b>{mark}")
        lines.extend("выходной" if line == "Занятий нет." else line for line in body if line)
        lines.append("")
    back = ctx.back
    prev_w, next_w = monday - timedelta(days=7), monday + timedelta(days=7)
    rows = [
        [
            b("◀ Пред.", with_back(f"w:{prev_w.isoformat()}", back)),
            b("Текущая", with_back("w:now", back)),
            b("След. ▶", with_back(f"w:{next_w.isoformat()}", back)),
        ]
    ]
    day_buttons = [
        b(("• " if day == today else "") + tu.fmt_dm(day), f"d:{day.isoformat()}|w:{monday.isoformat()}")
        for day in (monday + timedelta(days=i) for i in range(6))
    ]
    rows += chunk(day_buttons, 3)
    rows.append(nav(back))
    await ctx.show("\n".join(lines).strip(), rows)


async def on_day(ctx: Ctx, args: list[str]) -> None:
    today = tu.today()
    key = args[0] if args else "t"
    if key == "t":
        day = today
    elif key == "tm":
        day = today + timedelta(days=1)
    else:
        day = date.fromisoformat(key)
    await screen_day(ctx, day)


async def on_week(ctx: Ctx, args: list[str]) -> None:
    key = args[0] if args else "now"
    monday = tu.default_week_monday(tu.today()) if key == "now" else date.fromisoformat(key)
    await screen_week(ctx, monday)


def summary(ctx: Ctx) -> list[str]:
    moment = tu.now()
    today = moment.date()
    lessons = tu.lessons_for(today)
    lines: list[str] = []
    ongoing = [l for l in lessons if tu.lesson_state(l.time, moment) == "now"]
    future = [l for l in lessons if tu.lesson_state(l.time, moment) == "future"]
    place = lambda l: f" · {escape(l.place)}" if l.place else ""  # noqa: E731
    for lesson in ongoing[:1]:
        left = tu.minutes_until(lesson.time, moment, end=True) or 0
        lines.append(f"▶️ Сейчас: <b>{escape(lesson.subject)}</b>{place(lesson)} · ещё {tu.human_minutes(left)}")
    if future:
        lesson = future[0]
        wait = tu.minutes_until(lesson.time, moment) or 0
        lines.append(
            f"⏭ Далее: <b>{escape(lesson.time.split('–')[0])} {escape(lesson.subject)}</b>{place(lesson)}"
            f" · через {tu.human_minutes(wait)}"
        )
        if len(future) > 1:
            lines.append(f"   ещё пар сегодня: {len(future) - 1}")
    if not ongoing and not future:
        lines.append("✅ Пары на сегодня закончились" if lessons else "😌 Сегодня пар нет")
        nxt = next_study_day(today)
        if nxt:
            first = tu.lessons_for(nxt)[0]
            when = "Завтра" if nxt == today + timedelta(days=1) else tu.fmt_dm(nxt)
            lines.append(f"📅 {when}: первая пара {escape(first.time.split('–')[0])} {escape(first.subject)}{place(first)}")
    events = tasks.events_on(ctx.data, today)
    if events:
        lines.append("🔔 <b>Сегодня:</b>")
        for item in events[:3]:
            for index, part in enumerate(str(item.get("text") or "").split("\n")):
                lines.append(("• " if index == 0 else "  ") + escape(short(part, 120)))
    return lines


async def cmd_today(ctx: Ctx) -> None:
    await on_day(ctx, ["t"])


async def cmd_tomorrow(ctx: Ctx) -> None:
    await on_day(ctx, ["tm"])


async def cmd_week(ctx: Ctx) -> None:
    await on_week(ctx, ["now"])


feature = Feature(
    name="schedule",
    callbacks={"d": on_day, "w": on_week},
    menu=[
        MenuButton("📅 Сегодня", "d:t", order=10, row="sched"),
        MenuButton("⏭ Завтра", "d:tm", order=11, row="sched"),
        MenuButton("🗓 Неделя", "w:now", order=12, row="sched"),
    ],
    commands=[
        CommandSpec("today", "Пары на сегодня", cmd_today),
        CommandSpec("tomorrow", "Пары на завтра", cmd_tomorrow),
        CommandSpec("week", "Расписание на неделю", cmd_week),
    ],
    summary=summary,
    summary_order=10,
)
