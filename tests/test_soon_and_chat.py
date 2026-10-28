"""КР по латыни от преподавателя (чат группы, 03.11), блок «⏰ Скоро», карточка латыни."""
import asyncio
from datetime import date, datetime

from conftest import LIVE_BACKUP
from fakes import FakeBot, FakeCallback, buttons
from rudn import tuis
from rudn.core import app, tasks, timeutil as tu
from rudn.features import soon

TODAY = date(2026, 10, 27)
CHAT_KEY = "lat-kr1-chat"


def run(coro):
    return asyncio.run(coro)


def at_today():
    tu.set_now(datetime(2026, 10, 27, 12, 0, tzinfo=tu.MSK))


def latin(data):
    return tuis._subject_index(data, "Латинский язык")


def chat_items(data):
    return [i for i in data["homework"] if i.get("tuis_key") == CHAT_KEY]


def test_chat_kr_event_in_config():
    event = next(e for e in tuis.events() if e["key"] == CHAT_KEY)
    assert event["src"] == "chat" and event["kind"] == "ctrl" and event["subject"] == "Латинский язык"
    assert tuis.event_date(event) == date(2026, 11, 3)
    for part in ("перевод словосочетаний с русского и с латинского", "определение грамматической формы",
                 "склонение словосочетаний", "приставки / суффиксы", "задание на сокращение"):
        assert part in event["text"]
    # производные КР2/КР3 из КП остаются
    assert {"lat-kr2", "lat-kr3"} <= {e["key"] for e in tuis.events()}


def test_chat_kr_seeded_once_exact_date_and_no_resurrect(use_data):
    data = use_data(LIVE_BACKUP)
    si = latin(data)
    # своё Д/з на тот же день — не дубль КР
    tasks.new_item(data, "hw", si, "д.з. на 3.11", date(2026, 11, 3))
    tuis.seed_events(data, TODAY)
    [item] = chat_items(data)
    assert item["src"] == "chat" and item["date"] == "2026-11-03" and not item.get("date_hint")
    assert tasks.due_text(item, TODAY) == "ВТ 03.11"  # дата подтверждена — без ≈
    assert {"lat-kr2", "lat-kr3"} <= {i.get("tuis_key") for i in data["homework"]}
    # повторный запуск — без дублей; обновление текста работает и для src=chat
    assert tuis.seed_events(data, TODAY) == [] and tuis.restore_suppressed(data, TODAY) == []
    assert tuis.update_seeded(data) == []
    tasks.delete_item(data, item)
    tuis.seed_events(data, TODAY)
    tuis.restore_suppressed(data, TODAY)
    assert chat_items(data) == []


def test_chat_kr_not_added_over_own_kr_same_day(use_data):
    data = use_data(LIVE_BACKUP)
    tasks.new_item(data, "ctrl", latin(data), "КР по латыни", date(2026, 11, 3))
    tuis.seed_events(data, TODAY)
    assert chat_items(data) == []


def test_soon_items_window_and_format(use_data):
    data = use_data(LIVE_BACKUP)
    tuis.seed_events(data, TODAY)
    si = latin(data)
    hw = tasks.new_item(data, "hw", si, "Выучить слова", date(2026, 10, 28))
    far = tasks.new_item(data, "hw", si, "Далеко", date(2026, 11, 11))
    past = tasks.new_item(data, "hw", si, "Прошло", date(2026, 10, 26))
    edge = tasks.new_item(data, "hw", si, "Ровно 14", date(2026, 11, 10))
    got = soon.soon_items(data, TODAY)
    assert hw in got and edge in got and far not in got and past not in got
    assert got == sorted(got, key=tasks.sort_key)
    kr = chat_items(data)[0]
    assert kr in got and soon.is_check(kr) and not soon.is_check(hw)
    lines = soon.entry_lines(kr, data, TODAY, full=True)
    assert lines[0].startswith("❗ <b>ВТ 03.11</b> · через 7 дн.")
    assert "📚 перевод словосочетаний" in lines[1] and "задание на сокращение" in lines[1]
    assert soon.days_left(date(2026, 10, 28), TODAY) == "завтра"
    # темы из «кавычек» для КР из ФОС
    assert soon.split_topics({"text": "КР1 «Текстовый процессор» + доп. задания"})[1] == "Текстовый процессор"


def test_soon_on_home_compact_and_full_in_deadlines(use_data):
    data = use_data(LIVE_BACKUP)
    at_today()
    tuis.seed_events(data, TODAY)
    si = latin(data)
    for n in range(7):
        tasks.new_item(data, "hw", si, f"Д/з {n}", date(2026, 11, 1 + n))
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, "home")))
    home = bot.last_screen()
    text = home.text
    block = text.split("\n\n")[1]
    assert block.startswith("⏰ <b>Скоро</b> — 14 дней (8):")
    body = block.split("\n")[1:]
    assert len([line for line in body if not line.startswith("   …")]) <= 5
    assert body[-1].startswith("   … ещё ")
    assert not any("Скоро" in btn.text for btn in buttons(home.markup))  # без новой кнопки меню
    run(app.on_callback(FakeCallback(bot, "dl")))
    dl = bot.last_screen().text
    assert dl.index("⏰ <b>Скоро</b>") < dl.index("<b>📆 На неделе</b>")
    assert "📚 перевод словосочетаний с русского и с латинского" in dl
    assert "Д/з 6" in dl.split("<b>📆")[0]  # полный список, без «ещё N»


def test_no_new_menu_button():
    from rudn.core.registry import REGISTRY
    app.build_registry()
    soon_feature = next(f for f in REGISTRY.features if f.name == "soon")
    assert soon_feature.menu == [] and soon_feature.callbacks == {}


def test_latin_card_teacher_note_and_textbook(use_data):
    data = use_data(LIVE_BACKUP)
    bot = FakeBot()
    run(app.on_callback(FakeCallback(bot, f"s:{latin(data)}")))
    card = bot.last_screen().text
    assert "👤 Провоторова Елена Аркадьевна" in card
    assert "учить слова" in card and "устный опрос" in card
    lit = tuis.literature_for("Латинский язык")
    assert lit["main"][0].startswith("Латинский язык. Учебно-методическое пособие по медицинской терминологии")
    assert lit["links"][0]["url"] == "https://esystem.rudn.ru/mod/resource/view.php?id=1174286"
