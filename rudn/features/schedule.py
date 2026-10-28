"""Расписание: сегодня / завтра / день / неделя + сводка «сейчас/далее» в меню."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape

from rudn.core import dresscode, tasks, timeutil as tu
from rudn.core.registry import CommandSpec, Feature, MenuButton
from rudn.core.ui import Ctx, b, chunk, nav, short, with_back


MAX_EVENT_BUTTONS = 5


def _due_on(data: dict, day: date) -> list[dict]:
    return [
        item
        for item in data.get("homework") or []
        if not item.get("done") and tasks.item_on_date(item, day)
    ]


def _start(time_str: str) -> str:
    return str(time_str).split("–")[0].split("-")[0].strip()


def place_tail(place: str) -> str:
    """« · АТИ ауд. 251» или « · онлайн» (ДОТ); без места — пусто."""
    if dresscode.is_online(place):
        return " · онлайн"
    return f" · {escape(str(place).strip())}" if str(place or "").strip() else ""


def home_name(subject: str) -> str:
    """Название для главного экрана: полное, а длинное — коротким именем."""
    name = str(subject or "").split(" / ")[0].strip()  # «Введение в специальность / Ознакомительная практика»
    return name if len(name) <= 30 else short(tu.short_name(name), 30)


def day_lines(data: dict, day: date, moment: datetime, detailed: bool) -> list[str]:
    """Пары дня: «время  предмет», под ней аудитория (только на экране дня) и задания на эту пару.
    Без значков в строках: текущая пара — жирным, прошедшая — зачёркнута."""
    lines: list[str] = []
    is_today = day == moment.date()
    due = _due_on(data, day)
    shown: set[int] = set()
    lessons = tu.lessons_for(day)
    for lesson in lessons:
        state = tu.lesson_state(lesson.time, moment) if is_today else "future"
        name = escape(lesson.subject if detailed else home_name(lesson.subject))
        time_text = escape(lesson.time if detailed else _start(lesson.time))
        where = place_tail(lesson.place) if detailed else ""
        if state == "now":
            left = tu.minutes_until(lesson.time, moment, end=True) or 0
            tail = f", ещё {tu.human_minutes(left)}" if detailed else ""
            lines.append(f"<b>{time_text}  {name}</b>{where}{tail}")
        elif state == "past":
            lines.append(f"<s>{time_text}</s>  {name}{where}")
        else:
            lines.append(f"{time_text}  {name}{where}")
        for item in due:
            if id(item) in shown:
                continue
            if tu.subjects_match(tasks.subject_name(item, data), lesson.subject):
                shown.add(id(item))
                if tasks.kind_of(item) == "exam":
                    body = tasks.titled_text(item)  # «Зачёт — письменная работа», без «Зачёт: Зачёт …»
                else:
                    body = f"{tasks.kind_label(item)}: {item.get('text') or ''}"
                text = escape(body) if detailed else escape(short(body, 40 + len(tasks.kind_label(item)) + 2))
                lines.append(f"      {text}")
    if not lessons:
        lines.append("Занятий нет.")
    elif detailed:
        take = dresscode.take_line(lessons)
        if take:
            lines.append("")
            lines.append(take)
    rest = [item for item in due if id(item) not in shown]
    if rest:
        lines.append("")
        lines.append("<b>Сроки</b>")
        for item in rest:
            subject = tu.short_name(tasks.subject_name(item, data))
            prefix = f"{escape(short(subject, 30))}: " if subject else ""
            text = tasks.titled_text(item) if tasks.kind_of(item) == "exam" else str(item.get("text") or "")
            body = escape(text) if detailed else escape(short(text, 40))
            lines.append(f"{prefix}{body}")
    events = tasks.events_on(data, day)
    if events:
        lines.append("")
        lines.append("<b>Напоминания</b>")
        for item in events:
            for part in str(item.get("text") or "").split("\n"):
                if part.strip():
                    lines.append(escape(part))
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
        f"<b>{escape(tu.fmt_day(day))}</b>{tag}",
        f"{tu.week_label(tu.week_type(day)).capitalize()} неделя",
        "",
    ]
    lines += day_lines(ctx.data, day, moment, detailed=True)
    back = ctx.back
    prev_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
    middle = b("Завтра", with_back("d:tm", back)) if day == today else b("Сегодня", with_back("d:t", back))
    rows = [
        [
            b(f"◀ {tu.fmt_dm(prev_day)}", with_back(f"d:{prev_day.isoformat()}", back)),
            middle,
            b(f"{tu.fmt_dm(next_day)} ▶", with_back(f"d:{next_day.isoformat()}", back)),
        ]
    ]
    extra = [b("🗓 Неделя", f"w:{tu.default_week_monday(day).isoformat()}")]
    if not tu.lessons_for(day):
        nearest = next_study_day(day)
        if nearest and nearest != next_day:
            extra.insert(0, b(f"Пары {tu.fmt_dm(nearest)}", with_back(f"d:{nearest.isoformat()}", back)))
    rows.append(extra)
    if ctx.is_owner:
        for index, item in enumerate(tasks.events_on(ctx.data, day)[:MAX_EVENT_BUTTONS]):
            label = str(item.get("text") or "").split("\n")[0]
            rows.append([b(f"🗑 {short(label, 17)}", f"evd:{day.isoformat()}:{index}")])
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
        f"<b>{tu.week_label(tu.week_type(monday)).capitalize()} неделя</b>{note}",
        f"{tu.fmt_short(monday)}–{tu.fmt_short(sunday)}",
        "",
    ]
    data = ctx.data
    sunday_shown = False
    for offset in range(7):
        day = monday + timedelta(days=offset)
        body = day_lines(data, day, moment, detailed=False)
        if offset == 6 and body == ["Занятий нет."]:
            continue
        sunday_shown = sunday_shown or offset == 6
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
    days = [monday + timedelta(days=i) for i in range(6)]
    if sunday_shown:
        days.append(sunday)
    day_buttons = [
        b(("· " if day == today else "") + tu.fmt_dm(day), f"d:{day.isoformat()}|w:{monday.isoformat()}")
        for day in days
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
        try:
            day = date.fromisoformat(key)
        except ValueError:
            await ctx.answer("Меню обновилось")
            day = today
    await screen_day(ctx, day)


async def on_week(ctx: Ctx, args: list[str]) -> None:
    key = args[0] if args else "now"
    try:
        monday = tu.default_week_monday(tu.today()) if key == "now" else date.fromisoformat(key)
    except ValueError:
        await ctx.answer("Меню обновилось")
        monday = tu.default_week_monday(tu.today())
    await screen_week(ctx, monday)


async def on_event_delete(ctx: Ctx, args: list[str]) -> None:
    """evd:<дата>:<n> — спросить, evd:<дата>:<n>:y — удалить n-е напоминание дня."""
    if not await ctx.require_owner():
        return
    try:
        day = date.fromisoformat(args[0])
        index = int(args[1])
        item = tasks.events_on(ctx.data, day)[index]
    except (IndexError, ValueError):
        await ctx.answer("Напоминание уже удалено", alert=True)
        await screen_day(ctx, tu.today())
        return
    if len(args) > 2 and args[2] == "y":
        ctx.data["events"].remove(item)
        ctx.save()
        ctx.toast = "🗑 Удалено"
        await screen_day(ctx, day)
        return
    text = "\n".join(escape(part) for part in str(item.get("text") or "").split("\n"))
    await ctx.show(
        f"🗑 <b>Удалить напоминание на {tu.fmt_dm(day)}?</b>\n\n{text}",
        [[b("🗑 Да, удалить", f"evd:{day.isoformat()}:{index}:y"), b("Отмена", f"d:{day.isoformat()}")], nav(f"d:{day.isoformat()}")],
    )


def summary(ctx: Ctx) -> list[str]:
    """Главный экран: пары на сегодня (МСК) — «начало–конец  предмет · корпус ауд.» (ДОТ — «· онлайн»);
    закончившиеся не показываем, текущая — жирным; под ними «Взять: …» для очных пар."""
    moment = tu.now()
    lessons = tu.lessons_for(moment.date())
    if not lessons:
        return ["Пар нет"]
    left = [lesson for lesson in lessons if tu.lesson_state(lesson.time, moment) != "past"]
    if not left:
        return ["Пары закончились"]
    lines = []
    for lesson in left:
        name = escape(home_name(lesson.subject))
        time_text = escape(lesson.time)
        where = place_tail(lesson.place)
        if tu.lesson_state(lesson.time, moment) == "now":
            lines.append(f"<b>{time_text}  {name}</b>{where}")
        else:
            lines.append(f"{time_text}  {name}{where}")
    take = dresscode.take_line(left)
    if take:
        lines.append("")
        lines.append(take)
    return lines


async def cmd_today(ctx: Ctx) -> None:
    await on_day(ctx, ["t"])


async def cmd_tomorrow(ctx: Ctx) -> None:
    await on_day(ctx, ["tm"])


async def cmd_week(ctx: Ctx) -> None:
    await on_week(ctx, ["now"])


feature = Feature(
    name="schedule",
    callbacks={"d": on_day, "w": on_week, "evd": on_event_delete},
    menu=[MenuButton("🗓 Расписание", "d:t", order=10, row="m1")],
    commands=[
        CommandSpec("today", "Пары на сегодня", cmd_today),
        CommandSpec("tomorrow", "Пары на завтра", cmd_tomorrow, hidden=True),
        CommandSpec("week", "Расписание на неделю", cmd_week, hidden=True),
    ],
    summary=summary,
    summary_order=10,
)
