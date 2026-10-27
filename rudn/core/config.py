"""Настройки из переменных окружения и конфиг-файлов config/*.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.getenv("RUDN_CONFIG_DIR") or ROOT / "config")
BUNDLED_DATA = ROOT / "data.json"

DAY_CODES = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]


def token() -> str:
    return os.getenv("BOT_TOKEN", "").strip()


def owner_id() -> int | None:
    raw = os.getenv("OWNER_ID", "").strip()
    try:
        return int(raw) or None
    except ValueError:
        return None


def strict() -> bool:
    """Строгий режим (тесты): ошибки разметки кнопок бросают исключение."""
    return os.getenv("RUDN_STRICT", "") == "1"


def data_path() -> Path:
    override = os.getenv("DATA_FILE")
    if override:
        return Path(override)
    volume = Path(os.getenv("DATA_DIR", "/data"))
    if volume.is_dir() and os.access(volume, os.W_OK):
        return volume / "data.json"
    return BUNDLED_DATA


def link_secret() -> str:
    return os.getenv("FILE_LINK_SECRET") or os.getenv("BOT_TOKEN") or "rudn-bot"


def public_base() -> str | None:
    for key in ("PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL"):
        value = os.getenv(key, "").strip().rstrip("/")
        if not value:
            continue
        if not value.startswith(("http://", "https://")):
            value = "https://" + value
        return value
    return None


def _read_json(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ожидался JSON-объект")
    return data


@dataclass(frozen=True)
class Lesson:
    time: str
    subject: str
    place: str
    weeks: str  # both | upper | lower


@dataclass(frozen=True)
class ScheduleConfig:
    week_anchor: date
    anchor_is_upper: bool
    days: dict[str, tuple[Lesson, ...]]


@dataclass(frozen=True)
class SubjectsConfig:
    canonical: tuple[str, ...]
    renames: dict[str, str]
    aliases: dict[str, str]
    legacy_descriptions: frozenset[str]
    drop_if_empty: frozenset[str]
    common_links: tuple[dict[str, str], ...]
    section_template: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    short_names: dict[str, str] = field(default_factory=dict)  # норм. полное имя → короткое для кнопок


@lru_cache(maxsize=1)
def schedule() -> ScheduleConfig:
    raw = _read_json("schedule.json")
    days: dict[str, tuple[Lesson, ...]] = {}
    for code, lessons in (raw.get("days") or {}).items():
        if code not in DAY_CODES:
            raise ValueError(f"schedule.json: неизвестный день {code!r}")
        items = []
        for item in lessons:
            weeks = item.get("weeks", "both")
            if weeks not in ("both", "upper", "lower"):
                raise ValueError(f"schedule.json: weeks={weeks!r}")
            items.append(Lesson(str(item["time"]), str(item["subject"]), str(item.get("place") or ""), weeks))
        days[code] = tuple(items)
    return ScheduleConfig(
        week_anchor=date.fromisoformat(raw.get("week_anchor", "2026-10-26")),
        anchor_is_upper=bool(raw.get("anchor_is_upper", True)),
        days=days,
    )


def _norm(value: str) -> str:
    """Как timeutil.norm_name: регистр, ё→е, лишние пробелы."""
    return " ".join(str(value or "").lower().replace("ё", "е").split())


@lru_cache(maxsize=1)
def subjects() -> SubjectsConfig:
    raw = _read_json("subjects.json")
    return SubjectsConfig(
        canonical=tuple(raw.get("canonical") or []),
        renames=dict(raw.get("renames") or {}),
        aliases={_norm(k): _norm(v) for k, v in (raw.get("aliases") or {}).items()},
        legacy_descriptions=frozenset(raw.get("legacy_descriptions") or []),
        drop_if_empty=frozenset(raw.get("drop_if_empty") or []),
        common_links=tuple(raw.get("common_links") or []),
        section_template=tuple(raw.get("section_template") or []),
        short_names={_norm(k): str(v) for k, v in (raw.get("short_names") or {}).items() if v},
    )
