"""Данные из документов ТУИС (config/tuis.json): БРС, литература, планы занятий,
контрольные и итоговые испытания. Чистая логика без Telegram.

Источник истины — только документы курса; чего в них нет, помечено «нет данных».
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from functools import lru_cache
from html import escape
from typing import Any

from rudn.core import config, tasks, timeutil as tu

log = logging.getLogger(__name__)
FILE = "tuis.json"
PASS_MIN = 51
SEEDED_KEY = "tuis_seeded"
SCORES_KEY = "scores"


# ---------------------------------------------------------------- загрузка
@lru_cache(maxsize=1)
def load() -> dict:
    path = config.CONFIG_DIR / FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        log.exception("config/%s is broken", FILE)
        return {}
    return raw if isinstance(raw, dict) else {}


def courses() -> list[dict]:
    return [c for c in load().get("courses") or [] if isinstance(c, dict) and c.get("key")]


def course(key: str) -> dict | None:
    return next((c for c in courses() if c["key"] == key), None)


def courses_for(name: str) -> list[dict]:
    return [c for c in courses() if c.get("bot_subject") and tu.subjects_match(c["bot_subject"], name)]


def _by_subject(table: dict | None, name: str) -> Any:
    for subject, value in (table or {}).items():
        if tu.subjects_match(subject, name):
            return value
    return None


def literature_for(name: str) -> dict | None:
    return _by_subject(load().get("literature"), name)


def plan_for(name: str) -> dict | None:
    return _by_subject(load().get("plans"), name)


def no_data_for(name: str) -> str | None:
    return _by_subject(load().get("no_data"), name)


def events() -> list[dict]:
    return [e for e in load().get("events") or [] if isinstance(e, dict) and e.get("key")]


def event_date(event: dict) -> date | None:
    try:
        return date.fromisoformat(event["date"]) if event.get("date") else None
    except ValueError:
        return None


# ---------------------------------------------------------------- баллы
def item_max(c: dict, item: dict) -> float:
    value = item.get("max_total")
    return float(value) if isinstance(value, (int, float)) else float(c.get("total_max") or 100)


def scores_of(data: dict, key: str) -> dict[str, float]:
    raw = (data.get(SCORES_KEY) or {}).get(key) if isinstance(data.get(SCORES_KEY), dict) else None
    if not isinstance(raw, dict):
        return {}
    return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def set_score(data: dict, key: str, item_key: str, value: float | None) -> None:
    table = data.get(SCORES_KEY)
    if not isinstance(table, dict):
        table = data[SCORES_KEY] = {}
    row = table.get(key)
    if not isinstance(row, dict):
        row = table[key] = {}
    if value is None:
        row.pop(item_key, None)
        if not row:
            table.pop(key, None)
    else:
        row[item_key] = int(value) if float(value).is_integer() else round(float(value), 2)


def reset_scores(data: dict, key: str) -> None:
    if isinstance(data.get(SCORES_KEY), dict):
        data[SCORES_KEY].pop(key, None)


def total(data: dict, c: dict) -> tuple[float, float]:
    """(набрано, максимум по уже оценённым пунктам)."""
    scores = scores_of(data, c["key"])
    got = done_max = 0.0
    for item in c.get("items") or []:
        if item["key"] in scores:
            got += scores[item["key"]]
            done_max += item_max(c, item)
    return got, done_max


def max_sum(c: dict) -> float:
    """Сумма максимумов всех пунктов БРС (у Физики в документе 98, а не 100)."""
    return sum(item_max(c, item) for item in c.get("items") or [])


def remaining(data: dict, c: dict) -> float:
    """Сколько ещё можно набрать: по каждому пункту (максимум − уже набрано)."""
    return max(0.0, max_sum(c) - total(data, c)[0])


def has_points(data: dict, c: dict) -> bool:
    """Баллы показываем в сводках только когда набрано больше нуля
    (явный 0 виден на экране «📊 Баллы» у самого пункта)."""
    return total(data, c)[0] > 0


def grade(c: dict, points: float) -> dict | None:
    for row in c.get("scale") or []:
        if row["min"] <= points <= row["max"] + 0.999:
            return row
    return None


def fmt_num(value: float | None) -> str:
    if value is None:
        return "—"
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


_NUM = re.compile(r"^\s*([+\-−]?)\s*(\d+(?:[.,]\d+)?)\s*$")


def parse_score(raw: str, current: float | None, maximum: float) -> tuple[float | None, str | None]:
    """«12» — поставить, «+2» / «-1» — прибавить/убавить, «-» или «0»… Возвращает (значение|None=очистить, ошибка)."""
    text = (raw or "").strip()
    if text in ("-", "−", "—", "сброс", "очистить"):
        return None, None
    match = _NUM.match(text)
    if not match:
        return None, "Нужно число, например <code>3</code>, <code>2,5</code> или <code>+1</code>."
    sign, number = match.group(1), float(match.group(2).replace(",", "."))
    if sign == "+":
        value = (current or 0) + number
    elif sign in ("-", "−"):
        value = (current or 0) - number
    else:
        value = number
    if value < 0 or value > maximum + 1e-9:
        return None, f"Должно быть от 0 до {fmt_num(maximum)}."
    return round(value, 2), None


def status_lines(data: dict, c: dict) -> list[str]:
    got, done_max = total(data, c)
    full = float(c.get("total_max") or 100)
    pass_min = float(c.get("pass_min") or PASS_MIN)
    lines = [f"Набрано: <b>{fmt_num(got)}</b> из {fmt_num(full)} (оценено пунктов на {fmt_num(done_max)} б.)"]
    left = remaining(data, c)
    if got >= pass_min:
        lines.append(f"✅ Порог {fmt_num(pass_min)} б. пройден.")
    else:
        need = pass_min - got
        lines.append(f"До зачёта/положительной оценки ({fmt_num(pass_min)}): не хватает <b>{fmt_num(need)}</b> б.")
        if need > left + 1e-9:
            lines.append(f"⚠️ В оставшихся пунктах только {fmt_num(left)} б. — нужны отработки/пересдача.")
        else:
            lines.append(f"Ещё можно набрать: {fmt_num(left)} б.")
    row = grade(c, got)
    if row and done_max >= full - 1e-9:
        lines.append(f"Итог по шкале: {row['ects']} — {escape(row['ru'])}")
    return lines


# ---------------------------------------------------------------- тексты разделов
def _source(c_or_entry: dict, key: str = "source_file") -> str:
    value = c_or_entry.get(key) or c_or_entry.get("source")
    return f"<i>Источник: {escape(value)}</i>" if value else ""


def scale_text(c: dict) -> str:
    rows = c.get("scale") or []
    if not rows:
        return "Шкала: нет данных"
    return "Шкала: " + " · ".join(f"{r['min']}–{r['max']} {r['ects']}" for r in rows[:5]) + "; ≤50 — неуд."


def brs_block(c: dict) -> str:
    lines = [f"<b>{escape(c['name'])}</b>", f"<i>{escape(c.get('version_doc') or '')}</i>"]
    lines.append(f"Форма контроля: <b>{escape(c.get('final_control') or 'нет данных')}</b>")
    lines.append(f"Максимум: {fmt_num(c.get('total_max'))} · минимум для зачёта/оценки «3»: {fmt_num(c.get('pass_min') or PASS_MIN)}")
    lines.append(f"Допуск: {escape(c.get('admission_min') or 'нет данных')}")
    for item in c.get("items") or []:
        count, each = item.get("count"), item.get("max_each")
        formula = f"{count} × {fmt_num(each)} = " if count and each else ""
        note = f" — {escape(item['note'])}" if item.get("note") else ""
        lines.append(f"• {escape(item['name'])}: {formula}<b>{fmt_num(item.get('max_total'))}</b>{note}")
    lines.append(scale_text(c))
    if c.get("penalties"):
        lines.append(f"Пропуски/отработки: {escape(c['penalties'])}")
    if c.get("note"):
        lines.append(f"⚠️ {escape(c['note'])}")
    lines.append(_source(c))
    return "\n".join(line for line in lines if line)


def brs_short(c: dict) -> str:
    return f"{escape(c.get('final_control') or 'нет данных')} · из {fmt_num(c.get('total_max'))}, порог {fmt_num(c.get('pass_min') or PASS_MIN)}"


def brs_text(name: str) -> str:
    found = courses_for(name)
    if found:
        return "\n\n".join(brs_block(c) for c in found)
    note = no_data_for(name)
    return f"БРС: нет данных. {escape(note)}" if note else ""


def literature_text(name: str) -> tuple[str, list[dict]]:
    entry = literature_for(name)
    if not entry:
        return "", []
    lines = ["<b>Основная литература:</b>"]
    lines += [f"{i}. {escape(x)}" for i, x in enumerate(entry.get("main") or [], 1)] or ["нет данных"]
    if entry.get("additional"):
        lines.append("<b>Дополнительная:</b>")
        lines += [f"• {escape(x)}" for x in entry["additional"]]
    lines.append(_source(entry))
    return "\n".join(lines), [link for link in entry.get("links") or [] if isinstance(link, dict)]


def _mark(day: date | None, nearest: date | None, today: date) -> str:
    if day is None:
        return "•"
    if day == nearest:
        return "👉"
    return "✔️" if day < today else "•"


def _plan_lines(rows: list[dict], today: date) -> list[str]:
    dates = [date.fromisoformat(r["date"]) for r in rows if r.get("date")]
    nearest = min((d for d in dates if d >= today), default=None)
    lines = []
    for row in rows:
        day = date.fromisoformat(row["date"]) if row.get("date") else None
        week = f"нед. {row['week']}, " if row.get("week") else ""
        when = f"{week}{tu.fmt_dm(day)}" if day else week.rstrip(", ")
        extra = f" — <b>{escape(row['lab'])}</b>" if row.get("lab") else ""
        lines.append(f"{_mark(day, nearest, today)} {when}: {escape(row.get('topic') or '')}{extra}")
    return lines


def lectures_text(name: str, today: date | None = None) -> str:
    plan = plan_for(name)
    if not plan or not plan.get("lectures"):
        return ""
    today = today or tu.today()
    return "\n".join([f"<b>{escape(plan.get('lectures_title') or 'Лекции')}</b>"] + _plan_lines(plan["lectures"], today) + [_source(plan)])


def labs_text(name: str, today: date | None = None) -> str:
    plan = plan_for(name)
    if not plan or not plan.get("labs"):
        return ""
    today = today or tu.today()
    lines = [f"<b>{escape(plan.get('labs_title') or 'Занятия')}</b>"] + _plan_lines(plan["labs"], today)
    cons = [c for c in plan.get("consultations") or [] if c.get("date") and c["date"] >= today.isoformat()]
    if cons:
        lines.append("<b>Консультации (Яндекс Телемост):</b> " + ", ".join(f"{tu.fmt_dm(date.fromisoformat(c['date']))} {c['time']}" for c in cons))
    if plan.get("note"):
        lines.append(f"⚠️ {escape(plan['note'])}")
    lines.append(_source(plan))
    return "\n".join(lines)


def final_text(name: str) -> str:
    lines = []
    for c in courses_for(name):
        lines.append(f"• {escape(c['name'])}: <b>{escape(c.get('final_control') or 'нет данных')}</b>")
    for event in events():
        if event.get("kind") == "exam" and tu.subjects_match(event["subject"], name):
            day = event_date(event)
            lines.append(f"  {tu.fmt_dm(day) if day else 'дата не указана'} — {escape(event['text'])}")
    if not lines:
        note = no_data_for(name)
        return f"Форма контроля: нет данных. {escape(note)}" if note else ""
    return "\n".join(lines)


def section_block(name: str, title: str) -> tuple[str, list[dict]]:
    """Блок «из документов ТУИС» для раздела предмета — по ключевым словам названия."""
    low = tu.norm_name(title)
    if "литератур" in low:
        return literature_text(name)
    if "лекци" in low:
        return lectures_text(name), []
    if "практик" in low or "лаборатор" in low:
        return labs_text(name), []
    if "брс" in low or "балл" in low:
        return brs_text(name), []
    if "экзамен" in low or "зачет" in low:
        return final_text(name), []
    return "", []


# ---------------------------------------------------------------- контрольные → дедлайны/экзамены
_QUOTED = re.compile(r"«([^»]+)»")


def _stems(text: str) -> set[str]:
    return {w[:6] for part in _QUOTED.findall(text or "") for w in re.findall(r"[а-яёa-z]{4,}", part.lower())}


def _subject_index(data: dict, name: str) -> int | None:
    for index, subject in enumerate(data.get("subjects") or []):
        if isinstance(subject, dict) and tu.subjects_match(subject.get("name") or "", name):
            return index
    return None


def is_duplicate(data: dict, event: dict, si: int) -> bool:
    day = event_date(event)
    want_exam = event.get("kind") == "exam"
    stems = _stems(event.get("text") or "")
    for item in data.get("homework") or []:
        if not isinstance(item, dict) or item.get("tuis_key"):
            continue
        if not tasks.belongs_to(item, data, si):
            continue
        is_exam = tasks.kind_of(item) == "exam"
        if is_exam != want_exam:
            continue
        other = tasks.item_date(item)
        if want_exam:
            if day is None or other is None or day == other:
                return True
            continue
        if day is not None and other == day:
            return True
        if stems and stems & {w[:6] for w in re.findall(r"[а-яёa-z]{4,}", str(item.get("text") or "").lower())}:
            return True
    return False


def seed_events(data: dict, today: date | None = None) -> list[dict]:
    """Добавляет контрольные/зачёты из ТУИС один раз (ключи — в data['tuis_seeded']).
    Удалённое пользователем не возвращается; прошедшие даты и дубли не добавляются."""
    today = today or tu.today()
    seeded = data.get(SEEDED_KEY)
    if not isinstance(seeded, list):
        seeded = data[SEEDED_KEY] = []
    added = []
    for event in events():
        key = event["key"]
        if key in seeded:
            continue
        day = event_date(event)
        if day is not None and day < today:
            continue
        si = _subject_index(data, event.get("subject") or "")
        if si is None:
            continue
        seeded.append(key)
        if is_duplicate(data, event, si):
            continue
        extra: dict = {"src": "tuis", "tuis_key": key}
        kind = event.get("kind") if event.get("kind") in tasks.KINDS else "ctrl"
        if kind == "exam":
            extra["exam_type"] = event.get("exam_type") if event.get("exam_type") in tasks.EXAM_TYPES else "credit"
        added.append(tasks.new_item(data, kind, si, event.get("text") or "", day, **extra))
    return added


def startup(target: Any) -> None:
    data = target.data
    if seed_events(data):
        target.save()
