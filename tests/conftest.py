import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["RUDN_STRICT"] = "1"
os.environ["OWNER_ID"] = "111"
os.environ.pop("BOT_TOKEN", None)
for key in ("PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL"):
    os.environ.pop(key, None)

from rudn.core import access, pending, timeutil  # noqa: E402
from rudn.core.app import build_registry  # noqa: E402
from rudn.core.storage import store  # noqa: E402
from rudn.core.ui import LIVE_MENU  # noqa: E402

build_registry()

LIVE_BACKUP = Path("/workspace/tgbot/backups/data-live-2026-10-26-1600.json")
RUDN_BOT = Path("/workspace/tgbot/rudn-bot/data.json")
REPO_DATA = ROOT / "data.json"

LEGACY = {
    "pages": [{"title": "🏛 МФЦ / деканат", "text": "Окна 1-5", "links": [{"title": "Сайт", "url": "https://rudn.ru"}]}],
    "subjects": [
        {"name": "Основы военной подготовки", "teacher": "Иванов", "description": "", "sections": [
            {"title": "📚 Материалы", "text": "", "files": ["BQACAgIAAxkBAAIB", {"file_id": "X2", "name": "lec.pdf"}], "links": []}
        ]},
        {"name": "Экономика", "sections": []},
        {"name": "Химия"},
    ],
    "events": [{"day": 26, "text": "без месяца"}, {"day": 27, "month": 10, "text": "только месяц"}],
    "homework": [
        {"subject_idx": 0, "text": "старое без id", "day": 28, "done": False},
        {"subject_idx": 2, "text": "второе без id", "day": 3, "month": 11, "year": 2026, "done": False},
        {"id": 5, "subject": "Химия", "text": "дубль", "done": True},
        {"id": 5, "subject": "Химия", "text": "дубль-2", "done": False},
    ],
}

SOURCES = {"live": LIVE_BACKUP, "rudn_bot": RUDN_BOT, "repo": REPO_DATA}


TESTER_ID = 222  # в старых тестах «не владелец» — это одобренный тестер
STRANGER_ID = 333  # чужой: бот ему ничего не показывает


def reset_state():
    access.ACCESS.reset()
    pending._states.clear()
    LIVE_MENU.clear()
    store._data = None
    store._path = None


@pytest.fixture(autouse=True)
def frozen(monkeypatch, tmp_path):
    reset_state()
    timeutil.set_now(datetime(2026, 10, 26, 12, 0, tzinfo=timeutil.MSK))
    monkeypatch.setenv("DATA_FILE", str(tmp_path / "data.json"))
    access.ACCESS.data["testers"][str(TESTER_ID)] = {"name": "Тестер", "username": None, "added": "2026-10-26 12:00"}
    yield
    timeutil.set_now(None)
    reset_state()


@pytest.fixture
def use_data(tmp_path):
    """Кладёт копию источника во временный DATA_FILE и загружает его."""

    def _use(source):
        target = tmp_path / "data.json"
        if isinstance(source, Path):
            shutil.copy(source, target)
        else:
            target.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        store._data = None
        return store.load()

    return _use
