"""Доступ тестера: запрос от чужого → кнопки владельцу → тестер видит всё,
ничего не меняет (data.json владельца — байт в байт), уроки у него свои."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from conftest import LIVE_BACKUP, STRANGER_ID, TESTER_ID
from fakes import FakeBot, FakeCallback, FakeMessage, buttons, callbacks, fake_document
from rudn import lessons as content
from rudn.core import access, app, config, pending, testers
from rudn.core.registry import REGISTRY
from rudn.core.storage import encode, store
from rudn.core.ui import LIVE_MENU
from rudn.features import admin
from rudn.features import lessons as feat
from test_screens import crawl, seed

OWNER = 111
DENIED = "👀 Режим тестера: изменения недоступны"


def run(coro):
    return asyncio.run(coro)


def press(bot, data, user_id=OWNER, message_id=None):
    cb = FakeCallback(bot, data, user_id=user_id, message_id=message_id or LIVE_MENU.get(user_id))
    run(app.on_callback(cb))
    return cb


def person(uid, username=None, first="Nazli", last=None):
    return SimpleNamespace(id=uid, username=username, first_name=first, last_name=last)


def message(bot, text, user):
    msg = FakeMessage(bot, text, user_id=user.id)
    msg.from_user = user
    return msg


def command(bot, name, user):
    spec = REGISTRY.commands[name][1]
    msg = message(bot, f"/{name}", user)
    run(app._command_handler(spec)(msg))
    return msg


def say(bot, text, user_id):
    msg = FakeMessage(bot, text, user_id=user_id)
    run(app.on_text(msg))
    return msg


def sends(bot, since=0):
    return [c for c in bot.calls[since:] if c.kind == "send"]


def data_bytes():
    return store.path.read_bytes()


# ---------------------------------------------------------------- чужой → запрос
def test_stranger_start_sends_one_request(use_data, monkeypatch):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    nazli = person(STRANGER_ID, "nazli_a", "Nazli", "A.")
    command(bot, "start", nazli)
    sent = sends(bot)
    assert len(sent) == 2
    request, reply = sent
    assert "Запрос на доступ" in request.text and "Nazli A." in request.text
    assert "@nazli_a" in request.text and f"<code>{STRANGER_ID}</code>" in request.text
    assert [(b.text, b.callback_data) for b in buttons(request.markup)] == [
        ("✅ Пустить как тестера", f"acc:ok:{STRANGER_ID}"),
        ("❌ Отказать", f"acc:no:{STRANGER_ID}"),
    ]
    assert reply.text.startswith("📨 Запрос на доступ отправлен владельцу")
    assert access.role(STRANGER_ID) is None
    # спам /start — тишина; через минуту — одно напоминание
    for _ in range(5):
        command(bot, "start", nazli)
    assert len(sends(bot)) == 2
    real = time.time
    monkeypatch.setattr(access.time, "time", lambda: real() + 61)
    command(bot, "start", nazli)
    assert [c.text for c in sends(bot)[2:]] == ["⏳ Запрос уже отправлен владельцу — дождись ответа."]
    assert len(access.ACCESS.data["requests"]) == 1


def test_stranger_sees_nothing(use_data):
    use_data(LIVE_BACKUP)
    before = data_bytes()
    bot = FakeBot()
    stranger = person(STRANGER_ID)
    for name in REGISTRY.commands:
        if name != "start":
            command(bot, name, stranger)
    run(app.on_text(message(bot, "привет", stranger)))
    run(app.on_unknown_command(message(bot, "/whatever", stranger)))
    for data in ["home", "d:t", "dl", "subs", "s:3", "sc", "ls", "add", "adm:bk", "acc:ok:333"]:
        cb = press(bot, data, user_id=STRANGER_ID)
        assert cb.answers == [(None, False)]
    assert bot.calls == []
    assert access.ACCESS.data["requests"] == {} and access.role(STRANGER_ID) is None
    assert data_bytes() == before


def test_request_flood_is_capped(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for uid in range(5000, 5000 + access.MAX_OPEN_REQUESTS + 5):
        command(bot, "start", person(uid))
    owner_msgs = [c for c in sends(bot) if "Запрос на доступ к боту" in c.text]
    assert len(owner_msgs) == access.MAX_OPEN_REQUESTS


# ---------------------------------------------------------------- одобрить / отказать / отключить
def test_approve_greets_tester(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    command(bot, "start", person(STRANGER_ID, "nazli_a"))
    request_id = bot.calls[0].kw.get("message_id") or 1
    cb = press(bot, f"acc:ok:{STRANGER_ID}", message_id=request_id)
    assert cb.answers[-1] == ("✅ Пустил как тестера", False)
    edit = next(c for c in bot.calls if c.kind == "edit")
    assert edit.text.startswith("✅ Тестер: Nazli (@nazli_a)")
    greet = sends(bot)[-1]
    assert "Доступ открыт" in greet.text and "Режим тестера: только просмотр" in greet.text
    assert "subs" in callbacks(greet.markup)
    assert access.role(STRANGER_ID) == access.TESTER
    saved = json.loads(access.ACCESS.path.read_text(encoding="utf-8"))
    assert str(STRANGER_ID) in saved["testers"] and saved["requests"] == {}
    # повторное нажатие — без второго приветствия
    count = len(sends(bot))
    press(bot, f"acc:ok:{STRANGER_ID}", message_id=request_id)
    assert len(sends(bot)) == count
    # живое меню владельца не тронуто правкой запроса
    assert LIVE_MENU.get(OWNER) != request_id


def test_deny_and_cooldown(use_data, monkeypatch):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    command(bot, "start", person(STRANGER_ID))
    press(bot, f"acc:no:{STRANGER_ID}", message_id=42)
    assert any(c.kind == "edit" and c.text.startswith("❌ Отказано") for c in bot.calls)
    assert access.role(STRANGER_ID) is None
    count = len(bot.calls)
    command(bot, "start", person(STRANGER_ID))
    assert len(bot.calls) == count  # сутки — тишина
    cb = press(bot, f"acc:no:{STRANGER_ID}", message_id=42)
    assert cb.answers[0] == ("Запрос уже обработан", True)
    real = time.time
    monkeypatch.setattr(access.time, "time", lambda: real() + access.DENY_COOLDOWN_SECONDS + 1)
    command(bot, "start", person(STRANGER_ID))
    assert "Запрос на доступ к боту" in sends(bot, count)[0].text


def test_testers_screen_and_revoke(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    command(bot, "testers", person(OWNER))
    shot = bot.last_screen()
    assert "👥 <b>Тестеры</b>" in shot.text and "Тестер" in shot.text
    assert ("🚫 Отключить Тестер", f"acc:rv:{TESTER_ID}:l") in [(b.text, b.callback_data) for b in buttons(shot.markup)]
    press(bot, "adm")
    assert "acc:l" in callbacks(bot.last_screen().markup)
    pending.start(TESTER_ID, feat.STATE, id="lat-01", n=0)
    press(bot, f"acc:rv:{TESTER_ID}:l")
    assert access.role(TESTER_ID) is None and pending.get(TESTER_ID) is None
    assert "Тестеров пока нет." in bot.last_screen().text
    count = len(bot.calls)
    cb = press(bot, "home", user_id=TESTER_ID)
    assert cb.answers == [(None, False)] and len(bot.calls) == count


def test_tester_cannot_manage_access(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    command(bot, "start", person(STRANGER_ID))
    for data in [f"acc:ok:{STRANGER_ID}", "acc:l", f"acc:rv:{TESTER_ID}"]:
        cb = press(bot, data, user_id=TESTER_ID)
        assert cb.answers == [(DENIED, True)]
    assert access.role(STRANGER_ID) is None and access.role(TESTER_ID) == access.TESTER
    command(bot, "testers", person(TESTER_ID))
    assert DENIED in bot.last_screen().text


def test_preallow_config(use_data, monkeypatch, tmp_path):
    use_data(LIVE_BACKUP)
    assert json.loads((config.CONFIG_DIR / "testers.json").read_text()) == {"ids": [], "usernames": []}
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "testers.json").write_text(json.dumps({"ids": [444], "usernames": ["@Nazli_A"]}))
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    assert access.role(444) == access.TESTER
    assert access.role(555, "nazli_a") == access.TESTER
    assert access.role(555, "other") is None
    access.revoke(444)
    assert access.role(444) is None


# ---------------------------------------------------------------- тестер: чтение
def test_tester_reads_every_section(use_data):
    data = use_data(LIVE_BACKUP)
    seed(data)
    bot = FakeBot()
    press(bot, "home", user_id=TESTER_ID)
    home_cbs = callbacks(bot.last_screen().markup)
    assert "add" not in home_cbs and "adm" not in home_cbs
    assert home_cbs == ["d:t", "dl", "subs", "ls", "nt", "mo"]
    key = next(c for c in callbacks((press(bot, "sc", user_id=TESTER_ID), bot.last_screen())[1].markup) if c.startswith("sc:c:"))
    for data_ in ["d:t", "d:tm", "w:now", "dl", "dl:h", "ex", "ex:p", "subs", "s:3", "s:3:0", key, "p:0", "ls", "ls:A"]:
        cb = press(bot, data_, user_id=TESTER_ID)
        assert DENIED not in [a[0] for a in cb.answers], data_
        shot = bot.last_screen()
        cbs = callbacks(shot.markup)
        assert not any(c.startswith(("dl:ok", "dl:rm", "ex:ok", "it:", "sc:e", "sc:r", "add", "evd")) for c in cbs), (data_, cbs)
    # книга / файл: бот присылает документ в её чат
    press(bot, "s:3:0", user_id=TESTER_ID)
    file_cb = next(c for c in callbacks(bot.last_screen().markup) if c.startswith("file:"))
    press(bot, file_cb, user_id=TESTER_ID)
    doc = [c for c in bot.calls if c.kind == "document"][-1]
    assert doc.kw["document"] == "F1"


def test_tester_gets_signed_file_links(use_data, monkeypatch):
    data = use_data(LIVE_BACKUP)
    seed(data)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bot.example")
    bot = FakeBot()
    press(bot, "s:3:0", user_id=TESTER_ID)
    urls = [b.url for b in buttons(bot.last_screen().markup) if b.url and "/dl/" in b.url]
    assert urls and urls[0].startswith("https://bot.example/dl/")


# ---------------------------------------------------------------- тестер: все изменения запрещены
def _owner_callbacks():
    bot = FakeBot()
    seen = run(crawl(bot, OWNER, limit=800))
    found = set(seen)
    for call in bot.calls:
        found.update(callbacks(call.markup))
    return sorted(found)


def test_every_registered_prefix_has_tester_rule():
    assert set(REGISTRY.callbacks) <= set(access.TESTER_READ), set(REGISTRY.callbacks) - set(access.TESTER_READ)
    assert set(access.TESTER_COMMANDS) <= set(REGISTRY.commands)
    assert set(access.TESTER_TEXT_STATES) <= set(REGISTRY.text_states)


def test_every_mutating_callback_is_blocked_for_tester(use_data, tmp_path):
    data = use_data(LIVE_BACKUP)
    seed(data)
    pristine = data_bytes()
    all_cbs = _owner_callbacks()
    assert len(all_cbs) > 100
    mutating = []
    for cb_data in all_cbs:
        if cb_data.startswith("acc:"):
            continue  # доступ — отдельные тесты
        store.path.write_bytes(pristine)
        store._data = None
        store.load()
        pending._states.clear()
        before = encode(store.data)
        press(FakeBot(), cb_data)
        if encode(store.data) != before:
            mutating.append(cb_data)
    prefixes = {c.split(":")[0] for c in mutating}
    # обход владельца действительно нашёл изменения во всех «пишущих» фичах
    assert {"dl", "ex", "it", "evd", "sc", "ls"} <= prefixes, prefixes
    store.path.write_bytes(pristine)
    store._data = None
    store.load()
    bot = FakeBot()
    for cb_data in mutating:
        prefix, *args = cb_data.split("|")[0].split(":")
        if prefix == "ls":  # уроки: тестеру можно — но пишется только его прогресс
            press(bot, cb_data, user_id=TESTER_ID)
            continue
        assert not access.tester_may(prefix, args), cb_data
        cb = press(bot, cb_data, user_id=TESTER_ID)
        assert cb.answers == [(DENIED, True)], cb_data
    assert data_bytes() == pristine


def test_scripted_tester_session_keeps_owner_data_byte_identical(use_data):
    data = use_data(LIVE_BACKUP)
    seed(data)
    store._data = None
    store.load()
    pristine = data_bytes()
    snapshot = encode(store.data)
    owner_cbs = _owner_callbacks()
    store.path.write_bytes(pristine)
    store._data = None
    store.load()
    bot = FakeBot()
    run(crawl(bot, TESTER_ID, limit=800))
    for cb_data in owner_cbs:  # каждая кнопка владельца, нажатая тестером
        press(bot, cb_data, user_id=TESTER_ID)
    tester = person(TESTER_ID, None, "Тестер")
    for name in REGISTRY.commands:
        command(bot, name, tester)
    for text in ["привет", "15.01", "текст задания", "20"]:
        say(bot, text, TESTER_ID)
    # старт ввода задания / баллов / правки — и текст следом
    for start in ["add", "add:k:hw:3", "sc:e:chem:kr", "it:t:0:a", "it:w:0:a"]:
        press(bot, start, user_id=TESTER_ID)
        say(bot, "20", TESTER_ID)
    # документ (в т.ч. «data.json» — восстановление базы) — игнор
    doc = message(bot, None, tester)
    doc.document = fake_document(bot, "data.json", json.dumps({"subjects": []}).encode())
    run(admin.handle_document(doc))
    press(bot, "rs:yes", user_id=TESTER_ID)
    # уроки: проходит, отвечает, сбрасывает — только своё
    lesson = content.get("lat-01")
    t = next(i for i, b in enumerate(lesson.blocks) if b["type"] == "text")
    press(bot, "ls:o:lat-01", user_id=TESTER_ID)
    press(bot, f"ls:g:lat-01:{t}", user_id=TESTER_ID)
    say(bot, "I.", TESTER_ID)
    press(bot, "ls:m:lat-02", user_id=TESTER_ID)
    press(bot, "ls:u:lat-02", user_id=TESTER_ID)
    press(bot, "ls:m:lat-03", user_id=TESTER_ID)
    assert data_bytes() == pristine
    assert encode(store.data) == snapshot
    store._data = None
    assert encode(store.load()) == snapshot
    progress = access.ACCESS.data["progress"][str(TESTER_ID)]
    assert progress["lat-03"]["done"] and str(t) in progress["lat-01"]["ans"]


def test_lesson_progress_is_separate(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "ls:m:lat-01")  # владелец
    press(bot, "ls:m:lat-02", user_id=TESTER_ID)  # тестер
    assert feat.is_learned(store.data, "lat-01") and not feat.is_learned(store.data, "lat-02")
    mine = access.ACCESS.data["progress"][str(TESTER_ID)]
    assert "lat-02" in mine and "lat-01" not in mine
    # переживает перезапуск
    access.ACCESS.reset()
    store._data = None
    assert "lat-02" in access.ACCESS.data["progress"][str(TESTER_ID)]
    assert "lat-02" not in (store.load().get("lessons") or {})
    press(bot, "ls:A", user_id=TESTER_ID)
    tester_list = callbacks(bot.last_screen().markup)
    assert "ls:o:lat-01" in tester_list and "ls:o:lat-02" not in tester_list
    press(bot, "ls:A")
    owner_list = callbacks(bot.last_screen().markup)
    assert "ls:o:lat-02" in owner_list and "ls:o:lat-01" not in owner_list


def test_pending_input_is_per_user(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "add:k:hw:3")  # владелец начал ввод
    press(bot, "ls:g:lat-01:0", user_id=TESTER_ID)
    assert pending.get(OWNER).name == admin.ADD_STATE
    assert pending.get(TESTER_ID).name == feat.STATE
    LIVE_MENU[OWNER] = 1
    LIVE_MENU[TESTER_ID] = 2
    say(bot, "ответ", TESTER_ID)
    assert pending.get(OWNER).name == admin.ADD_STATE and LIVE_MENU[OWNER] == 1
    assert all(c.kw.get("message_id") != 1 for c in bot.calls if c.kind == "edit")


def test_tester_blocked_commands_show_notice(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    tester = person(TESTER_ID)
    for name in ("status", "backup", "testers"):
        command(bot, name, tester)
        assert DENIED in bot.last_screen().text
    assert not [c for c in bot.calls if c.kind == "document"]
    command(bot, "today", tester)
    assert DENIED not in bot.last_screen().text


def test_real_dispatcher_stranger_start_and_approve(use_data):
    """Настоящие апдейты aiogram: /start от чужого → запрос владельцу → «Пустить»."""
    from aiogram import Bot
    from aiogram.types import Update

    from test_dispatcher import FakeSession

    use_data(LIVE_BACKUP)
    for feature in REGISTRY.features:
        if feature.router is not None:
            feature.router._parent_router = None
    session = FakeSession()
    bot = Bot("123456:ABCDEFabcdef-fake", session=session)
    dispatcher = app.build_dispatcher()
    nazli = {"id": STRANGER_ID, "is_bot": False, "first_name": "nazli.a"}

    async def go():
        await dispatcher.feed_update(bot, Update.model_validate({"update_id": 1, "message": {
            "message_id": 10, "date": 1, "chat": {"id": STRANGER_ID, "type": "private"}, "from": nazli,
            "text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}))
        await dispatcher.feed_update(bot, Update.model_validate({"update_id": 2, "callback_query": {
            "id": "q1", "chat_instance": "c", "from": {"id": OWNER, "is_bot": False, "first_name": "O"},
            "data": f"acc:ok:{STRANGER_ID}",
            "message": {"message_id": 777, "date": 1, "chat": {"id": OWNER, "type": "private"}, "text": "req"}}}))

    try:
        asyncio.run(go())
    finally:
        for feature in REGISTRY.features:
            if feature.router is not None:
                feature.router._parent_router = None
    texts = [(m.chat_id, m.text) for m in session.requests if type(m).__name__ == "SendMessage"]
    assert texts[0][0] == OWNER and "nazli.a" in texts[0][1]
    assert texts[1][0] == STRANGER_ID and texts[1][1].startswith("📨")
    assert texts[2][0] == STRANGER_ID and "Доступ открыт" in texts[2][1]
    assert access.role(STRANGER_ID) == access.TESTER
