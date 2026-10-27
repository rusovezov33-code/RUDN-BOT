import json
from datetime import date

import pytest

from conftest import LEGACY, LIVE_BACKUP, SOURCES
from rudn.core import storage, tasks
from rudn.core.storage import Store, normalize


@pytest.mark.parametrize("name", list(SOURCES))
def test_sources_load_without_loss(name, use_data):
    source = SOURCES[name]
    if not source.exists():
        pytest.skip(f"нет файла {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    data = use_data(source)
    assert len(data["subjects"]) == 15
    assert len(data["pages"]) == len(raw.get("pages") or [])
    assert data["pages"] == (raw.get("pages") or [])
    assert len(data["events"]) == len(raw.get("events") or [])
    assert len(data["homework"]) == len(raw.get("homework") or [])
    raw_files = sum(len(sec.get("files") or []) for s in raw["subjects"] for sec in s.get("sections") or [])
    files = sum(len(sec.get("files") or []) for s in data["subjects"] for sec in s.get("sections") or [])
    assert files == raw_files


def test_live_backup_exact(use_data):
    data = use_data(LIVE_BACKUP)
    assert [p["title"] for p in data["pages"]][0].startswith("🏛")
    event = data["events"][0]
    assert event["date"] == "2026-10-26" and event["text"].startswith("Поработать над ИИ.")
    assert tasks.events_on(data, date(2026, 10, 26)) == [event]
    assert tasks.events_on(data, date(2026, 10, 27)) == []


def test_legacy_synthetic(use_data):
    data = use_data(LEGACY)
    names = [s["name"] for s in data["subjects"]]
    assert "Экономика" in names  # есть в официальном расписании (СБ, верхняя)
    assert any("БЖД" in n for n in names)
    ids = [item["id"] for item in data["homework"]]
    assert len(set(ids)) == len(ids) == 4
    assert all(isinstance(i, int) for i in ids)
    oborona = next(s for s in data["subjects"] if "БЖД" in s["name"])
    assert len(oborona["sections"][0]["files"]) == 2
    # старые события без года/месяца
    assert len(tasks.events_on(data, date(2026, 10, 26))) == 1
    assert len(tasks.events_on(data, date(2026, 10, 27))) == 1
    # задание без явного месяца: открыто и попадает в дедлайны (kind по умолчанию hw)
    assert all(tasks.kind_of(i) == "hw" for i in data["homework"])
    assert len(tasks.open_items(data)) == 3


@pytest.mark.parametrize("name", list(SOURCES))
def test_roundtrip(name, use_data, tmp_path):
    source = SOURCES[name]
    if not source.exists():
        pytest.skip()
    data = use_data(source)
    tasks.new_item(data, "lab", 2, "лаба", date(2026, 10, 28))
    tasks.new_item(data, "exam", 3, "", date(2027, 1, 15), exam_type="credit", time="10:00", place="ауд. 1")
    storage.store.save()
    again = Store(tmp_path / "data.json").load()
    assert again == json.loads(json.dumps(data))
    assert normalize(json.loads(json.dumps(again))) == again
    kinds = [tasks.kind_of(i) for i in again["homework"]][-2:]
    assert kinds == ["lab", "exam"]
    # hw не пишет поле kind — формат прежний
    hw = tasks.new_item(again, "hw", 0, "x", None)
    assert "kind" not in hw


def test_corrupt_file_is_kept(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("{broken", encoding="utf-8")
    data = Store(path).load()
    assert len(data["subjects"]) == 15
    assert list(tmp_path.glob("data.json.corrupt-*"))


def test_atomic_write_leaves_no_tmp(tmp_path):
    storage.atomic_write(tmp_path / "a.json", "{}")
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]
