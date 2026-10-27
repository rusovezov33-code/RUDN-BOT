"""⏰ Скоро: всё, что наступает в ближайшие 14 дней (контрольные, тесты, зачёты,
экзамены, Д/з, лабы) — по дате, с «через N дн.».

Не отдельный раздел меню, а блок: кратко — вверху главного экрана (сводка),
полностью — вверху «🔥 Дедлайнов» (deadlines.render вызывает block()).
Для КР/тестов/зачётов показывается, что на них будет (темы/задания), если это
известно из текста пункта: «… Задания: …» или темы в «кавычках».
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from html import escape

from rudn.core import tasks, timeutil as tu
from rudn.core.registry import Feature
from rudn.core.ui import Ctx, short

HORIZON_DAYS = 14
HOME_LIMIT = 5

_CHECK = re.compile(r"(?<![а-яё])(кр\d*|тест\w*|контрольн\w*|зач[её]т\w*|экзамен\w*|аттестац\w*|опрос\w*)", re.I)
_TASKS = re.compile(r"[.;:]?\s*Задания\s*[:—–-]\s*", re.I)
_QUOTED = re.compile(r"«([^»]+)»")


def soon_items(data: dict, today: date, days: int = HORIZON_DAYS) -> list[dict]:
    """Невыполненные пункты со сроком от сегодня до today+days включительно."""
    until = today + timedelta(days=days)
    found = [
        item
        for item in tasks.open_items(data, tasks.ALL_KINDS)
        if (due := tasks.item_date(item)) is not None and today <= due <= until
    ]
    return sorted(found, key=tasks.sort_key)


def is_check(item: dict) -> bool:
    """КР, тест, зачёт, экзамен — выделяются."""
    return tasks.kind_of(item) in ("ctrl", "exam") or bool(_CHECK.search(str(item.get("text") or "")))


def days_left(due: date, today: date) -> str:
    delta = (due - today).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    return f"через {delta} дн."


def split_topics(item: dict) -> tuple[str, str | None]:
    """(заголовок, что будет). Темы — из «… Задания: …» или из «кавычек»."""
    text = tasks.body_text(item) if tasks.kind_of(item) == "exam" else str(item.get("text") or "")
    text = " ".join(text.split())
    if isinstance(item.get("topics"), list) and item["topics"]:
        return text, "; ".join(str(x) for x in item["topics"])
    match = _TASKS.search(text)
    if match:
        return text[: match.start()].strip(" .;:"), text[match.end():].strip() or None
    quoted = [q for q in _QUOTED.findall(text) if not q.lower().startswith(("д.з", "д/з"))]
    return text, ("; ".join(quoted) if quoted else None)


def _title(item: dict, data: dict, limit: int) -> str:
    subject = tu.short_name(tasks.subject_name(item, data))
    label = tasks.kind_label(item) if tasks.kind_of(item) == "exam" else ""
    head, _ = split_topics(item)
    head = f"{label} — {head}" if label and head else (label or head or "—")
    who = f"{escape(short(subject, 28))}: " if subject else ""
    return f"{who}{escape(short(head, limit))}"


def entry_lines(item: dict, data: dict, today: date, full: bool) -> list[str]:
    due = tasks.item_date(item)
    when = f"<b>{escape(tu.fmt_dm(due))}{tasks.date_mark(item)}</b> · {days_left(due, today)}"
    check = is_check(item)
    mark = "❗" if check else "▫️"
    title = _title(item, data, 90 if full else 40)
    if check:
        title = f"<b>{title}</b>"
    lines = [f"{mark} {when} · {tasks.kind_icon(item)} {title}"]
    if check:
        _, topics = split_topics(item)
        if topics:
            lines.append(f"   📚 {escape(topics if full else short(topics, 70))}")
    return lines


def block(data: dict, today: date, full: bool = True) -> list[str]:
    """Строки блока «⏰ Скоро» (пусто, если в ближайшие 14 дней ничего нет)."""
    items = soon_items(data, today)
    if not items:
        return []
    lines = [f"⏰ <b>Скоро</b> — {HORIZON_DAYS} дней ({len(items)}):"]
    shown = items if full else items[:HOME_LIMIT]
    budget = None if full else HOME_LIMIT
    for item in shown:
        part = entry_lines(item, data, today, full)
        if budget is not None:
            if budget <= 0:
                break
            part = part[:budget]
            budget -= len(part)
        lines += part
    rest = len(items) - sum(1 for line in lines[1:] if not line.startswith("   "))
    if rest > 0:
        lines.append(f"   … ещё {rest} — в «🔥 Дедлайнах»")
    return lines


def summary(ctx: Ctx) -> list[str]:
    return block(ctx.data, tu.today(), full=False)


feature = Feature(name="soon", summary=summary, summary_order=5)
