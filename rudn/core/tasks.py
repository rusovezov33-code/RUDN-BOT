"""Задания (Д/з, лабы, экзамены) — чистая логика без Telegram.

Все задания живут в data["homework"]. Поле `kind` необязательно:
нет поля → "hw" (так старые записи остаются Д/з).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from . import timeutil

KINDS: dict[str, tuple[str, str]] = {
    "hw": ("📝", "Д/з"),
    "lab": ("🧪", "Лаба"),
    "exam": ("🎓", "Экзамен"),
}
DEADLINE_KINDS = ("hw", "lab")
EXAM_TYPES = {"exam": "Экзамен", "credit": "Зачёт"}

BUCKETS: list[tuple[str, str]] = [
    ("overdue", "⚠️ Просрочено"),
    ("today", "🔥 Сегодня"),
    ("tomorrow", "⏰ Завтра"),
    ("week", "📆 На неделе"),
    ("later", "🗓 Позже"),
    ("nodate", "❔ Без даты"),
]


def kind_of(item: dict) -> str:
    kind = item.get("kind")
    return kind if kind in KINDS else "hw"


def kind_icon(item: dict) -> str:
    return KINDS[kind_of(item)][0]


def kind_label(item: dict) -> str:
    if kind_of(item) == "exam":
        return EXAM_TYPES.get(str(item.get("exam_type") or "exam"), "Экзамен")
    return KINDS[kind_of(item)][1]


def item_date(item: dict) -> date | None:
    if item.get("date"):
        try:
            return date.fromisoformat(str(item["date"]))
        except ValueError:
            return None
    day_num = item.get("day")
    if not isinstance(day_num, int):
        return None
    month, year = item.get("month"), item.get("year")
    if not (month and year) and item.get("created"):
        try:
            stamp = datetime.fromisoformat(str(item["created"]))
        except ValueError:
            stamp = None
        if stamp is not None:
            month = month or stamp.month
            year = year or stamp.year
    if not month or not year:
        return None
    try:
        return date(int(year), int(month), day_num)
    except ValueError:
        return None


def item_on_date(item: dict, day: date) -> bool:
    """Совместимо со старой логикой (события без года/месяца)."""
    if item.get("date"):
        return item["date"] == day.isoformat()
    if item.get("day") != day.day:
        return False
    month, year = item.get("month"), item.get("year")
    if month and year:
        return int(month) == day.month and int(year) == day.year
    if month:
        return int(month) == day.month
    created = item.get("created")
    if created:
        try:
            stamp = datetime.fromisoformat(str(created))
        except ValueError:
            return True
        return stamp.year == day.year and stamp.month == day.month
    return True


def subject_name(item: dict, data: dict) -> str:
    if item.get("subject"):
        return str(item["subject"])
    index = item.get("subject_idx")
    subjects = data.get("subjects") or []
    if isinstance(index, int) and 0 <= index < len(subjects):
        return str(subjects[index].get("name") or "")
    return ""


def belongs_to(item: dict, data: dict, si: int) -> bool:
    subjects = data.get("subjects") or []
    if not 0 <= si < len(subjects):
        return False
    name = subjects[si].get("name") or ""
    own = subject_name(item, data)
    return item.get("subject_idx") == si or (bool(own) and timeutil.subjects_match(own, name))


def open_items(data: dict, kinds: Iterable[str] = DEADLINE_KINDS, si: int | None = None) -> list[dict]:
    kinds = tuple(kinds)
    return [
        item
        for item in data.get("homework") or []
        if not item.get("done") and kind_of(item) in kinds and (si is None or belongs_to(item, data, si))
    ]


def sort_key(item: dict) -> tuple:
    due = item_date(item)
    return (due or date.max, str(item.get("time") or ""), item.get("id") or 0)


def bucket_of(due: date | None, today: date) -> str:
    if due is None:
        return "nodate"
    delta = (due - today).days
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta <= 7:
        return "week"
    return "later"


def group_deadlines(items: Iterable[dict], today: date) -> list[tuple[str, str, list[dict]]]:
    groups: dict[str, list[dict]] = {key: [] for key, _ in BUCKETS}
    for item in sorted(items, key=sort_key):
        groups[bucket_of(item_date(item), today)].append(item)
    return [(key, title, groups[key]) for key, title in BUCKETS if groups[key]]


def urgent_counts(data: dict, today: date) -> dict[str, int]:
    counts = {key: 0 for key, _ in BUCKETS}
    for item in open_items(data):
        counts[bucket_of(item_date(item), today)] += 1
    return counts


def upcoming_exams(data: dict, today: date) -> list[dict]:
    items = [item for item in open_items(data, ("exam",)) if (item_date(item) or date.max) >= today]
    return sorted(items, key=sort_key)


def past_exams(data: dict, today: date) -> list[dict]:
    items = [
        item
        for item in data.get("homework") or []
        if kind_of(item) == "exam" and (item.get("done") or (item_date(item) or date.max) < today)
    ]
    return sorted(items, key=sort_key, reverse=True)


def find(data: dict, item_id: int) -> dict | None:
    for item in data.get("homework") or []:
        if item.get("id") == item_id:
            return item
    return None


def next_id(items: list[dict]) -> int:
    ids = [item.get("id") for item in items if isinstance(item.get("id"), int)]
    return max(ids) + 1 if ids else 0


def new_item(data: dict, kind: str, si: int | None, text: str, due: date | None, **extra: Any) -> dict:
    subjects = data.get("subjects") or []
    item: dict[str, Any] = {"id": next_id(data.setdefault("homework", []))}
    if si is not None and 0 <= si < len(subjects):
        item["subject"] = subjects[si].get("name")
        item["subject_idx"] = si
    item["text"] = text
    if due is not None:
        item.update(day=due.day, month=due.month, year=due.year, date=due.isoformat())
    item["done"] = False
    item["created"] = timeutil.now().isoformat()
    if kind != "hw":
        item["kind"] = kind
    item.update({key: value for key, value in extra.items() if value not in (None, "")})
    data["homework"].append(item)
    return item


def mark_done(item: dict, done: bool = True) -> None:
    item["done"] = done
    if done:
        item["done_at"] = timeutil.now().isoformat()
    else:
        item.pop("done_at", None)


def due_text(item: dict, today: date) -> str:
    due = item_date(item)
    if due is None:
        day_num = item.get("day")
        return f"{day_num} числа" if day_num else "без срока"
    delta = (due - today).days
    base = timeutil.fmt_dm(due)
    if delta < 0:
        return f"{base} (−{-delta} {timeutil.days_word(delta)})"
    if delta == 0:
        return f"{base} (сегодня)"
    if delta == 1:
        return f"{base} (завтра)"
    return base


def events_on(data: dict, day: date) -> list[dict]:
    return [item for item in data.get("events") or [] if item_on_date(item, day)]


def week_end(today: date) -> date:
    return today + timedelta(days=7)
