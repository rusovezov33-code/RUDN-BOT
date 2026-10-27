"""Группа позднего заезда: неделя 1 = term_start (19.10.2026); пересчёт дат по неделям,
пометка дат основного потока и обновление уже добавленных из ТУИС пунктов."""
import json
from datetime import date

from conftest import LIVE_BACKUP
from rudn import tuis
from rudn.core import config, tasks


def ev(key):
    return next(e for e in tuis.events() if e["key"] == key)


def test_term_start_single_value():
    raw = json.loads((config.CONFIG_DIR / "tuis.json").read_text("utf-8"))
    assert raw["term_start"] == "2026-10-19"
    assert tuis.term_start() == date(2026, 10, 19)
    assert tuis.week_date(1, 0) == date(2026, 10, 19)
    assert tuis.week_of(date(2026, 10, 27)) == 2


def test_week_based_dates_from_term_start():
    assert tuis.event_date(ev("lat-kr2")) == date(2026, 12, 15)  # нед. 9, вторник — не 27.10
    assert tuis.event_date(ev("lat-kr3")) == date(2027, 1, 26)
    assert tuis.event_date(ev("lat-credit")) == date(2027, 2, 9)
    assert tuis.event_date(ev("minf-t2")) == date(2026, 12, 17)  # нед. 9, четверг
    assert tuis.event_date(ev("minf-kr4")) == date(2027, 1, 21)
    assert "27.10" not in ev("lat-kr2")["text"]


def test_regular_stream_dates_marked_and_tuis_dates_kept():
    for key in ["chem-ref", "chem-retake", "chem-final", "chem-credit"]:
        assert tuis.REGULAR_WARNING in tuis.event_text(ev(key)), key
    for key, want in [("bzh-final", date(2026, 12, 21)), ("prac-test", date(2026, 12, 18)),
                      ("prac-docs", date(2026, 12, 25)), ("org-rk", date(2026, 12, 31))]:
        assert tuis.event_date(ev(key)) == want and tuis.REGULAR_WARNING not in tuis.event_text(ev(key))


def test_plans_follow_term_start():
    text = tuis.labs_text("Латинский язык", date(2026, 10, 27))
    assert "нед. 1, ВТ 20.10" in text or "нед. 1, 20.10" in text
    assert "👉 нед. 2" in text
    chem = tuis.labs_text("Химия", date(2026, 10, 27))
    assert "основного потока" in chem and "👉" not in chem


def _old_seeded(data):
    """Как засеял коммит 3fd0cb2: старые даты/тексты из event.prev."""
    items = {}
    for event in tuis.events():
        old = (event.get("prev") or [{"date": event.get("date"), "text": event["text"]}])[0]
        si = tuis._subject_index(data, event["subject"])
        day = date.fromisoformat(old["date"]) if old.get("date") else None
        extra = {"src": "tuis", "tuis_key": event["key"]}
        if event["kind"] == "exam":
            extra["exam_type"] = event["exam_type"]
        items[event["key"]] = tasks.new_item(data, event["kind"], si, old["text"], day, **extra)
        data.setdefault("tuis_seeded", []).append(event["key"])
    return items


def test_reseed_updates_old_items_without_touching_user_changes(use_data):
    data = use_data(LIVE_BACKUP)
    items = _old_seeded(data)
    mine = tasks.new_item(data, "ctrl", 2, "моя КР", date(2026, 10, 27))
    tasks.mark_done(items["minf-t2"])
    items["minf-kr3"]["text"] = "КР3 — перенесли, сказали на паре"  # пользователь поправил
    data["homework"].remove(items["minf-kr4"])  # пользователь удалил
    count = len(data["homework"])
    tuis.update_seeded(data)
    tuis.seed_events(data, date(2026, 10, 27))
    assert len(data["homework"]) == count  # ничего не воскресло, дублей нет
    assert items["lat-kr2"]["date"] == "2026-12-15" and items["lat-kr2"]["date_hint"] == "week"
    assert items["minf-t2"]["date"] == "2026-12-17" and items["minf-t2"]["done"] is True
    assert items["minf-kr3"]["date"] == "2026-11-12"  # правку пользователя не трогаем
    assert items["chem-final"]["date"] == "2026-12-15" and tuis.REGULAR_WARNING in items["chem-final"]["text"]
    assert items["bzh-final"]["date"] == "2026-12-21" and "date_hint" not in items["bzh-final"]
    assert mine["date"] == "2026-10-27" and mine["text"] == "моя КР"
    assert not any(i.get("tuis_key") == "minf-kr4" for i in data["homework"])
    # повторный запуск ничего не меняет
    assert tuis.update_seeded(data) == []
    assert tasks.due_text(items["chem-final"], date(2026, 10, 27)).endswith("⚠️")
    assert tasks.due_text(items["lat-kr2"], date(2026, 10, 27)).endswith("≈")


def test_early_weeks_now_upcoming_are_seeded(use_data):
    """Нед. 4/5/8 Мед. информатики у основного потока уже прошли, у нашей группы — впереди."""
    data = use_data(LIVE_BACKUP)
    data["tuis_seeded"] = [e["key"] for e in tuis.events() if not e["key"].startswith("minf-")]
    added = {i["tuis_key"]: i["date"] for i in tuis.seed_events(data, date(2026, 10, 27))}
    assert added["minf-kr1"] == "2026-11-12" and added["minf-t1"] == "2026-11-19"
    assert added["minf-kr2"] == "2026-12-10" and added["minf-t2"] == "2026-12-17"
