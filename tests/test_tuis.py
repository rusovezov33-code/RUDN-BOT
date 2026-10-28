"""ТУИС: целостность config/tuis.json, подсчёт баллов, ввод баллов, однократное
добавление контрольных/зачётов без дублей, сохранность в data.json и откат."""
import asyncio
import importlib.util
import json
import subprocess
from datetime import date


from conftest import LIVE_BACKUP
from fakes import FakeBot, FakeCallback, FakeMessage, callbacks
from rudn import tuis
from rudn.core import app, config, tasks, timeutil as tu
from rudn.core.registry import REGISTRY
from rudn.core.storage import store
from rudn.features import subjects as subj


def run(coro):
    return asyncio.run(coro)


def tuis_items(data):
    return [i for i in data["homework"] if i.get("src") == "tuis"]


# ---------------------------------------------------------------- данные
def test_config_integrity():
    names = list(config.subjects().canonical)
    assert len(names) == 15
    keys = set()
    for c in tuis.courses():
        assert c["key"] not in keys
        keys.add(c["key"])
        assert any(tu.subjects_match(c["bot_subject"], n) for n in names), c["bot_subject"]
        total = sum(i["max_total"] for i in c["items"])
        if c["key"] == "phys":
            assert total == 98 and "98" in c["note"]  # расхождение в самом документе
        else:
            assert total == c["total_max"] == 100, c["key"]
        for item in c["items"]:
            assert len(f"sc:e:{c['key']}:{item['key']}".encode()) <= 64
    for e in tuis.events():
        assert any(tu.subjects_match(e["subject"], n) for n in names), e["subject"]
        assert e["kind"] in tasks.KINDS
        if e["date"]:
            date.fromisoformat(e["date"])
    # стоматологическое моделирование — не предмет этого семестра
    assert not any("моделир" in tu.norm_name(c["name"]) for c in tuis.courses())
    assert any(x["course_id"] == 15938 and x["bot_subject"] is None for x in tuis.load()["extra"])


def test_section_blocks():
    assert "Тюкавкина" in tuis.section_block("Химия", "📖 Учебная литература")[0]
    assert tuis.section_block("Химия", "📖 Учебная литература")[1]
    assert "ЛР №5" in tuis.section_block("Химия", "🧪 Практики / лабораторные")[0]
    assert "буфер" in tuis.section_block("Химия", "🗂 Лекции и конспекты")[0]
    brs = tuis.section_block("Латинский язык", "📋 Требования и баллы — БРС")[0]
    assert "3 × 10" in brs and "Зачётная работа" in brs
    assert "09.02" in tuis.section_block("Латинский язык", "🎓 Экзамен / зачёт")[0]
    assert "нет данных" in tuis.section_block("Основы российской государственности", "📋 Требования и баллы — БРС")[0]
    assert tuis.section_block("Физическая культура", "📖 Учебная литература") == ("", [])
    # синонимы: практика → Введение в специальность
    assert {c["key"] for c in tuis.courses_for("Введение в специальность")} == {"intro", "prac"}
    assert tuis.courses_for("бжд")[0]["key"] == "bzh"


def test_parse_and_status():
    assert tuis.parse_score("3", None, 24) == (3, None)
    assert tuis.parse_score("2,5", None, 24) == (2.5, None)
    assert tuis.parse_score("+2", 3, 24) == (5, None)
    assert tuis.parse_score("-1", 3, 24) == (2, None)
    assert tuis.parse_score("-", 3, 24) == (None, None)
    assert tuis.parse_score("25", None, 24)[1]
    assert tuis.parse_score("-5", 3, 24)[1]
    assert tuis.parse_score("abc", None, 24)[1]
    data = {}
    chem = tuis.course("chem")
    tuis.set_score(data, "chem", "labs", 20)
    tuis.set_score(data, "chem", "kr", 15)
    assert tuis.total(data, chem) == (35, 44)
    text = "\n".join(tuis.status_lines(data, chem))
    assert "не хватает <b>16</b>" in text and "65" in text
    tuis.set_score(data, "chem", "ref", 10)
    tuis.set_score(data, "chem", "final", 40)
    text = "\n".join(tuis.status_lines(data, chem))
    assert "пройден" in text and "C —" in text
    tuis.set_score(data, "chem", "labs", None)
    assert "labs" not in data["scores"]["chem"]
    tuis.reset_scores(data, "chem")
    assert "chem" not in data["scores"]


# ---------------------------------------------------------------- добавление событий
def test_seed_once_no_duplicates(use_data):
    data = use_data(LIVE_BACKUP)
    added = tuis.seed_events(data, date(2026, 10, 27))
    keys = {i["tuis_key"] for i in added}
    assert {"chem-final", "chem-credit", "lat-credit", "minf-t2", "org-rk", "bzh-final", "prac-test", "prac-docs"} <= keys
    assert all(i["subject"] for i in added)
    kinds = {i["tuis_key"]: tasks.kind_of(i) for i in added}
    assert kinds["chem-final"] == "exam" and kinds["minf-t2"] == "ctrl" and kinds["chem-ref"] == "hw"
    assert next(i for i in added if i["tuis_key"] == "prac-test")["subject"] == "Введение в специальность"
    # повторный запуск ничего не добавляет
    assert tuis.seed_events(data, date(2026, 10, 27)) == []
    # удалённое пользователем не возвращается
    data["homework"] = [i for i in data["homework"] if i.get("tuis_key") != "org-rk"]
    assert tuis.seed_events(data, date(2026, 10, 27)) == []
    assert len({i["tuis_key"] for i in tuis_items(data)}) == len(tuis_items(data))


def test_seed_skips_past_and_existing(use_data):
    data = use_data(LIVE_BACKUP)
    tasks.new_item(data, "hw", 3, "Тест углеводороды в ТУИС", None)
    tasks.new_item(data, "exam", 3, "", date(2026, 12, 22), exam_type="credit")
    tasks.new_item(data, "lab", 8, "лаба", date(2026, 12, 17))  # мед. информатика, та же дата (нед. 9 от 19.10)
    added = {i["tuis_key"] for i in tuis.seed_events(data, date(2026, 10, 27))}
    assert "chem-test-hc" not in added and "chem-test-alc" in added
    assert "chem-credit" not in added and "chem-final" in added
    assert "minf-t2" not in added and "minf-kr3" in added
    fresh = use_data(LIVE_BACKUP)
    late = {i["tuis_key"] for i in tuis.seed_events(fresh, date(2026, 12, 20))}
    assert "chem-final" not in late and "minf-t2" not in late and "bzh-final" in late
    assert "chem-final" not in fresh["tuis_seeded"]


def test_startup_hook_persists_and_survives_reload(use_data):
    use_data(LIVE_BACKUP)
    app.run_startup_hooks(REGISTRY, store)
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["tuis_seeded"] and any(i.get("src") == "tuis" for i in on_disk["homework"])
    count = len(on_disk["homework"])
    tuis.set_score(store.data, "lat", "kr", 7)
    store.save()
    store._data = None
    data = store.load()
    assert data["scores"] == {"lat": {"kr": 7}}
    app.run_startup_hooks(REGISTRY, store)
    assert len(store.data["homework"]) == count


def test_startup_hook_error_does_not_crash(monkeypatch, use_data, caplog):
    use_data(LIVE_BACKUP)
    monkeypatch.setattr(tuis, "events", lambda: 1 / 0)
    app.run_startup_hooks(REGISTRY, store)
    assert "on_startup scores failed" in caplog.text


# ---------------------------------------------------------------- экраны
def test_score_entry_flow(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "sc:e:chem:labs")))
    run(app.on_text(FakeMessage(bot, "5")))
    run(app.on_callback(FakeCallback(bot, "sc:e:chem:labs")))
    run(app.on_text(FakeMessage(bot, "+1,5")))
    assert json.loads(store.path.read_text("utf-8"))["scores"] == {"chem": {"labs": 6.5}}
    shot = bot.last_screen()
    assert "6,5</b> из 24" in shot.text and "сохранено" in shot.text
    run(app.on_callback(FakeCallback(bot, "sc:e:chem:labs")))
    run(app.on_text(FakeMessage(bot, "99")))
    assert "от 0 до 24" in bot.last_screen().text
    run(app.on_callback(FakeCallback(bot, "sc:v:chem:kr:20")))
    assert store.data["scores"]["chem"] == {"labs": 6.5, "kr": 20}
    run(app.on_callback(FakeCallback(bot, "sc:R:chem")))
    assert "chem" not in store.data["scores"]


def test_non_owner_cannot_edit(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    cb = FakeCallback(bot, "sc:v:chem:kr:20", user_id=222)
    run(app.on_callback(cb))
    assert not store.data.get("scores")
    run(app.on_callback(FakeCallback(bot, "sc:c:chem", user_id=222)))
    assert not any(c.startswith("sc:e") for c in callbacks(bot.last_screen().markup))


def test_subject_card_and_sections(use_data):
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, date(2026, 10, 26))
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "s:3")))
    shot = bot.last_screen()
    assert "📋 БРС: Зачёт с оценкой" in shot.text
    cbs = callbacks(shot.markup)
    assert "sc:c:chem|s:3" in cbs and "s:3:0" in cbs and "s:3:2" in cbs
    run(app.on_callback(FakeCallback(bot, "s:3:2")))
    assert "Из документов ТУИС" in bot.last_screen().text
    run(app.on_callback(FakeCallback(bot, "sc:c:chem|s:3")))
    assert "s:3" in callbacks(bot.last_screen().markup)
    # пустой раздел с данными ТУИС теперь виден, без данных — нет
    sec = data["subjects"][3]["sections"][0]
    assert subj.section_size(sec) == 0 and subj.section_total("Химия", sec) > 0
    assert subj.section_total("Физическая культура", data["subjects"][13]["sections"][0]) == 0


def test_deadlines_and_exams_show_tuis(use_data):
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, date(2026, 10, 26))
    store.save()
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "dl")))
    text = bot.last_screen().text
    assert "✍️" in text and "Рубежный контроль ОРГ" in text and "Табличный процессор" in text
    run(app.on_callback(FakeCallback(bot, "ex")))
    text = bot.last_screen().text
    assert "Итоговая контрольная работа" in text and "21.12" in text and "Медицинская информатика" in text


def test_stable_v1_reads_file_with_tuis(use_data, tmp_path, monkeypatch):
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, date(2026, 10, 26))
    tuis.set_score(data, "chem", "kr", 15)
    store.save()
    written = json.loads(store.path.read_text(encoding="utf-8"))
    old_src = subprocess.run(["git", "show", "stable-v1:bot.py"], capture_output=True, text=True, cwd=config.ROOT).stdout
    old_path = tmp_path / "old_bot.py"
    old_path.write_text(old_src, encoding="utf-8")
    monkeypatch.setenv("DATA_FILE", str(store.path))
    spec = importlib.util.spec_from_file_location("old_bot_v1_tuis", old_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.load()["homework"] == written["homework"]
