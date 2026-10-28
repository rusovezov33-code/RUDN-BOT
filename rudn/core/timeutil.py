"""Время (Москва), верхняя/нижняя неделя, расписание на день, форматирование дат."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from . import config
from .config import DAY_CODES, Lesson

MSK = timezone(timedelta(hours=3))
FULL_DAY = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
WEEK_NOTE = {"both": "каждую неделю", "lower": "нижняя неделя", "upper": "верхняя неделя"}

# Тесты подменяют эту функцию, чтобы «заморозить» время.
_now_override: datetime | None = None


def now() -> datetime:
    return _now_override or datetime.now(MSK)


def today() -> date:
    return now().date()


def set_now(value: datetime | None) -> None:
    global _now_override
    _now_override = value


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_type(day: date) -> str:
    cfg = config.schedule()
    weeks = (monday_of(day) - monday_of(cfg.week_anchor)).days // 7
    upper = (weeks % 2 == 0) if cfg.anchor_is_upper else (weeks % 2 == 1)
    return "upper" if upper else "lower"


def week_label(kind: str) -> str:
    return "верхняя" if kind == "upper" else "нижняя"


def default_week_monday(day: date) -> date:
    """Неделя по умолчанию: в воскресенье показываем следующую."""
    monday = monday_of(day)
    return monday + timedelta(days=7) if day.weekday() == 6 else monday


def day_code(day: date) -> str:
    return DAY_CODES[day.weekday()]


def norm_name(value: str) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def canon_name(value: str) -> str:
    key = norm_name(value)
    return config.subjects().aliases.get(key, key)


def short_name(value: str) -> str:
    """Короткое имя предмета для кнопок и списков (config/subjects.json → short_names)."""
    names = config.subjects().short_names
    return names.get(norm_name(value)) or names.get(canon_name(value)) or str(value or "")


def subjects_match(left: str, right: str) -> bool:
    a, b = canon_name(left), canon_name(right)
    return bool(a) and a == b


def lessons_for(day: date) -> list[Lesson]:
    kind = week_type(day)
    return [item for item in config.schedule().days.get(day_code(day), ()) if item.weeks in ("both", kind)]


def slots_for_subject(name: str) -> list[tuple[str, Lesson]]:
    return [
        (code, lesson)
        for code, lessons in config.schedule().days.items()
        for lesson in lessons
        if subjects_match(lesson.subject, name)
    ]


def upcoming_lesson_dates(name: str, start: date, days: int = 60, moment: datetime | None = None) -> list[date]:
    """Даты пар по предмету. Если передан `moment`, в его день уже закончившиеся пары не считаются."""
    found = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        lessons = [lesson for lesson in lessons_for(day) if subjects_match(lesson.subject, name)]
        if moment is not None and day == moment.date():
            lessons = [lesson for lesson in lessons if lesson_state(lesson.time, moment) != "past"]
        if lessons:
            found.append(day)
    return found


def fmt_day(day: date) -> str:
    return f"{FULL_DAY[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}"


def fmt_short(day: date) -> str:
    return f"{day.day:02d}.{day.month:02d}"


def fmt_dm(day: date) -> str:
    """«ПН 26.10»"""
    return f"{day_code(day)} {fmt_short(day)}"


def lesson_bounds(time_str: str) -> tuple[int, int] | None:
    parts = re.split(r"\s*[–—-]\s*", time_str.strip())
    if len(parts) != 2:
        return None
    try:
        values = []
        for part in parts:
            hour, minute = part.strip().split(":")
            values.append(int(hour) * 60 + int(minute))
        return values[0], values[1]
    except ValueError:
        return None


def lesson_state(time_str: str, moment: datetime) -> str:
    """past | now | future."""
    bounds = lesson_bounds(time_str)
    if not bounds:
        return "future"
    current = moment.hour * 60 + moment.minute
    if current >= bounds[1]:
        return "past"
    if current >= bounds[0]:
        return "now"
    return "future"


def minutes_until(time_str: str, moment: datetime, end: bool = False) -> int | None:
    bounds = lesson_bounds(time_str)
    if not bounds:
        return None
    return bounds[1 if end else 0] - (moment.hour * 60 + moment.minute)


def human_minutes(value: int) -> str:
    if value < 60:
        return f"{value} мин"
    hours, minutes = divmod(value, 60)
    return f"{hours} ч {minutes:02d} мин" if minutes else f"{hours} ч"


def days_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def parse_user_date(raw: str, ref: date | None = None) -> date | None:
    """«15.01», «15.01.2027», «15/1», «2027-01-15». Без года — ближайшая будущая дата."""
    ref = ref or today()
    raw = raw.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return date.fromisoformat(raw)
        match = re.fullmatch(r"(\d{1,2})[./\-](\d{1,2})(?:[./\-](\d{2,4}))?", raw)
        if not match:
            return None
        day_num, month = int(match.group(1)), int(match.group(2))
        if match.group(3):
            year = int(match.group(3))
            year = year + 2000 if year < 100 else year
            return date(year, month, day_num)
        candidate = date(ref.year, month, day_num)
        if candidate < ref - timedelta(days=31):
            candidate = date(ref.year + 1, month, day_num)
        return candidate
    except ValueError:
        return None
