"""Откат: старый bot.py (stable-v1) должен читать файл, записанный v2, без потерь."""
import importlib.util
import json
import subprocess
from datetime import date

from conftest import LIVE_BACKUP
from rudn.core import config, tasks
from rudn.core.storage import store


def test_stable_v1_reads_v2_file(use_data, tmp_path, monkeypatch):
    data = use_data(LIVE_BACKUP)
    tasks.new_item(data, "lab", 3, "лаба", date(2026, 10, 28))
    tasks.new_item(data, "exam", 7, "", date(2027, 1, 15), exam_type="credit", time="10:00")
    tasks.new_item(data, "hw", 2, "дз", date(2026, 10, 27))
    store.save()
    written = json.loads(store.path.read_text(encoding="utf-8"))

    old_src = subprocess.run(["git", "show", "stable-v1:bot.py"], capture_output=True, text=True, cwd=config.ROOT).stdout
    old_path = tmp_path / "old_bot.py"
    old_path.write_text(old_src, encoding="utf-8")
    monkeypatch.setenv("DATA_FILE", str(store.path))
    spec = importlib.util.spec_from_file_location("old_bot_v1", old_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = module.load()
    assert old["homework"] == written["homework"]
    assert old["events"] == written["events"]
    assert old["pages"] == written["pages"]
    assert [s["name"] for s in old["subjects"]] == [s["name"] for s in written["subjects"]]
