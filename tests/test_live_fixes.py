"""Регрессии по живому тесту коммита 3fd0cb2 (пункты 1–9) и похожие места."""
import asyncio
import re

import pytest

from conftest import LIVE_BACKUP
from fakes import FakeBot, FakeCallback, FakeMessage, buttons, callbacks
from rudn import tuis
from rudn.core import app, home, pending, tasks, timeutil as tu
from rudn.core.registry import REGISTRY
from rudn.core.storage import store
from rudn.core.ui import Ctx, short


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def live(use_data):
    use_data(LIVE_BACKUP)
    app.run_startup_hooks(REGISTRY, store)
    return FakeBot()


def press(bot, data, user_id=111):
    cb = FakeCallback(bot, data, user_id=user_id)
    run(app.on_callback(cb))
    return cb


def labels(bot):
    return [btn.text for btn in buttons(bot.last_screen().markup)]


def subject_index(name):
    return next(i for i, s in enumerate(store.data["subjects"]) if s["name"] == name)


# 1 — кнопка «➕ Добавить…» в карточке предмета
def test_add_from_subject_card_works_and_answers(live):
    si = subject_index("Латинский язык")
    press(live, f"s:{si}")
    add = next(b for b in buttons(live.last_screen().markup) if b.callback_data == f"add:s:{si}")
    assert add.text.startswith("➕ Добавить")
    cb = press(live, add.callback_data)
    assert cb.answers and cb.answers[0][0] == "➕ Латинский язык"  # «часики» гасятся сразу
    shot = live.last_screen()
    assert "Что добавить?" in shot.text and "Латинский язык" in shot.text
    assert f"add:k:hw:{si}" in callbacks(shot.markup)
    press(live, f"add:k:hw:{si}")
    press(live, "add:d:none")
    run(app.on_text(FakeMessage(live, "выучить 20 терминов")))
    item = store.data["homework"][-1]
    assert item["text"] == "выучить 20 терминов" and item["subject_idx"] == si


def test_every_callback_is_answered(live):
    for data in ["home", "subs", "s:2", "add:s:2", "sc", "sc:c:chem", "dl", "dl:h", "ex", "nonexistent:1"]:
        assert press(live, data).answers, data


# 2 — одинаковые числа в «Предметы», карточке и «📝 Задания»
def test_open_task_counts_consistent(live):
    si = subject_index("Химия")
    open_all = tasks.open_items(store.data, tasks.ALL_KINDS, si=si)
    press(live, "subs")
    badge = next(b.text for b in buttons(live.last_screen().markup) if b.callback_data == f"s:{si}")
    assert f"📝{len(open_all)}" in badge
    press(live, f"s:{si}")
    card = live.last_screen().text
    m = re.search(r"Открытые задания \((\d+)\)", card)
    assert m and int(m.group(1)) == len(open_all)
    exams = sum(1 for i in open_all if tasks.kind_of(i) == "exam")
    assert f"экзаменов/зачётов {exams}" in card
    shown = card.split("Открытые задания", 1)[1].count("\n• ")
    hidden = re.search(r"и ещё (\d+)", card)
    assert shown + (int(hidden.group(1)) if hidden else 0) == len(open_all)
    assert f"📝 Задания ({len(open_all)})" in labels(live)
    press(live, f"dl:s:{si}")
    assert live.last_screen().text.count("\n• ") == len(open_all)


def test_card_says_and_more_when_hidden(live):
    si = subject_index("Химия")
    for n in range(4):
        store.data["homework"].append({"id": 9000 + n, "kind": "hw", "subject_idx": si, "text": f"доп {n}", "done": False})
    total = len(tasks.open_items(store.data, tasks.ALL_KINDS, si=si))
    press(live, f"s:{si}")
    card = live.last_screen().text
    assert f"Открытые задания ({total})" in card
    assert f"… и ещё {total - 6} — в «📝 Задания»" in card


def test_global_deadlines_point_to_exams(live):
    press(live, "dl")
    shot = live.last_screen()
    n = len(tasks.upcoming_exams(store.data, tu.today()))
    assert f"Экзамены и зачёты ({n})" in shot.text and "ex" in callbacks(shot.markup)


# 3 — нули не показываем в сводках
def test_zero_score_hidden_in_summaries(live):
    tuis.set_score(store.data, "chem", "labs", 0)
    press(live, "home")
    assert "Баллы:" not in live.last_screen().text
    press(live, f"s:{subject_index('Химия')}")
    assert "мои баллы" not in live.last_screen().text
    press(live, "sc:c:chem")
    assert "Лабораторные работы: <b>0</b>" in live.last_screen().text  # сам ввод виден
    tuis.set_score(store.data, "chem", "labs", 2.5)
    press(live, "home")
    assert "Баллы: Химия 2,5" in live.last_screen().text


# 4 — «Ещё можно набрать»
def test_remaining_points():
    chem = tuis.course("chem")
    data = {}
    tuis.set_score(data, "chem", "labs", 2.5)
    assert tuis.remaining(data, chem) == 97.5
    assert "Ещё можно набрать: 97,5 б." in "\n".join(tuis.status_lines(data, chem))
    phys = tuis.course("phys")
    assert tuis.remaining({}, phys) == 98  # в документе сумма 98


# 5 — после ввода остаёмся в режиме ввода
def test_stay_in_score_input(live):
    press(live, "sc:e:chem:labs")
    run(app.on_text(FakeMessage(live, "2,5")))
    run(app.on_text(FakeMessage(live, "+1")))
    run(app.on_text(FakeMessage(live, "+1")))
    assert store.data["scores"]["chem"]["labs"] == 4.5
    shot = live.last_screen()
    assert "сейчас <b>4,5</b> из 24 ✅ сохранено" in shot.text and "ещё можно набрать 95,5" in shot.text
    assert pending.get(111) is not None
    press(live, "sc:v:chem:labs:p1")
    press(live, "sc:v:chem:labs:m1")
    press(live, "sc:v:chem:labs:p1")
    assert store.data["scores"]["chem"]["labs"] == 5.5
    assert pending.get(111) is not None and "Лабораторные работы: сейчас" in live.last_screen().text
    assert ("⬅ Назад", "sc:c:chem") in [(b.text, b.callback_data) for b in buttons(live.last_screen().markup)]
    press(live, "sc:c:chem")
    assert pending.get(111) is None
    run(app.on_text(FakeMessage(live, "7")))
    assert store.data["scores"]["chem"]["labs"] == 5.5


# 6 — команды во время ввода и случайный текст
def test_unknown_command_during_input_reports_cancel(live):
    press(live, "add")
    press(live, "add:k:hw")
    run(app.on_unknown_command(FakeMessage(live, "/foo")))
    assert pending.get(111) is None
    assert live.last_screen().text.startswith("✖️ <i>Ввод отменён</i>")


def test_known_command_during_input_reports_cancel(live):
    press(live, "sc:e:chem:labs")
    run(app._command_handler(REGISTRY.commands["deadlines"][1])(FakeMessage(live, "/deadlines")))
    assert pending.get(111) is None
    shot = live.last_screen().text
    assert shot.startswith("✖️ <i>Ввод отменён</i>") and "Дедлайны" in shot


def test_command_without_input_has_no_notice(live):
    run(app.on_unknown_command(FakeMessage(live, "/foo")))
    assert "Ввод отменён" not in live.last_screen().text
    run(app._command_handler(REGISTRY.commands["deadlines"][1])(FakeMessage(live, "/deadlines")))
    assert "Ввод отменён" not in live.last_screen().text


def test_stray_text_gets_hint(live):
    before = len(store.data["homework"])
    run(app.on_text(FakeMessage(live, "привет")))
    shot = live.last_screen().text
    assert shot.startswith("💡") and "🎓 <b>РУДН" in shot
    assert len(store.data["homework"]) == before  # не сохранилось как д/з
    run(app.on_text(FakeMessage(live, "привет", user_id=222)))
    assert live.last_screen().text.startswith("💡 <i>Бот управляется")


# 7 — короткие названия и обрезка по словам
def test_short_names_used_everywhere(live):
    assert tu.short_name("Основы российской государственности") == "ОРГ"
    assert tu.short_name("Химия") == "Химия"
    press(live, "subs")
    texts = labels(live)
    for want in ["ОРГ", "РКИ", "РЯВАС", "Мед. информатика", "ОВП / БЖД", "Физкультура"]:
        assert any(t.startswith(want) for t in texts), want
    assert not any("…" in t for t in texts)
    press(live, "add")
    press(live, "add:k:hw")
    assert not any("…" in t for t in labels(live) if t not in ("✖ Отмена",))
    press(live, "dl")
    assert "ОРГ: «Рубежный" in live.last_screen().text


def test_short_cuts_on_word_boundary():
    text = "Повторное выполнение ЛР 5–8 и переписывание КР №1"
    cut = short(text, 30)
    assert cut.endswith("…") and text.startswith(cut[:-1]) and text[len(cut) - 1] == " "
    assert short("коротко", 30) == "коротко"


# 8 — «🗑 Удалить» с подписью
def test_delete_buttons_have_label(live):
    item = tasks.open_items(store.data)[0]
    press(live, f"dl:ok:{item['id']}:a")
    press(live, "dl:h:a")
    assert ("🗑 Удалить", f"dl:rm:{item['id']}:a") in [(b.text, b.callback_data) for b in buttons(live.last_screen().markup)]
    exam = tasks.upcoming_exams(store.data, tu.today())[0]
    press(live, f"ex:ok:{exam['id']}")
    press(live, "ex:p")
    assert any(b.text == "🗑 Удалить" and b.callback_data == f"ex:rm:{exam['id']}" for b in buttons(live.last_screen().markup))
    assert not any(b.text == "🗑" for b in buttons(live.last_screen().markup))


# 9 — «Назад»: ответ на нажатие не ждёт правки сообщения
def test_show_answers_callback_concurrently():
    order = []

    class Bot:
        async def edit_message_text(self, **kw):
            order.append("edit-start")
            await asyncio.sleep(0.05)
            order.append("edit-end")

        async def delete_message(self, *a):
            pass

    class Cb:
        def __init__(self):
            self.message = type("M", (), {"chat": type("C", (), {"id": 1}), "message_id": 5})
            self.from_user = type("U", (), {"id": 1})
            self.bot = Bot()

        async def answer(self, **kw):
            order.append("answer-start")
            await asyncio.sleep(0.05)
            order.append("answer-end")

    ctx = Ctx.from_callback(Cb())
    run(ctx.show("x", []))
    assert order.index("answer-start") < order.index("edit-start") < order.index("answer-end")
