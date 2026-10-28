"""Повторная проверка 389d4bd: возврат «съеденных» пунктов ТУИС (Латынь КР2),
«Зачёт — Зачёт», «без срока», экран пункта с удалением, скорость ответа, генератор tuis.json."""
import asyncio
import json
import time
from datetime import date

from conftest import LIVE_BACKUP
from fakes import FakeBot, FakeCallback, FakeMessage, callbacks
from rudn import tuis
from rudn.core import app, config, tasks
from rudn.core.storage import store

TODAY = date(2026, 10, 27)


def run(coro):
    return asyncio.run(coro)


def latin(data):
    return tuis._subject_index(data, "Латинский язык")


def old_seed_state(data):
    """Как было после 3fd0cb2: всё засеяно, но КР2/КР3 по латыни (тогда без даты)
    не добавились — старое правило дублей сочло их дублем обычного д/з по латыни."""
    si = latin(data)
    mine = tasks.new_item(data, "hw", si, "Повторить склонения", date(2026, 11, 3))
    event = next(e for e in tuis.events() if e["key"] == "lat-kr2")
    old_text = event["prev"][0]["text"]
    assert tuis._find_duplicate(data, si, None, False, tuis._stems(old_text), legacy=True) is mine
    tuis.seed_events(data, TODAY)
    for item in [i for i in data["homework"] if i.get("tuis_key") in ("lat-kr2", "lat-kr3")]:
        data["homework"].remove(item)
    assert {"lat-kr2", "lat-kr3"} <= set(data["tuis_seeded"])
    return mine


def keys(data):
    return {i.get("tuis_key") for i in data["homework"]}


def test_lat_kr2_restored_with_new_date(use_data):
    data = use_data(LIVE_BACKUP)
    mine = old_seed_state(data)
    # новое правило: даты есть и не совпадают → не дубль
    assert not tuis.is_duplicate(data, next(e for e in tuis.events() if e["key"] == "lat-kr2"), latin(data))
    added = {i["tuis_key"]: i for i in tuis.restore_suppressed(data, TODAY)}
    assert set(added) == {"lat-kr2", "lat-kr3"}
    kr2 = added["lat-kr2"]
    assert kr2["date"] == "2026-12-15" and kr2["date_hint"] == "week" and kr2["kind"] == "ctrl"
    assert tasks.due_text(kr2, TODAY).endswith("15.12 ≈")
    assert added["lat-kr3"]["date"] == "2027-01-26"
    assert mine in data["homework"]
    # однократно; обычный посев не дублирует
    assert tuis.restore_suppressed(data, TODAY) == [] and tuis.seed_events(data, TODAY) == []
    assert sum(1 for i in data["homework"] if i.get("tuis_key") == "lat-kr2") == 1


def test_lat_kr2_visible_in_deadlines_and_latin_card(use_data):
    data = use_data(LIVE_BACKUP)
    old_seed_state(data)
    tuis.startup(store)
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "dl")))
    assert "КР2 (анатомическая терминология" in bot.last_screen().text
    run(app.on_callback(FakeCallback(bot, f"s:{latin(data)}")))
    assert "КР2" in bot.last_screen().text and "15.12 ≈" in bot.last_screen().text


def test_user_deleted_items_never_restored(use_data):
    data = use_data(LIVE_BACKUP)
    old_seed_state(data)
    data[tasks.DELETED_KEY] = ["lat-kr2"]
    added = {i["tuis_key"] for i in tuis.restore_suppressed(data, TODAY)}
    assert added == {"lat-kr3"}
    # удаление через экран пункта запоминается
    item = next(i for i in data["homework"] if i.get("tuis_key") == "lat-kr3")
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, f"it:X:{item['id']}:a")))
    assert "lat-kr3" in data[tasks.DELETED_KEY]
    assert tuis.restore_suppressed(data, TODAY) == [] and tuis.seed_events(data, TODAY) == []
    assert "lat-kr3" not in keys(data)


def test_dated_events_not_restored_when_user_deleted_without_trace(use_data):
    """Пункт с датой (prev с датой), который старое правило не считало дублем, —
    значит, его удалил пользователь: не возвращаем."""
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, TODAY)
    gone = next(i for i in data["homework"] if i.get("tuis_key") == "minf-kr3")
    data["homework"].remove(gone)
    assert "minf-kr3" not in {i["tuis_key"] for i in tuis.restore_suppressed(data, TODAY)}


def test_all_redated_events_present_after_startup(use_data):
    data = use_data(LIVE_BACKUP)
    old_seed_state(data)
    tuis.startup(store)
    for event in tuis.events():
        day = tuis.event_date(event)
        if event.get("date_basis") == "week" and day and day >= TODAY:
            assert event["key"] in keys(data), event["key"]


def test_no_doubled_kind_prefix_anywhere(use_data):
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, TODAY)
    lat = next(i for i in data["homework"] if i.get("tuis_key") == "lat-credit")
    assert tasks.body_text(lat).startswith("письменная работа")
    assert tasks.titled_text(lat).startswith("Зачёт — письменная работа")
    assert tasks.body_text({"text": "Зачётное занятие", "kind": "exam", "exam_type": "credit"}) == "Зачётное занятие"
    bot = FakeBot()
    screens = ["ex", "dl"] + [f"s:{i}" for i in range(len(data["subjects"]))]
    screens += [f"d:{d}" for d in ("2027-02-09", "2026-12-22", "2026-12-15")]
    for cb in screens:
        run(app.on_callback(FakeCallback(bot, cb)))
        text = bot.last_screen().text
        for label in set(tasks.EXAM_TYPES.values()) | {"Экзамен", "Зачёт"}:
            for sep in (" — ", ": "):
                assert f"{label}{sep}{label}" not in text, (cb, label)  # и «Зачёт — Зачётное …»


def test_chem_undated_tests_say_bez_sroka(use_data):
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, TODAY)
    for key in ("chem-test-hc", "chem-test-alc"):
        item = next(i for i in data["homework"] if i.get("tuis_key") == key)
        assert tasks.due_text(item, TODAY) == "без срока"
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "dl")))
    text = bot.last_screen().text
    assert "Без даты" in text
    line = next(l for l in text.split("\n") if "Углеводороды" in l)
    assert line.startswith("• <b>без срока</b>") and "⚠️" not in line and "≈" not in line


def test_item_screen_edit_and_delete_with_confirm(use_data):
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, TODAY)
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "dl")))
    shot = bot.last_screen()
    assert "✏️ — открыть" in shot.text
    opens = [c for c in callbacks(shot.markup) if c.startswith("it:o:")]
    oks = [c for c in callbacks(shot.markup) if c.startswith("dl:ok:")]
    assert opens and len(opens) == len(oks)  # одна строка: ✅ + ✏️
    item_id = int(opens[0].split(":")[2])
    run(app.on_callback(FakeCallback(bot, opens[0])))
    cbs = callbacks(bot.last_screen().markup)
    assert {f"it:d:{item_id}:a", f"it:t:{item_id}:a", f"it:w:{item_id}:a", f"it:x:{item_id}:a"} <= set(cbs)
    # дата через ввод текста
    run(app.on_callback(FakeCallback(bot, f"it:w:{item_id}:a")))
    run(app.on_text(FakeMessage(bot, "20.12")))
    item = tasks.find(data, item_id)
    assert item["date"] == "2026-12-20" and "date_hint" not in item
    assert "Срок изменён" in bot.last_screen().text
    # текст
    run(app.on_callback(FakeCallback(bot, f"it:t:{item_id}:a")))
    run(app.on_text(FakeMessage(bot, "мой текст")))
    assert tasks.find(data, item_id)["text"] == "мой текст"
    # удаление: сначала подтверждение, без него ничего не удаляется
    run(app.on_callback(FakeCallback(bot, f"it:x:{item_id}:a")))
    assert "Удалить насовсем?" in bot.last_screen().text and tasks.find(data, item_id) is not None
    cb = FakeCallback(bot, f"it:X:{item_id}:a")
    run(app.on_callback(cb))
    assert tasks.find(data, item_id) is None and cb.answers == [("🗑 Удалено", False)]
    assert bot.last_screen().text.startswith("🔥 <b>Дедлайны")
    saved = json.loads(store.path.read_text("utf-8"))
    assert all(i.get("id") != item_id for i in saved["homework"])


def test_exam_list_has_open_button(use_data):
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, TODAY)
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "ex")))
    cbs = callbacks(bot.last_screen().markup)
    opens = [c for c in cbs if c.startswith("it:o:") and c.endswith(":e")]
    assert opens and len(opens) == len([c for c in cbs if c.startswith("ex:ok:")])
    item_id = int(opens[0].split(":")[2])
    run(app.on_callback(FakeCallback(bot, f"it:d:{item_id}:e")))
    assert tasks.find(data, item_id)["done"] is True
    assert bot.last_screen().text.startswith("🎓")


def test_score_click_answers_with_toast_and_saves_after_screen(use_data, monkeypatch):
    data = use_data(LIVE_BACKUP)
    course = tuis.courses()[0]
    item = course["items"][0]
    order = []
    real_save = store.save_async

    async def tracked():
        order.append("save")
        await real_save()

    monkeypatch.setattr(store, "save_async", tracked)
    monkeypatch.setattr(store, "save", lambda: order.append("sync-save"))
    bot = FakeBot()
    real_edit = bot.edit_message_text

    async def edit(*a, **kw):
        order.append("edit")
        await real_edit(*a, **kw)

    bot.edit_message_text = edit
    cb = FakeCallback(bot, f"sc:v:{course['key']}:{item['key']}:p1")
    started = time.monotonic()
    run(app.on_callback(cb))
    assert time.monotonic() - started < 1
    assert cb.answers == [("✅ Сохранено", False)]  # один ответ, вместе с экраном
    assert order == ["edit", "save"]  # запись файла — после показа, без синхронной записи
    assert json.loads(store.path.read_text("utf-8"))["scores"][course["key"]][item["key"]] == 1


def test_week_screen_fast():
    bot = FakeBot()
    started = time.monotonic()
    for _ in range(5):
        run(app.on_callback(FakeCallback(bot, "w")))
    assert (time.monotonic() - started) / 5 < 0.2


def test_build_tool_reproduces_config():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_tuis", config.CONFIG_DIR.parent / "tools" / "build_tuis.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build() == (config.CONFIG_DIR / "tuis.json").read_text("utf-8")
    assert json.loads(module.build())["term_start"] == tuis.term_start().isoformat()


def test_slow_api_logged(caplog):
    class SendMessage:
        pass

    async def slow(bot, method):
        app.time.sleep(0)  # без реального ожидания: подменяем порог
        return "ok"

    app.SLOW_API_SECONDS, old = 0.0, app.SLOW_API_SECONDS
    try:
        with caplog.at_level("WARNING"):
            assert run(app.log_slow_api(slow, None, SendMessage())) == "ok"
    finally:
        app.SLOW_API_SECONDS = old
    assert "slow telegram SendMessage" in caplog.text
