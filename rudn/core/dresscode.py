"""Форма на пары (халат / шапочка / сменка) — config/dresscode.json.

Совпадение с расписанием по названию: точное (после нормализации и алиасов
config/subjects.json), алиас из dresscode.json или «начинается с» по целому
слову («Введение в специальность / …» → «Введение в специальность»).
Онлайн-пары (ДОТ) не считаются."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Iterable

from . import config, timeutil as tu

log = logging.getLogger(__name__)
FILE = "dresscode.json"


@lru_cache(maxsize=1)
def _load() -> tuple[tuple[str, ...], dict[str, tuple[str, ...]], dict[str, str], tuple[str, ...]]:
    try:
        raw = json.loads((config.CONFIG_DIR / FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (), {}, {}, ()
    except Exception:
        log.exception("не удалось прочитать %s", FILE)
        return (), {}, {}, ()
    subjects = {tu.norm_name(k): tuple(str(x).strip().lower() for x in v) for k, v in (raw.get("subjects") or {}).items()}
    aliases = {tu.norm_name(k): tu.norm_name(v) for k, v in (raw.get("aliases") or {}).items()}
    order = tuple(str(x).lower() for x in raw.get("order") or ())
    markers = tuple(str(x).lower() for x in raw.get("online_markers") or ("дот",))
    return order, subjects, aliases, markers


def reload() -> None:
    _load.cache_clear()


def items_for(subject: str) -> tuple[str, ...]:
    """Что взять на предмет (пусто — ничего или предмета нет в списке)."""
    _, subjects, aliases, _ = _load()
    if not subjects:
        return ()
    for key in (tu.norm_name(subject), tu.canon_name(subject)):
        key = aliases.get(key, key)
        if key in subjects:
            return subjects[key]
    name = tu.norm_name(subject)
    for key, items in subjects.items():
        if name.startswith(key) and name[len(key) : len(key) + 1] in ("", " ", "/", ",", "("):
            return items
    return ()


def is_online(place: str) -> bool:
    _, _, _, markers = _load()
    low = str(place or "").lower()
    return any(marker in low for marker in markers)


def union(items: Iterable[str]) -> list[str]:
    order, _, _, _ = _load()
    found = set(items)
    known = [x for x in order if x in found]
    return known + sorted(found - set(known))


def for_lessons(lessons: Iterable) -> list[str]:
    """Объединение формы по очным парам."""
    picked: list[str] = []
    for lesson in lessons:
        if is_online(lesson.place):
            continue
        picked.extend(items_for(lesson.subject))
    return union(picked)


def take_line(lessons: Iterable) -> str | None:
    items = for_lessons(lessons)
    return f"Взять: {', '.join(items)}" if items else None


def form_line(subject: str) -> str | None:
    items = union(items_for(subject))
    return f"Форма: {', '.join(items)}" if items else None
