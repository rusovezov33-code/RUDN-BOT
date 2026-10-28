"""Главный экран: только расписание на сегодня + свои заметки/напоминания; «📌 Заметки»;
«Назад» на сводке «🎓 ТУИС · предмет» ведёт в карточку предмета."""
import asyncio
from datetime import datetime

from conftest import LIVE_BACKUP, TESTER_ID
from fakes import FakeBot, FakeCallback, FakeMessage, buttons, callbacks
from rudn.core import app, timeutil as tu
from rudn.core.storage import store
from rudn.core.ui import LIVE_MENU

OWNER = 111
DENIED = "👀 Режим тестера: изменения недоступны"


def run(coro):
    return asyncio.run(coro)


def press(bot, data, user_id=OWNER):
    cb = FakeCallback(bot, data, user_id=user_id, message_id=LIVE_MENU.get(user_id))
    run(app.on_callback(cb))
    return cb


def say(bot, text, user_id=OWNER):
    run(app.on_text(FakeMessage(bot, text, user_id=user_id)))


def subject_index(name):
    return next(i for i, s in enumerate(store.data["subjects"]) if s["name"] == name)


def test_home_is_schedule_and_own_notes_only(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 10, 26, 12, 30, tzinfo=tu.MSK))
    bot = FakeBot()
    press(bot, "home")
    shot = bot.last_screen()
    text = shot.text
    assert text.startswith("<b>Понедельник, 26 октября</b> · верхняя неделя\n\n<b>12:00–13:20  Русский язык для иностранцев</b> · ЕГФ ауд. 375\n")
    assert "\n13:30–14:50  Русский язык для иностранцев · ЕГФ ауд. 375" in text
    # своё напоминание на сегодня (➕ → 🔔) — видно, без заголовков
    assert "\nПоработать над ИИ." in text
    for noise in ["Скоро", "Дедлайн", "срочных", "Ближайший", "📚", "Сейчас:", "Далее", "Баллы", "ТУИС", "Заметки", "•", "📍"]:
        assert noise not in text, noise
    cbs = callbacks(shot.markup)
    assert cbs == ["d:t", "dl", "subs", "ls", "nt", "mo"]
    # d:t по-прежнему работает (команда /today, старые кнопки)
    press(bot, "d:t")
    assert "26.10" in bot.last_screen().text or "26 октября" in bot.last_screen().text


def test_home_without_classes_one_line(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 11, 1, 10, 0, tzinfo=tu.MSK))  # воскресенье
    bot = FakeBot()
    press(bot, "home")
    body = bot.last_screen().text.split("\n\n", 1)[1]
    assert body.split("\n")[0] == "Пар нет"
    assert "Расписание на сегодня" not in body


def test_notes_add_list_delete(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "nt")
    shot = bot.last_screen()
    assert shot.text.startswith("📌 <b>Заметки</b>") and "Пока пусто." in shot.text
    assert "nt:a" in callbacks(shot.markup) and "home" in callbacks(shot.markup)
    press(bot, "nt:a")
    assert "Новая заметка" in bot.last_screen().text
    say(bot, "Сдать справку в деканат\nдо пятницы")
    assert store.data["notes"][0]["text"] == "Сдать справку в деканат\nдо пятницы"
    assert "✅ Добавлено" in bot.last_screen().text
    press(bot, "nt:a")
    say(bot, "Купить <халат>")
    press(bot, "home")
    text = bot.last_screen().text
    assert "Сдать справку в деканат" in text and "Купить &lt;халат&gt;" in text
    assert text.index("Русский язык") < text.index("Сдать справку") < text.index("Купить")
    press(bot, "nt")
    assert "nt:d:0" in callbacks(bot.last_screen().markup)
    press(bot, "nt:d:0")
    shot = bot.last_screen()
    assert "Удалить заметку?" in shot.text and "nt:d:0:y" in callbacks(shot.markup)
    press(bot, "nt:d:0:y")
    assert [n["text"] for n in store.data["notes"]] == ["Купить <халат>"]
    press(bot, "nt:d:5:y")  # уже нет — без падения
    press(bot, "nt:d:0:y")
    assert store.data["notes"] == []
    press(bot, "home")
    assert "Купить" not in bot.last_screen().text


def test_notes_tester_read_only(use_data):
    use_data(LIVE_BACKUP)
    store.data["notes"] = [{"text": "моя заметка", "created": "2026-10-26T10:00:00+03:00"}]
    bot = FakeBot()
    press(bot, "nt", user_id=TESTER_ID)
    shot = bot.last_screen()
    assert "моя заметка" in shot.text
    assert not [c for c in callbacks(shot.markup) if c.startswith("nt:")]
    for data in ["nt:a", "nt:d:0", "nt:d:0:y"]:
        cb = press(bot, data, user_id=TESTER_ID)
        assert DENIED in [a[0] for a in cb.answers], data
    say(bot, "чужой текст", user_id=TESTER_ID)
    assert [n["text"] for n in store.data["notes"]] == ["моя заметка"]


def test_tuis_summary_back_returns_to_subject_card(use_data):
    use_data(LIVE_BACKUP)
    si = subject_index("Химия")
    for user in (OWNER, TESTER_ID):
        bot = FakeBot()
        for data in ["home", "subs", f"s:{si}", f"s:{si}:t"]:
            press(bot, data, user_id=user)
        shot = bot.last_screen()
        assert shot.text.startswith("🎓 <b>ТУИС · Химия</b>")
        back = next(x for x in buttons(shot.markup) if x.text == "⬅ Назад")
        assert back.callback_data == f"s:{si}"
        press(bot, back.callback_data, user_id=user)
        assert bot.last_screen().text.startswith("<b>Химия</b>"), user
        # через «📊 Мои баллы» и обратно: ТУИС → карточка
        press(bot, f"s:{si}:t", user_id=user)
        scores = next(x.callback_data for x in buttons(bot.last_screen().markup) if x.text == "📊 Мои баллы")
        press(bot, scores, user_id=user)
        back = next(x.callback_data for x in buttons(bot.last_screen().markup) if x.text == "⬅ Назад")
        press(bot, back, user_id=user)
        assert bot.last_screen().text.startswith("🎓 <b>ТУИС · Химия</b>")
        press(bot, f"s:{si}", user_id=user)
        assert bot.last_screen().text.startswith("<b>Химия</b>")
        # из раздела ТУИС: Назад → сводка ТУИС
        press(bot, f"s:{si}:t:brs", user_id=user)
        back = next(x.callback_data for x in buttons(bot.last_screen().markup) if x.text == "⬅ Назад")
        assert back == f"s:{si}:t"
