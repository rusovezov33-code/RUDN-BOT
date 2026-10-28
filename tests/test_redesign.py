"""Редизайн «меньше визуального шума»: 6 кнопок меню, чистый главный экран,
короткие подписи, форма (config/dresscode.json), «⋯ Ещё» для тестера."""
import asyncio
import re
from datetime import datetime
from types import SimpleNamespace

from conftest import LIVE_BACKUP, TESTER_ID
from fakes import FakeBot, FakeCallback, buttons, callbacks
from rudn.core import app, dresscode, timeutil as tu
from rudn.core.registry import REGISTRY
from rudn.core.storage import store
from rudn.core.ui import LIVE_MENU

OWNER = 111
DENIED = "👀 Режим тестера: изменения недоступны"
MENU = ["🗓 Расписание", "✅ Задачи", "📚 Предметы", "🧠 Учить", "📌 Заметки", "⋯ Ещё"]
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF\u22EF\uFE0F]"
)


def run(coro):
    return asyncio.run(coro)


def press(bot, data, user_id=OWNER):
    cb = FakeCallback(bot, data, user_id=user_id, message_id=LIVE_MENU.get(user_id))
    run(app.on_callback(cb))
    return cb


def emoji_count(text):
    return len([ch for ch in EMOJI.findall(text) if ch != "\uFE0F"])


def lesson(subject, place=""):
    return SimpleNamespace(subject=subject, place=place, time="12:00–13:20", weeks="")


def index_of(name):
    return next(i for i, s in enumerate(store.data["subjects"]) if s["name"] == name)


def test_main_menu_six_buttons(use_data):
    use_data(LIVE_BACKUP)
    for user in (OWNER, TESTER_ID):
        bot = FakeBot()
        press(bot, "home", user_id=user)
        markup = bot.last_screen().markup
        labels = [btn.text.rstrip("0123456789 ") for btn in buttons(markup)]
        assert len(labels) <= 6 and labels == MENU, user
        assert [len(row) for row in markup.inline_keyboard] == [2, 2, 2]


def test_main_screen_body_has_no_emoji(use_data):
    use_data(LIVE_BACKUP)
    for moment in (datetime(2026, 10, 28, 11, 0), datetime(2026, 10, 26, 12, 30), datetime(2026, 11, 1, 10, 0)):
        tu.set_now(moment.replace(tzinfo=tu.MSK))
        bot = FakeBot()
        press(bot, "home")
        text = bot.last_screen().text
        assert emoji_count(text) == 0, text
        for noise in ("📍", "•", "Подсказ", "РУДН", "обновлено", "Нажми"):
            assert noise not in text


def test_main_screen_28_10(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 10, 28, 11, 0, tzinfo=tu.MSK))
    bot = FakeBot()
    press(bot, "home")
    text = bot.last_screen().text
    assert text.startswith("<b>Среда, 28 октября</b> · верхняя неделя\n\n")
    assert "12:00  Введение в специальность" in text and "19:30  Основы военной подготовки · онлайн" in text
    assert "\nВзять: халат, шапочка, сменка" in text
    assert "АТИ" not in text and "ауд." not in text  # адреса — только по нажатию


def test_button_labels_short_and_one_emoji(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 10, 28, 11, 0, tzinfo=tu.MSK))
    screens = ["home", "mo", "subs", "dl", "ex", "d:t", "nt", "sc|subs"]
    screens += [f"s:{i}" for i in range(len(store.data["subjects"]))]
    for user in (OWNER, TESTER_ID):
        bot = FakeBot()
        for data in screens:
            press(bot, data, user_id=user)
            for btn in buttons(bot.last_screen().markup):
                assert len(btn.text) <= 20, (data, btn.text)
                assert emoji_count(btn.text) <= 1, (data, btn.text)


def test_dresscode_union_and_aliases():
    assert dresscode.items_for("Латинский язык") == ("халат",)
    assert dresscode.items_for("Введение в специальность / Ознакомительная практика") == ("халат", "шапочка", "сменка")
    assert dresscode.items_for("Химия биогенных элементов") == ("халат",)  # = Химия
    assert dresscode.items_for("Медицинская элементология") == ("халат",)
    assert dresscode.items_for("Русский язык для иностранцев") == ()
    lessons = [lesson("Латинский язык", "ауд. 10"), lesson("Анатомия", "ауд. 2"), lesson("Материаловедение", "ауд. 3")]
    assert dresscode.take_line(lessons) == "Взять: халат, шапочка, сменка"
    assert dresscode.take_line([lesson("Латинский язык"), lesson("Биология")]) == "Взять: халат"
    assert dresscode.take_line([lesson("РКИ")]) is None
    assert dresscode.form_line("Анатомия") == "Форма: халат, шапочка"


def test_dresscode_skips_online():
    online = [lesson("Моделирование", "лек (ДОТ)"), lesson("Химия биогенных элементов", "лаб (ДОТ)")]
    assert dresscode.take_line(online) is None
    mixed = online + [lesson("Гистология", "ауд. 5")]
    assert dresscode.take_line(mixed) == "Взять: халат"


def test_take_line_on_home_and_tomorrow(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 10, 27, 20, 0, tzinfo=tu.MSK))
    bot = FakeBot()
    press(bot, "d:tm")  # «Завтра» = 28.10
    assert "Взять: халат, шапочка, сменка" in bot.last_screen().text
    tu.set_now(datetime(2026, 10, 29, 9, 0, tzinfo=tu.MSK))  # чт: всё очное без формы, химия — ДОТ
    press(bot, "home")
    assert "Взять" not in bot.last_screen().text


def test_subject_card_form_line(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, f"s:{index_of('Латинский язык')}")
    text = bot.last_screen().text
    assert text.startswith("<b>Латинский язык</b>\n") and "\nФорма: халат" in text
    cbs = callbacks(bot.last_screen().markup)
    assert f"s:{index_of('Латинский язык')}:t" in cbs and cbs[-2:] == ["subs", "home"]


def test_tester_opens_more_read_only(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    cb = press(bot, "mo", user_id=TESTER_ID)
    assert DENIED not in [a[0] for a in cb.answers]
    shot = bot.last_screen()
    assert shot.text.startswith("<b>Ещё</b>")
    cbs = callbacks(shot.markup)
    assert "add" not in cbs and "adm" not in cbs and "home" in cbs
    page = next(c for c in cbs if c.startswith("p:"))
    cb = press(bot, page, user_id=TESTER_ID)
    assert DENIED not in [a[0] for a in cb.answers]
    owner = FakeBot()
    press(owner, "mo")
    assert {"add", "adm"} <= set(callbacks(owner.last_screen().markup))


def test_old_callbacks_still_work(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for data in ["d:t", "d:tm", "w:now", "dl", "dl:h", "ex", "ex:p", "subs", "s:0", "sc", "ls", "p:0", "nt", "add", "adm"]:
        REGISTRY.check_callback(data)
        before = len(bot.calls)
        cb = press(bot, data)
        assert len(bot.calls) > before or cb.answers, data
