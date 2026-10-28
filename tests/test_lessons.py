"""📚 Уроки: схема контента, проверка ответов, прогресс (усвоенные скрываются и
возвращаются), все кнопки зарегистрированы и ≤64 байт, крайние случаи ввода."""
import asyncio
import json
import logging
from collections import deque
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Update

from conftest import LIVE_BACKUP
from fakes import FakeBot, FakeCallback, FakeMessage, buttons, callbacks, summary_text
from rudn import lessons as content
from rudn.core import app, home, pending, timeutil as tu
from rudn.core.registry import REGISTRY
from rudn.core.storage import store
from rudn.core.ui import LIVE_MENU
from rudn.features import lessons as feat
from test_dispatcher import FakeSession


def run(coro):
    return asyncio.run(coro)


def press(bot, data, user_id=111):
    cb = FakeCallback(bot, data, user_id=user_id, message_id=LIVE_MENU.get(user_id))
    run(app.on_callback(cb))
    return cb


def say(bot, text):
    msg = FakeMessage(bot, text)
    run(app.on_text(msg))
    return msg


def screen(bot):
    return bot.last_screen()


def first_question(lesson, kind):
    return next(i for i, block in enumerate(lesson.blocks) if block["type"] == kind)


LAT = "lat-01"


# ---------------------------------------------------------------- схема
def test_all_content_files_valid():
    lessons, errors = content.load_dir()
    assert not errors, errors
    files = sorted(content.CONTENT_DIR.glob("*/*.json"))
    assert len(lessons) == len(files) >= 5
    ids = [l.id for l in lessons]
    assert len(ids) == len(set(ids))
    latin = [l for l in lessons if l.subject == "Латинский язык"]
    assert 4 <= len(latin) <= 8
    for lesson in lessons:
        kinds = [b["type"] for b in lesson.blocks]
        assert kinds[-1] == "think" and "explain" in kinds and "example" in kinds
        assert 3 <= len(lesson.questions) <= 6
        assert lesson.sources and all(s.strip() for s in lesson.sources)
        if lesson.subject == "Латинский язык":
            assert any("с." in s for s in lesson.sources), lesson.id  # страницы пособия
            assert lesson.target and lesson.target["date"] == "2026-11-03"


def test_rendered_blocks_fit_telegram(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for lesson in content.all_lessons():
        for index, block in enumerate(lesson.blocks):
            press(bot, f"ls:g:{lesson.id}:{index}")
            shot = screen(bot)
            assert len(shot.text) < 3900 and "…" not in shot.text[-5:], (lesson.id, index)
            if block["type"] == "choice":
                press(bot, f"ls:a:{lesson.id}:{index}:0")
            elif block["type"] in ("text", "think"):
                say(bot, "x" * 300)
            assert len(screen(bot).text) < 3900, (lesson.id, index)


@pytest.mark.parametrize(
    "mutate, problem",
    [
        (lambda r: r.pop("sources"), "sources"),
        (lambda r: r.update(id="BAD ID"), "id"),
        (lambda r: r["blocks"].pop(), "think"),
        (lambda r: r["blocks"][0].update(text="<a>x</a>"), "не разрешён"),
        (lambda r: r["blocks"][0].update(text="<b>открыт"), "не закрыт"),
        (lambda r: r["blocks"][0].update(text="a & b"), "&"),
        (lambda r: r["blocks"].insert(0, {"type": "think", "q": "q", "answer": "a"}), "think"),
        (lambda r: next(b for b in r["blocks"] if b["type"] == "choice").update(correct=9), "correct"),
        (lambda r: next(b for b in r["blocks"] if b["type"] == "text").update(accept=[]), "accept"),
        (lambda r: r.update(blocks=[b for b in r["blocks"] if b["type"] != "example"]), "example"),
        (lambda r: r.update(blocks=[b for b in r["blocks"] if b["type"] in ("explain", "example", "think")]), "вопросов"),
        (lambda r: r["target"].update(date="03.11"), "target"),
    ],
)
def test_validator_rejects(mutate, problem):
    raw = json.loads((content.CONTENT_DIR / "latin" / "lat-01.json").read_text(encoding="utf-8"))
    assert content.validate(raw) == []
    mutate(raw)
    errors = content.validate(raw)
    assert any(problem in e for e in errors), errors


def test_broken_file_skipped_not_fatal(tmp_path):
    (tmp_path / "x").mkdir()
    good = json.loads((content.CONTENT_DIR / "latin" / "lat-01.json").read_text(encoding="utf-8"))
    (tmp_path / "x" / "lat-01.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "x" / "bad.json").write_text("{не json", encoding="utf-8")
    renamed = dict(good, id="lat-99")
    (tmp_path / "x" / "lat-02.json").write_text(json.dumps(renamed, ensure_ascii=False), encoding="utf-8")
    lessons, errors = content.load_dir(tmp_path)
    assert [l.id for l in lessons] == ["lat-01"]
    assert len(errors) == 2


# ---------------------------------------------------------------- проверка ответов
@pytest.mark.parametrize(
    "given, accept, ok",
    [
        ("Ramus  Mandibulae!", ["ramus mandibulae"], True),
        ("  ramus,mandibulae. ", ["ramus mandibulae"], True),
        ("ligg.", ["ligg"], True),
        ("-i", ["i"], True),
        ("\u0441\u043estae", ["costae"], True),  # кириллические «с», «о»
        ("ВНУТРИЧЕРЕПНОЙ", ["внутричерепной", "внутричерепная"], True),
        ("внутричерепная", ["внутричерепной", "внутричерепная"], True),
        ("тело подъязычнои кости", ["тело подъязычной кости"], True),
        ("зубные канальцы", ["зубные канальцы"], True),
        ("Зубные Канальцы", ["зубные канальцы"], True),
        ("ёлка", ["елка"], True),
        ("vértebra", ["vertebra"], True),
        ("costa", ["costae"], False),  # окончание — это ошибка, не опечатка
        ("facierum articularum", ["facierum articularium"], False),
        ("поднижнечелюсная ямка", ["поднижнечелюстная ямка"], True),  # опечатка в русском
        ("ramus", ["ramus mandibulae"], False),
        ("", ["x"], False),
    ],
)
def test_match(given, accept, ok):
    assert content.match(given, accept).ok is ok


def test_match_flags():
    assert content.match("😀😀", ["x"]).empty
    assert content.match("...", ["x"]).empty
    assert content.match("a" * 301, ["a"]).too_long
    assert content.match("costa", ["costae"]).near
    assert content.match("поднижнечелюсная ямка", ["поднижнечелюстная ямка"]).typo


def test_check_choice_by_number_or_text():
    block = {"type": "choice", "options": ["Gen. Sing.", "Nom. Plur.", "Gen. Plur."], "correct": 2}
    assert content.check_choice(block, "3") == (content.Verdict(True), 2)
    assert content.check_choice(block, "gen plur") == (content.Verdict(True), 2)
    assert content.check_choice(block, "1")[0].ok is False
    assert content.check_choice(block, "7")[1] is None
    numeric = {"type": "choice", "options": ["2", "3", "4"], "correct": 1}
    assert content.check_choice(numeric, "3") == (content.Verdict(True), 1)  # текст варианта важнее номера


# ---------------------------------------------------------------- меню, список, сроки
def test_menu_button_owner_only(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "home")
    assert "ls" in callbacks(screen(bot).markup)
    assert "🧠 Учить" in [b.text for b in buttons(screen(bot).markup)]
    press(bot, "home", user_id=222)  # тестер: уроки есть (свой прогресс)
    assert "ls" in callbacks(screen(bot).markup)
    calls = len(bot.calls)
    cb = press(bot, "ls", user_id=333)  # чужой: ничего
    assert cb.answers[0] == (None, False) and len(bot.calls) == calls


def test_list_deadline_first_and_hint(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "ls:A")
    text = screen(bot).text
    assert "к КР 03.11, осталось 8 дн." in text
    assert "к ЛР №5 03.11" in text
    cbs = [c for c in callbacks(screen(bot).markup) if c.startswith("ls:o:")]
    assert len(cbs) == len(content.all_lessons())
    assert cbs[0] == "ls:o:lat-01"
    tu.set_now(datetime(2026, 11, 2, 9, 0, tzinfo=tu.MSK))
    press(bot, "ls:A")
    assert "к КР 03.11, уже завтра" in screen(bot).text
    tu.set_now(datetime(2026, 11, 4, 9, 0, tzinfo=tu.MSK))
    press(bot, "ls:A")
    assert "к КР" not in screen(bot).text


def test_deadline_uses_seeded_item_and_done_hides(use_data):
    data = use_data(LIVE_BACKUP)
    lesson = content.get(LAT)
    data["homework"].append({"id": 999, "subject": "Латинский язык", "text": "КР", "tuis_key": "lat-kr1-chat",
                             "date": "2026-11-05", "done": False})
    assert feat.deadline(lesson, data) == ("КР", date(2026, 11, 5))
    data["homework"][-1]["done"] = True
    assert feat.deadline(lesson, data) is None


def test_home_summary(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "home")
    assert "📚" not in screen(bot).text  # на главном только расписание и свои заметки
    # сводка уроков (без главного): 26.10 (пн, 2 пары) — только задание на сегодня
    assert "📚 Сегодня: Латынь — уроки 1, 2 (≈29 мин)" in summary_text(feat)
    assert "0/8" not in summary_text(feat)
    press(bot, "ls:m:lat-01")
    assert "📚 Сегодня: Латынь — урок 2 (≈14 мин)" in summary_text(feat)
    press(bot, "ls:m:lat-02")
    assert "📚 Сегодня: ✅ уроки на сегодня сделаны" in summary_text(feat)
    tu.set_now(datetime(2026, 10, 26, 22, 0, tzinfo=tu.MSK))  # вечер: показываем завтра
    press(bot, "ls:u:lat-02")
    press(bot, "ls:u:lat-01")
    assert "📚 Завтра: Латынь — урок 1 (≈15 мин)" in summary_text(feat)
    assert "📚 Завтра: Латынь — урок 1" in summary_text(feat, user_id=222)  # тестер: свой план


# ---------------------------------------------------------------- урок в одном сообщении
def test_lesson_flow_edits_one_message(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "ls")
    menu_id = LIVE_MENU[111]
    press(bot, f"ls:o:{LAT}")
    shot = screen(bot)
    assert shot.kind == "edit" and shot.kw["message_id"] == menu_id
    assert "Блок 1/9" in shot.text and "📖" in shot.text
    labels = [b.text for b in buttons(shot.markup)]
    assert "◀️ Назад" not in labels and {"Далее ▶️", "✖️ Выйти", "🗑 Сброс", "🏠 Меню"} <= set(labels)
    lesson = content.get(LAT)
    q = first_question(lesson, "choice")
    press(bot, f"ls:g:{LAT}:{q}")
    assert f"Блок {q + 1}/9" in screen(bot).text and "Вопрос 1/" in screen(bot).text
    assert "◀️ Назад" in [b.text for b in buttons(screen(bot).markup)]
    # неверный вариант кнопкой
    cb = press(bot, f"ls:a:{LAT}:{q}:0")
    assert cb.answers[-1][0] == "❌ Неверно"
    assert "❌ <b>Неверно.</b>" in screen(bot).text and "✔️ Правильно: <b>3</b>" in screen(bot).text
    assert "🔁 Ещё раз" in [b.text for b in buttons(screen(bot).markup)]
    press(bot, f"ls:t:{LAT}:{q}")
    press(bot, f"ls:a:{LAT}:{q}:2")
    assert "✅ <b>Верно!</b>" in screen(bot).text
    # текстовый ответ: сообщение удаляется, результат — в том же сообщении
    t = first_question(lesson, "text")
    press(bot, f"ls:g:{LAT}:{t}")
    before = len(bot.calls)
    msg = say(bot, "  I. ")
    kinds = [c.kind for c in bot.calls[before:]]
    assert kinds == ["delete", "edit"], kinds
    assert bot.calls[before].kw["message_id"] == msg.message_id
    assert bot.calls[-1].kw["message_id"] == menu_id and "✅ <b>Верно!</b>" in screen(bot).text
    assert not msg.answers
    sends = [c for c in bot.calls if c.kind == "send"]
    assert len(sends) <= 1  # только самый первый экран (дальше всё правками)


def test_text_answer_wrong_and_show_answer(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    lesson = content.get("lat-02")
    t = first_question(lesson, "text")
    press(bot, f"ls:g:lat-02:{t}")
    say(bot, "ramus mandibula")
    text = screen(bot).text
    assert "❌ <b>Неверно.</b> Твой ответ: «ramus mandibula»" in text
    assert "Почти!" in text and "✔️ Правильно: <b>ramus mandibulae</b>" in text
    t2 = lesson.questions[1]
    press(bot, f"ls:g:lat-02:{t2}")
    press(bot, f"ls:s:lat-02:{t2}")
    assert "👀 Правильный ответ: <b>septum linguae</b>" in screen(bot).text
    press(bot, f"ls:s:lat-02:{t2}")  # повторное нажатие — тот же экран
    assert feat.progress(store.data, "lat-02")["ans"][str(t2)]["shown"] is True


def test_think_block(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    lesson = content.get(LAT)
    last = len(lesson.blocks) - 1
    press(bot, f"ls:g:{LAT}:{last}")
    assert "<b>🤔 Задача на подумать</b>" in screen(bot).text
    assert "🏁 Итог" in [b.text for b in buttons(screen(bot).markup)]
    say(bot, "4 склонение, средний род")
    text = screen(bot).text
    assert "📝 Твой ответ: «4 склонение, средний род»" in text and "✅ Совпадает с эталоном!" in text
    assert "💡 <b>Разбор:</b>" in text
    press(bot, f"ls:t:{LAT}:{last}")
    press(bot, f"ls:s:{LAT}:{last}")
    assert "💡 <b>Разбор:</b>" in screen(bot).text and "📝" not in screen(bot).text


def answer_all(bot, lesson, right=True):
    for index in lesson.questions:
        block = lesson.blocks[index]
        if block["type"] == "choice":
            k = block["correct"] if right else (block["correct"] + 1) % len(block["options"])
            press(bot, f"ls:a:{lesson.id}:{index}:{k}")
        else:
            press(bot, f"ls:g:{lesson.id}:{index}")
            say(bot, block["accept"][0] if right else "неправильно")


# ---------------------------------------------------------------- прогресс: усвоено / скрыть / вернуть
def test_learned_hidden_toggle_unlearn_reset(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    lesson = content.get(LAT)
    answer_all(bot, lesson)
    press(bot, f"ls:g:{LAT}:{len(lesson.blocks)}")
    text = screen(bot).text
    assert "🏁 <b>Итог</b>: верно 5 из 5" in text and "✅ <b>Урок усвоен</b>" in text
    assert feat.is_learned(store.data, LAT)
    press(bot, "ls:A")
    cbs = callbacks(screen(bot).markup)
    assert f"ls:o:{LAT}" not in cbs and "ls:l:a" in cbs
    assert "👁 Показать усвоенные (1)" in [b.text for b in buttons(screen(bot).markup)]
    press(bot, "ls:l:a")
    assert f"ls:o:{LAT}" in callbacks(screen(bot).markup)
    assert "🙈 Скрыть усвоенные" in [b.text for b in buttons(screen(bot).markup)]
    assert "✅" in next(b.text for b in buttons(screen(bot).markup) if b.callback_data == f"ls:o:{LAT}")
    # открыть усвоенный → итог с «Повторить» / «Вернуть в список»
    press(bot, f"ls:o:{LAT}")
    labels = [b.text for b in buttons(screen(bot).markup)]
    assert {"🔁 Повторить", "↩️ Вернуть в список", "🗑 Сбросить прогресс урока"} <= set(labels)
    # повторить: ответы стёрты, отметка остаётся
    press(bot, f"ls:p:{LAT}")
    assert "Блок 1/9" in screen(bot).text
    entry = feat.progress(store.data, LAT)
    assert entry["ans"] == {} and entry["done"]
    # вернуть в список
    press(bot, f"ls:u:{LAT}")
    assert f"ls:o:{LAT}" in callbacks(screen(bot).markup) and "снова в списке" in screen(bot).text
    assert not feat.is_learned(store.data, LAT)
    # сброс с подтверждением
    press(bot, f"ls:m:{LAT}")
    press(bot, f"ls:r:{LAT}")
    assert "Да, сбросить" in [b.text for b in buttons(screen(bot).markup)]
    assert feat.is_learned(store.data, LAT)
    press(bot, f"ls:R:{LAT}")
    assert LAT not in store.data["lessons"]
    assert f"ls:o:{LAT}" in callbacks(screen(bot).markup)
    # прогресс сохранён на диск
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert LAT not in saved.get("lessons", {})


def test_not_enough_correct_not_learned(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    lesson = content.get(LAT)
    answer_all(bot, lesson, right=False)
    press(bot, f"ls:g:{LAT}:{len(lesson.blocks)}")
    text = screen(bot).text
    assert "верно 0 из 5" in text and "Урок усвоен" not in text
    labels = [b.text for b in buttons(screen(bot).markup)]
    assert {"✅ Отметить усвоенным", "➡️ К вопросу 1", "🔁 Пройти заново"} <= set(labels)
    assert "🗑 Сбросить прогресс урока" not in labels
    assert not feat.is_learned(store.data, LAT)
    press(bot, f"ls:m:{LAT}")
    assert feat.is_learned(store.data, LAT)


def test_all_learned_message(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for lesson in content.all_lessons():
        press(bot, f"ls:m:{lesson.id}")
    press(bot, "ls:A")
    assert "🎉 Все уроки усвоены!" in screen(bot).text
    assert callbacks(screen(bot).markup)[0] == "ls:l:a"
    press(bot, "ls")
    assert "🎉 Все уроки усвоены!" in screen(bot).text


def test_resume_position(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, f"ls:g:{LAT}:3")
    press(bot, "ls:x")  # «Выйти» → «Сегодня»: урок помечен начатым
    assert any(x.text.startswith("▶️ 🏛 Латынь 1.") for x in buttons(screen(bot).markup))
    press(bot, "ls:A")
    assert "▶️ 1." in screen(bot).text and "блок 4/9" in screen(bot).text
    press(bot, f"ls:o:{LAT}")
    assert "Блок 4/9" in screen(bot).text


def test_corrupt_progress_tolerated(use_data):
    data = use_data(LIVE_BACKUP)
    data["lessons"] = {LAT: {"pos": "x", "ans": [1], "done": 5}, "lat-02": "мусор"}
    bot = FakeBot()
    press(bot, "ls")
    press(bot, f"ls:o:{LAT}")
    assert "Блок 1/9" in screen(bot).text
    data["lessons"] = []
    press(bot, "ls")
    press(bot, f"ls:a:{LAT}:3:2")
    assert isinstance(store.data["lessons"], dict)


# ---------------------------------------------------------------- все кнопки рабочие
def test_crawl_all_lesson_screens(use_data, caplog):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    caplog.set_level(logging.WARNING)
    seen, queue = set(), deque(["ls"])
    produced = set()
    while queue:
        data = queue.popleft()
        if data in seen or data == "home":
            continue
        seen.add(data)
        press(bot, data)
        shot = screen(bot)
        for cb in callbacks(shot.markup):
            assert len(cb.encode()) <= 64, cb
            REGISTRY.check_callback(cb)
            assert REGISTRY.handler(cb.split("|")[0].split(":")[0]) is not None, cb
            produced.add(cb)
            if cb.startswith("ls"):
                queue.append(cb)
        assert "home" in callbacks(shot.markup), data
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, [r.getMessage() for r in errors]
    actions = {c.split(":")[1] for c in produced if c.startswith("ls:")}
    assert {"o", "g", "a", "s", "t", "m", "p", "u", "r", "R", "x", "l", "A", "P"} <= actions
    assert len(seen) > 150


# ---------------------------------------------------------------- крайние случаи
def test_stale_and_missing_ids(use_data, caplog):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    cb = press(bot, "ls:o:nope")
    assert cb.answers[-1][0] == "Урок не найден"
    assert "Урок не найден" in screen(bot).text and "ls:o:lat-01" in callbacks(screen(bot).markup)
    for data in [f"ls:g:{LAT}:999", f"ls:g:{LAT}:-5", f"ls:g:{LAT}:abc", f"ls:a:{LAT}:0:1",
                 f"ls:a:{LAT}:3:99", f"ls:a:{LAT}:3:x", f"ls:s:{LAT}:0", f"ls:t:{LAT}:77", "ls:zz:lat-01", "ls:o",
                 "ls:v", "ls:v:abc", "ls:v:20250101", "ls:v:20261399"]:
        press(bot, data)
        assert screen(bot) is not None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    # «Далее» за последним блоком = итог
    press(bot, f"ls:g:{LAT}:999")
    assert "🏁 <b>Итог</b>" in screen(bot).text


def test_double_tap_not_modified_is_silent(use_data, caplog):
    use_data(LIVE_BACKUP)

    class SameBot(FakeBot):
        async def edit_message_text(self, text, chat_id, message_id, reply_markup=None, **kw):
            if self.messages.get(message_id, (None,))[0] == text:
                raise TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: message is not modified")
            await super().edit_message_text(text, chat_id, message_id, reply_markup, **kw)

    bot = SameBot()
    press(bot, "ls")
    press(bot, f"ls:a:{LAT}:3:2")
    before = len(bot.calls)
    cb = press(bot, f"ls:a:{LAT}:3:2")
    assert len(bot.calls) == before  # ни нового сообщения, ни ошибки
    assert not any(alert for _, alert in cb.answers)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("text, note", [
    ("😀", "Не вижу ответа"),
    ("...", "Не вижу ответа"),
    ("x" * 1000, "Слишком длинно"),
])
def test_empty_or_long_text_not_recorded(use_data, text, note):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    lesson = content.get(LAT)
    t = first_question(lesson, "text")
    press(bot, f"ls:g:{LAT}:{t}")
    say(bot, text)
    assert note in screen(bot).text
    assert str(t) not in feat.progress(store.data, LAT)["ans"]
    assert pending.get(111).name == feat.STATE


def test_text_on_explain_and_choice_blocks(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, f"ls:o:{LAT}")
    say(bot, "привет")
    assert "Здесь ответ не нужен" in screen(bot).text
    press(bot, f"ls:g:{LAT}:3")
    say(bot, "совсем не то")
    assert "напиши номер 1–5" in screen(bot).text
    say(bot, "3")
    assert "✅ <b>Верно!</b>" in screen(bot).text
    say(bot, "4")
    assert "Ответ уже есть" in screen(bot).text
    press(bot, f"ls:g:{LAT}:{len(content.get(LAT).blocks)}")
    say(bot, "ещё текст")
    assert "Урок закончен" in screen(bot).text


def test_sticker_photo_deleted_and_hinted(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, f"ls:g:{LAT}:4")
    menu = LIVE_MENU[111]
    for attr in ("sticker", "photo", "voice"):
        msg = FakeMessage(bot, None)
        setattr(msg, attr, object())
        assert feat._in_lesson(msg)
        before = len(bot.calls)
        run(feat.on_not_text(msg))
        kinds = [c.kind for c in bot.calls[before:]]
        assert kinds == ["delete", "edit"] or kinds == ["delete"], kinds  # «not modified» — тоже ок
        assert "Ответ нужен текстом" in bot.messages[menu][0]
    other = FakeMessage(bot, None, user_id=222)
    assert not feat._in_lesson(other)
    press(bot, "ls")
    assert not feat._in_lesson(FakeMessage(bot, None))


def test_dispatcher_sticker_and_photo_in_lesson(use_data):
    """Через настоящий диспетчер: во время урока фото/стикер ловит урок (не admin)."""
    use_data(LIVE_BACKUP)
    session = FakeSession()
    bot = Bot("123456:ABCDEFabcdef-fake", session=session)
    for feature in REGISTRY.features:  # роутеры модульные: отцепляем от диспетчера прошлого теста
        if feature.router is not None:
            feature.router._parent_router = None
    dispatcher = app.build_dispatcher()
    names = [r.name for r in dispatcher.sub_routers]
    assert names.index("lessons-input") < names.index("admin-files")
    pending.start(111, feat.STATE, id=LAT, n=4, keep=("ls",), ttl=feat.INPUT_TTL)
    LIVE_MENU[111] = 500
    base = {"date": 1, "chat": {"id": 111, "type": "private"}, "from": {"id": 111, "is_bot": False, "first_name": "O"}}

    async def go():
        sticker = {"file_id": "S", "file_unique_id": "s", "type": "regular", "width": 1, "height": 1,
                   "is_animated": False, "is_video": False}
        await dispatcher.feed_update(bot, Update.model_validate({"update_id": 1, "message": dict(base, message_id=11, sticker=sticker)}))
        photo = [{"file_id": "P", "file_unique_id": "p", "width": 1, "height": 1}]
        await dispatcher.feed_update(bot, Update.model_validate({"update_id": 2, "message": dict(base, message_id=12, photo=photo)}))
        await bot.session.close()

    try:
        run(go())
    finally:
        for feature in REGISTRY.features:
            if feature.router is not None:
                feature.router._parent_router = None
    sent = [type(m).__name__ for m in session.requests]
    assert sent.count("DeleteMessage") == 2 and "SendMessage" not in sent, sent
    assert sent.count("EditMessageText") == 2


def test_command_during_lesson_cancels(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, f"ls:g:{LAT}:4")
    run(app._command_handler(REGISTRY.commands["today"][1])(FakeMessage(bot, "/today")))
    assert pending.get(111) is None
    assert screen(bot).text.startswith(home.CANCELLED)
    press(bot, f"ls:g:{LAT}:4")
    run(app.on_unknown_command(FakeMessage(bot, "/foo")))
    assert pending.get(111) is None and home.CANCELLED in screen(bot).text


def test_failing_delete_does_not_break(use_data, caplog):
    use_data(LIVE_BACKUP)

    class NoDelete(FakeBot):
        async def delete_message(self, chat_id, message_id):
            raise TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: message can't be deleted")

    bot = NoDelete()
    lesson = content.get(LAT)
    t = first_question(lesson, "text")
    press(bot, f"ls:g:{LAT}:{t}")
    say(bot, "i")
    assert "✅ <b>Верно!</b>" in screen(bot).text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_no_conflict_with_score_input(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, f"ls:g:{LAT}:4")
    assert pending.get(111).name == feat.STATE
    press(bot, "sc")
    assert pending.get(111) is None  # кнопка другого раздела выходит из урока
    from rudn import tuis

    course = tuis.courses()[0]
    item = course["items"][0]
    press(bot, f"sc:e:{course['key']}:{item['key']}")
    assert pending.get(111).name == "sc_input"
    press(bot, "ls")  # урок не перехватывает ввод баллов и сам его закрывает
    assert pending.get(111) is None
    press(bot, f"sc:e:{course['key']}:{item['key']}")
    say(bot, "2")
    assert not feat.progress(store.data, LAT)["ans"]
    assert "сохранено" in screen(bot).text


def test_lesson_input_ttl_longer_than_default(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, f"ls:g:{LAT}:4")
    state = pending._states[111]
    state.ts -= pending.TTL_SECONDS + 60  # 16 минут читаем объяснение — ввод ещё ждёт
    assert pending.get(111) is state
    state.ts -= feat.INPUT_TTL
    msg = say(bot, "i")
    assert msg.answers and "устарел" in msg.answers[0]


def test_returned_lesson_not_stale_summary(use_data):
    """Регресс: «↩️ Вернуть в список» → снова открыть = блок 1, а не «Урок усвоен»."""
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    lesson = content.get(LAT)
    answer_all(bot, lesson)
    press(bot, f"ls:g:{LAT}:{len(lesson.blocks)}")
    assert feat.is_learned(store.data, LAT)
    press(bot, f"ls:u:{LAT}")
    assert "откроется с блока 1" in screen(bot).text
    assert f"ls:o:{LAT}" in callbacks(screen(bot).markup)
    press(bot, f"ls:o:{LAT}")
    text = screen(bot).text
    assert "Блок 1/9" in text and "Урок усвоен" not in text
    assert not feat.is_learned(store.data, LAT)
    # итог без новых ответов отражает текущее состояние, а не прошлое «усвоено»
    press(bot, f"ls:g:{LAT}:{len(lesson.blocks)}")
    text = screen(bot).text
    assert "Урок усвоен" not in text and "верно 0 из 5" in text and "Прошлый проход: верно 5 из 5" in text
    labels = [b.text for b in buttons(screen(bot).markup)]
    assert {"✅ Отметить усвоенным", "🔁 Пройти заново"} <= set(labels)
    press(bot, f"ls:l:a")
    assert f"ls:o:{LAT}" in callbacks(screen(bot).markup) and not feat.is_learned(store.data, LAT)
    # «🔁 Пройти заново» — с подтверждением, затем блок 1
    press(bot, f"ls:r:{LAT}:1")
    assert "Да, заново" in [b.text for b in buttons(screen(bot).markup)]
    press(bot, f"ls:o:{LAT}")  # «Нет» → обратно к итогу
    assert "🏁 <b>Итог</b>" in screen(bot).text
    press(bot, f"ls:R:{LAT}:1")
    assert "Блок 1/9" in screen(bot).text and LAT not in store.data["lessons"]
    # пройти снова на 5/5 → усвоен автоматически
    answer_all(bot, lesson)
    press(bot, f"ls:g:{LAT}:{len(lesson.blocks)}")
    assert "✅ <b>Урок усвоен</b>" in screen(bot).text
