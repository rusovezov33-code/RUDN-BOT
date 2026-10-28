"""Обходит все экраны через настоящий on_callback с фейковым ботом в строгом
режиме: каждая кнопка ≤64 байт, у каждого префикса есть обработчик, ни одной
ошибки в логах, у каждого экрана есть путь в меню."""
import asyncio
import logging
from collections import deque
from datetime import date, datetime

import pytest

from conftest import LIVE_BACKUP
from fakes import FakeBot, FakeCallback, FakeMessage, buttons, callbacks
from rudn.core import app, home, pending, tasks, timeutil as tu
from rudn.core.registry import REGISTRY
from rudn.core.storage import store
from rudn.core.ui import LIVE_MENU, Ctx


def run(coro):
    return asyncio.run(coro)


def seed(data):
    today = date(2026, 10, 26)
    tasks.new_item(data, "hw", 2, "конспект", today.replace(day=24))
    tasks.new_item(data, "lab", 3, "лаба 1", today)
    tasks.new_item(data, "hw", 4, "задачи", today.replace(day=27))
    tasks.new_item(data, "lab", 5, "лаба 2", today.replace(day=30))
    tasks.new_item(data, "hw", 6, "реферат", date(2026, 11, 20))
    tasks.new_item(data, "hw", None, "без предмета", None)
    tasks.new_item(data, "exam", 7, "", date(2027, 1, 15), exam_type="exam", time="10:00", place="ауд. 1")
    tasks.new_item(data, "exam", 8, "", date(2026, 12, 25), exam_type="credit")
    old = tasks.new_item(data, "hw", 2, "сделано", today)
    tasks.mark_done(old)
    data["subjects"][3]["sections"][0]["files"].append({"file_id": "F1", "name": "лекция.pdf"})
    data["subjects"][3]["sections"][5]["links"].append({"title": "Teams", "url": "https://teams.microsoft.com/x"})
    store.save()


async def crawl(bot, user_id, limit=500):
    seen = set()
    queue = deque(["home"])
    screens = 0
    while queue and screens < limit:
        data = queue.popleft()
        if data in seen:
            continue
        seen.add(data)
        if data.startswith(("rs:yes", "acc:rv", "acc:no")):  # не отключать тестера посреди обхода
            continue
        before = len(bot.calls)
        await app.on_callback(FakeCallback(bot, data, user_id=user_id))
        screens += 1
        shot = bot.last_screen()
        if len(bot.calls) == before or shot is None:
            continue
        cbs = callbacks(shot.markup)
        for cb in cbs:
            REGISTRY.check_callback(cb)
        if cbs:
            assert "home" in cbs or any(c.startswith(("x", "add:")) for c in cbs) or shot.text.startswith("🎓 <b>РУДН"), (
                f"экран {data!r} без выхода в меню"
            )
        for cb in cbs:
            # не уходим бесконечно по датам: только ближайшие недели
            if cb.startswith(("d:", "w:")) and cb[2:12] > "2026-11-20":
                continue
            if cb.startswith("add:cal:") and cb != "add:cal:1":
                continue
            queue.append(cb)
    return seen


@pytest.mark.parametrize("user_id", [111, 222])
def test_crawl_all_screens(use_data, caplog, user_id):
    data = use_data(LIVE_BACKUP)
    seed(data)
    bot = FakeBot()
    caplog.set_level(logging.WARNING)
    seen = run(crawl(bot, user_id))
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, [r.getMessage() for r in errors]
    prefixes = {s.split(":")[0].split("|")[0] for s in seen}
    expected = {"home", "d", "w", "dl", "ex", "subs", "s", "p"}
    expected |= {"ls"}  # уроки: у тестера свой прогресс
    if user_id == 111:
        expected |= {"add", "adm", "acc"}
    assert expected <= prefixes, expected - prefixes
    assert len(seen) > 60


def test_home_text_live(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "home")))
    screen = bot.last_screen()
    assert "Поработать над ИИ." in screen.text
    texts = [btn.text for btn in buttons(screen.markup)]
    assert any("МФЦ" in t for t in texts)
    assert not any("Расписание" in t and "📅" in t and t != "📅 Сегодня" for t in texts if "Дедлайны и экзамены" in t)
    assert not any("Дедлайны и экзамены" in t for t in texts)
    assert any(t.startswith("🔥 Дедлайны") for t in texts) and any(t.startswith("🎓 Экзамены") for t in texts)


def test_live_event_on_day_screen(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "d:2026-10-26")))
    text = bot.last_screen().text
    assert "Поработать над ИИ." in text and "Не забыть что завтра Латинский и Химия" in text
    run(app.on_callback(FakeCallback(bot, "d:2026-10-27")))
    assert "Поработать над ИИ." not in bot.last_screen().text


def test_mfc_page_content_unchanged(use_data):
    data = use_data(LIVE_BACKUP)
    index = next(i for i, p in enumerate(data["pages"]) if "МФЦ" in p["title"])
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, f"p:{index}")))
    screen = bot.last_screen()
    page = data["pages"][index]
    from html import escape

    assert escape(page["text"]) in screen.text
    urls = [btn.url for btn in buttons(screen.markup) if btn.url]
    assert urls == [l["url"] for l in page.get("links") or [] if l.get("url")]
    assert callbacks(screen.markup) == ["home", "home"]


def test_one_tap_done_and_undo(use_data):
    data = use_data(LIVE_BACKUP)
    item = tasks.new_item(data, "lab", 3, "лаба", date(2026, 10, 26))
    store.save()
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "dl")))
    assert f"dl:ok:{item['id']}:a" in callbacks(bot.last_screen().markup)
    run(app.on_callback(FakeCallback(bot, f"dl:ok:{item['id']}:a")))
    assert item["done"] is True
    store._data = None
    assert tasks.find(store.load(), item["id"])["done"] is True
    assert f"dl:un:{item['id']}:a" in callbacks(bot.last_screen().markup)


def test_add_flow_hw_by_lesson_and_typed_text(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "add")))
    assert "add:k:lab" in callbacks(bot.last_screen().markup)
    run(app.on_callback(FakeCallback(bot, "add:k:lab")))
    cbs = callbacks(bot.last_screen().markup)
    chem = next(c for c in cbs if c.startswith("add:su:") and "Хим" in next(b.text for b in buttons(bot.last_screen().markup) if b.callback_data == c))
    run(app.on_callback(FakeCallback(bot, chem)))
    lesson_dates = [c for c in callbacks(bot.last_screen().markup) if c.startswith("add:d:20")]
    assert lesson_dates and lesson_dates[0] == "add:d:2026-10-27"  # Химия во вторник
    run(app.on_callback(FakeCallback(bot, lesson_dates[0])))
    msg = FakeMessage(bot, "Отчёт по лабе №1")
    run(app.on_text(msg))
    item = store.data["homework"][-1]
    assert item["kind"] == "lab" and item["date"] == "2026-10-27" and item["text"] == "Отчёт по лабе №1"
    assert pending.get(111) is None
    assert ("delete", msg.message_id) in [(c.kind, c.kw.get("message_id")) for c in bot.calls]
    # 4 нажатия + текст: Добавить → Лаба → Химия → ВТ 27.10 → текст


def test_add_exam_with_typed_date(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for cb in ["add", "add:k:exam", "add:su:3"]:
        run(app.on_callback(FakeCallback(bot, cb)))
    run(app.on_text(FakeMessage(bot, "15.01")))
    assert "детали" in bot.last_screen().text
    run(app.on_text(FakeMessage(bot, "10:00, ЕГФ ауд. 375")))
    item = store.data["homework"][-1]
    assert item["kind"] == "exam" and item["exam_type"] == "exam"
    assert item["date"] == "2027-01-15" and item["time"] == "10:00" and item["place"] == "ЕГФ ауд. 375"
    run(app.on_callback(FakeCallback(bot, "ex")))
    assert "15.01" in bot.last_screen().text


def test_add_event(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for cb in ["add", "add:k:ev", "add:d:2026-10-28"]:
        run(app.on_callback(FakeCallback(bot, cb)))
    run(app.on_text(FakeMessage(bot, "Купить халат")))
    event = store.data["events"][-1]
    assert event["date"] == "2026-10-28" and event["day"] == 28 and event["month"] == 10 and event["year"] == 2026
    assert set(event) == {"day", "month", "year", "date", "text", "created"}


def test_pending_expires_and_other_button_cancels(use_data, monkeypatch):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for cb in ["add", "add:k:hw", "add:su:2", "add:d:2026-10-28"]:
        run(app.on_callback(FakeCallback(bot, cb)))
    assert pending.get(111) is not None
    run(app.on_callback(FakeCallback(bot, "subs")))
    assert pending.get(111) is None
    for cb in ["add", "add:k:hw", "add:su:2", "add:d:2026-10-28"]:
        run(app.on_callback(FakeCallback(bot, cb)))
    pending._states[111].ts -= pending.TTL_SECONDS + 1
    count = len(store.data["homework"])
    msg = FakeMessage(bot, "поздний текст")
    run(app.on_text(msg))
    assert len(store.data["homework"]) == count
    assert msg.answers and "устарел" in msg.answers[0]


def test_non_owner_cannot_add(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    cb = FakeCallback(bot, "add", user_id=222)
    run(app.on_callback(cb))
    assert cb.answers[0] == ("👀 Режим тестера: изменения недоступны", True)
    run(app.on_text(FakeMessage(bot, "текст", user_id=222)))
    assert store.data["homework"] == []


def test_single_live_menu(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for _ in range(3):
        run(app._command_handler(REGISTRY.commands["start"][1])(FakeMessage(bot, "/start")))
    sends = [c for c in bot.calls if c.kind == "send"]
    deletes = [c.kw["message_id"] for c in bot.calls if c.kind == "delete"]
    # каждое /start удаляет саму команду, новое меню вытесняет старое
    assert len(sends) == 3
    alive = [mid for mid in bot.messages]
    assert len(alive) == 1 and LIVE_MENU[111] == alive[0]
    assert len(deletes) >= 2


def test_unknown_and_stale_callback(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    cb = FakeCallback(bot, "hw_old:1:2")
    run(app.on_callback(cb))
    assert cb.answers[0][0] == "Меню обновилось"
    cb = FakeCallback(bot, "s:999")
    run(app.on_callback(cb))
    assert "не найден" in bot.last_screen().text


def test_sunday_week_and_menu(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 11, 1, 18, 0, tzinfo=tu.MSK))
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "w:now")))
    assert "02.11" in bot.last_screen().text
    run(app.on_callback(FakeCallback(bot, "home")))
    assert bot.last_screen().text
