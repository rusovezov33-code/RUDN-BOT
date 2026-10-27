"""Регрессии аудита 2026-10-27: сверка предметов с расписанием."""
import asyncio
from datetime import date, datetime

from conftest import LIVE_BACKUP
from fakes import FakeBot, FakeCallback, callbacks
from rudn.core import app, config, timeutil as tu


def test_every_schedule_subject_has_a_card(use_data):
    data = use_data(LIVE_BACKUP)
    names = [s["name"] for s in data["subjects"]]
    for lessons in config.schedule().days.values():
        for lesson in lessons:
            assert [n for n in names if tu.subjects_match(lesson.subject, n)], lesson.subject


def test_intro_card_sees_wednesday_practice():
    slots = tu.slots_for_subject("Введение в специальность")
    assert {code for code, _ in slots} == {"ПН", "СР"}
    assert tu.upcoming_lesson_dates("Введение в специальность", date(2026, 10, 27))[0] == date(2026, 10, 28)


def test_economics_card_kept_and_appended(use_data):
    data = use_data(LIVE_BACKUP)
    assert data["subjects"][-1]["name"] == "Экономика"
    assert tu.upcoming_lesson_dates("Экономика", date(2026, 10, 27))[0] == date(2026, 10, 31)


def test_nearest_skips_finished_lessons_today():
    moment = datetime(2026, 10, 27, 21, 0, tzinfo=tu.MSK)
    assert tu.upcoming_lesson_dates("Химия", moment.date(), moment=moment)[0] == date(2026, 11, 3)
    moment = datetime(2026, 10, 27, 19, 40, tzinfo=tu.MSK)
    assert tu.upcoming_lesson_dates("Химия", moment.date(), moment=moment)[0] == date(2026, 10, 27)


def test_sunday_day_screen_has_no_duplicate_button(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 11, 1, 21, 0, tzinfo=tu.MSK))
    bot = FakeBot()
    asyncio.run(app.on_callback(FakeCallback(bot, "d:t")))
    cbs = callbacks(bot.last_screen().markup)
    assert cbs.count("d:2026-11-02") == 1


# ---------------------------------------------------------------- раунд 2
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from conftest import LEGACY  # noqa: E402
from fakes import FakeMessage, buttons, fake_document  # noqa: E402
from rudn.core import pending, tasks, ui  # noqa: E402
from rudn.core.registry import REGISTRY  # noqa: E402
from rudn.core.storage import encode, store  # noqa: E402
from rudn.features import admin  # noqa: E402
from test_screens import crawl, seed  # noqa: E402

ZIP_BACKUP = Path("/tmp/audit/RUDN-BOT-work-2026-10-26/data-backup/data-live-2026-10-26-1600.json")


def run(coro):
    return asyncio.run(coro)


def press(bot, data, user_id=111):
    cb = FakeCallback(bot, data, user_id=user_id)
    run(app.on_callback(cb))
    return cb


@pytest.fixture
def toasts(monkeypatch):
    """Ловит тосты, которые потерялись бы (answer() после show())."""
    dropped = []
    original = ui.Ctx.answer

    async def answer(self, text=None, alert=False):
        if self.callback is not None and self._answered and text:
            dropped.append((self.callback.data, text))
        return await original(self, text, alert)

    monkeypatch.setattr(ui.Ctx, "answer", answer)
    return dropped


# --- #8: конец пары — не включительно
def test_lesson_end_minute_is_exclusive(use_data):
    assert tu.lesson_state("9:00–10:20", datetime(2026, 10, 27, 10, 19, tzinfo=tu.MSK)) == "now"
    assert tu.lesson_state("9:00–10:20", datetime(2026, 10, 27, 10, 20, tzinfo=tu.MSK)) == "past"
    use_data(LIVE_BACKUP)
    for hour, minute in [(10, 20), (11, 50), (14, 50), (17, 50), (20, 50)]:
        tu.set_now(datetime(2026, 10, 27, hour, minute, tzinfo=tu.MSK))
        bot = FakeBot()
        press(bot, "home")
        assert "ещё 0" not in bot.last_screen().text
        press(bot, "d:t")
        assert "ещё 0" not in bot.last_screen().text


# --- #6: кнопка «Неделя» в воскресенье ведёт туда же, куда меню
def test_sunday_week_button_matches_menu(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 11, 1, 12, 0, tzinfo=tu.MSK))
    bot = FakeBot()
    press(bot, "d:t")
    assert "w:2026-11-02" in callbacks(bot.last_screen().markup)
    press(bot, "d:2026-10-31")
    assert "w:2026-10-26" in callbacks(bot.last_screen().markup)


# --- #7: Мед. информатика в четверг — одно занятие
def test_thursday_med_informatics_single_lesson():
    thu = [l for l in tu.lessons_for(date(2026, 10, 29)) if l.subject == "Медицинская информатика"]
    assert [l.time for l in thu] == ["12:00–14:10"]
    moment = datetime(2026, 10, 29, 13, 25, tzinfo=tu.MSK)
    assert tu.lesson_state(thu[0].time, moment) == "now"
    tue = tu.lessons_for(date(2026, 10, 27))[0]
    assert tue.subject == "Латинский язык" and "подгруппа А — верхняя неделя, Б — нижняя" in tue.place


# --- #9: обрезка длинной строки без двойного экранирования
def test_clip_html_long_line_escapes_once():
    line = "<b>Заголовок</b> " + "a &amp; b &lt;c&gt; " * 600
    out = ui.clip_html(line)
    assert len(out) <= ui.TEXT_LIMIT
    assert "&amp;amp;" not in out and "<b>" not in out
    body = out.split("\n")[0]
    assert not re.search(r"&(?!amp;|lt;|gt;|quot;|#x27;)", body), body[-20:]
    assert body.startswith("Заголовок a &amp; b &lt;c&gt;")


# --- #10: сданные экзамены видны и возвращаются
def test_done_exam_undo_and_history(use_data, toasts):
    data = use_data(LIVE_BACKUP)
    si = next(i for i, s in enumerate(data["subjects"]) if s["name"] == "Химия")
    exam = tasks.new_item(data, "exam", si, "", date(2027, 1, 15), exam_type="exam")
    hw = tasks.new_item(data, "hw", si, "задачи", date(2026, 10, 30))
    store.save()
    bot = FakeBot()
    press(bot, "ex")
    press(bot, f"ex:ok:{exam['id']}")
    assert exam["done"] is True
    undo = f"ex:un:{exam['id']}:u"
    assert undo in callbacks(bot.last_screen().markup)
    press(bot, undo)
    assert exam["done"] is False and "Экзамены и зачёты" in bot.last_screen().text
    tasks.mark_done(exam)
    tasks.mark_done(hw)
    store.save()
    press(bot, "dl:h:a")
    assert f"dl:un:{hw['id']}:a" in callbacks(bot.last_screen().markup)
    assert f"dl:un:{exam['id']}:a" not in callbacks(bot.last_screen().markup)
    press(bot, f"dl:h:s{si}")
    assert f"dl:un:{exam['id']}:s{si}" in callbacks(bot.last_screen().markup)
    press(bot, "ex:p")
    assert f"ex:un:{exam['id']}" in callbacks(bot.last_screen().markup)
    assert not toasts


# --- новое: «➕ Экзамен/Зачёт» с экрана экзаменов
@pytest.mark.parametrize("kind", ["exam", "credit"])
def test_add_exam_from_exams_screen(use_data, kind):
    data = use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "ex")
    assert f"add:k:{kind}" in callbacks(bot.last_screen().markup)
    cb = press(bot, f"add:k:{kind}")
    assert not any(text and "устарел" in text for text, _ in cb.answers)
    assert "Выбери предмет" in bot.last_screen().text
    si = next(i for i, s in enumerate(data["subjects"]) if s["name"] == "Химия")
    press(bot, f"add:su:{si}")
    run(app.on_text(FakeMessage(bot, "15.01")))
    run(app.on_text(FakeMessage(bot, "10:00")))
    item = store.data["homework"][-1]
    assert item["kind"] == "exam" and item["exam_type"] == kind and item["date"] == "2027-01-15"


# --- новое: устаревшие индексы предмета в шагах добавления
def test_add_with_stale_subject_index(use_data, caplog):
    use_data(LIVE_BACKUP)
    caplog.set_level(logging.WARNING)
    bot = FakeBot()
    press(bot, "add")
    press(bot, "add:k:hw")
    cb = press(bot, "add:su:999")
    assert cb.answers[0] == ("Предмет не найден", True)
    cb = press(bot, "add:k:lab:999")
    assert cb.answers[0] == ("Предмет не найден", True)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# --- новое: команда прерывает незаконченный ввод
@pytest.mark.parametrize("command", ["today", "deadlines", "start"])
def test_command_cancels_pending_add(use_data, command):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for data in ["add", "add:k:hw", "add:su:2", "add:d:2026-10-28"]:
        press(bot, data)
    assert pending.get(111) is not None
    run(app._command_handler(REGISTRY.commands[command][1])(FakeMessage(bot, f"/{command}")))
    assert pending.get(111) is None
    run(app.on_text(FakeMessage(bot, "просто текст")))
    assert store.data["homework"] == []


def test_cancel_command_still_reports(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for data in ["add", "add:k:hw"]:
        press(bot, data)
    msg = FakeMessage(bot, "/cancel")
    run(app._command_handler(REGISTRY.commands["cancel"][1])(msg))
    assert pending.get(111) is None and bot.last_screen().text.startswith("🎓 <b>РУДН")


# --- загрузка файлов
def _send_doc(bot, name, content=b"x"):
    msg = FakeMessage(bot, "")
    msg.text = None
    msg.document = fake_document(bot, name, content)
    run(admin.handle_document(msg))
    return msg


def test_upload_into_section(use_data):
    data = use_data(LIVE_BACKUP)
    bot = FakeBot()
    _send_doc(bot, "лекция 1.pdf")
    assert "up:s:3" in callbacks(bot.last_screen().markup)
    press(bot, "up:s:3")
    assert "up:sec:3:0" in callbacks(bot.last_screen().markup)
    press(bot, "up:back")
    press(bot, "up:s:3")
    press(bot, "up:sec:3:1")
    files = data["subjects"][3]["sections"][1]["files"]
    assert files[-1]["name"] == "лекция 1.pdf"
    assert "s:3:1" in callbacks(bot.last_screen().markup)
    press(bot, "s:3:1")
    assert "лекция 1.pdf" in " ".join(btn.text for btn in buttons(bot.last_screen().markup))
    cb = press(bot, "up:sec:3:1")  # повторно — сессия уже закрыта
    assert cb.answers[0][1] is True


def test_upload_to_subject_with_empty_sections(use_data):
    data = use_data(LEGACY)
    econ = next(i for i, s in enumerate(data["subjects"]) if s["name"] == "Экономика")
    assert data["subjects"][econ]["sections"], "пустые разделы заполняются шаблоном"
    bot = FakeBot()
    _send_doc(bot, "econ.pdf")
    press(bot, f"up:s:{econ}")
    assert f"up:sec:{econ}:0" in callbacks(bot.last_screen().markup)


def test_json_that_is_not_a_db_can_be_uploaded(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    _send_doc(bot, "data_structures.json", b'{"a": 1}')
    assert "В какой предмет" in bot.last_screen().text
    assert pending.get(111).name == "upload"


def test_restore_prompt_and_cancel(use_data):
    data = use_data(LIVE_BACKUP)
    bot = FakeBot()
    _send_doc(bot, "data-old.json", json.dumps(LEGACY, ensure_ascii=False).encode())
    assert "Заменить текущую базу" in bot.last_screen().text
    press(bot, "x")
    assert pending.get(111) is None
    assert len(store.data["subjects"]) == len(data["subjects"])
    cb = press(bot, "rs:yes")  # устаревшая кнопка
    assert cb.answers[0][0] == "⌛ Сессия истекла"


# --- напоминания: удаление с подтверждением
def test_delete_reminder(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    press(bot, "d:2026-10-26", user_id=222)
    assert not [c for c in callbacks(bot.last_screen().markup) if c.startswith("evd:")]
    press(bot, "d:2026-10-26")
    assert "evd:2026-10-26:0" in callbacks(bot.last_screen().markup)
    press(bot, "evd:2026-10-26:0")
    assert "Удалить напоминание" in bot.last_screen().text and store.data["events"]
    press(bot, "evd:2026-10-26:0:y")
    assert store.data["events"] == []
    store._data = None
    assert store.load()["events"] == []
    cb = press(bot, "evd:2026-10-26:0:y")
    assert cb.answers[0] == ("Напоминание уже удалено", True)
    cb = press(bot, "evd:2026-10-26:0", user_id=222)
    assert cb.answers[0][0] == "Нет доступа"


# --- кривые даты в callback_data
@pytest.mark.parametrize("data", ["d:2026-13-45", "w:garbage", "d:", "w:2026-02-30", "evd:xx:1", "ex:ok:abc", "dl:ok:zz", "s:abc", "s:-1", "s:1:-1", "p:-1x"])
def test_garbage_callbacks_no_errors(use_data, caplog, data):
    use_data(LIVE_BACKUP)
    caplog.set_level(logging.WARNING)
    bot = FakeBot()
    cb = press(bot, data)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], data
    assert not any(text and "Ошибка" in text for text, _ in cb.answers)
    assert bot.last_screen() is not None or any(alert for _, alert in cb.answers)


# --- задание с устаревшим subject_idx показывается по имени
def test_belongs_to_prefers_subject_name(use_data):
    data = use_data(LIVE_BACKUP)
    chem = next(i for i, s in enumerate(data["subjects"]) if s["name"] == "Химия")
    phys = next(i for i, s in enumerate(data["subjects"]) if s["name"] == "Физика")
    item = {"id": 1, "subject": "Химия", "subject_idx": phys, "text": "x", "done": False}
    assert tasks.belongs_to(item, data, chem) and not tasks.belongs_to(item, data, phys)
    orphan = {"id": 2, "subject": "Удалённый предмет", "subject_idx": phys, "text": "x"}
    assert tasks.belongs_to(orphan, data, phys)


# --- длинные тексты
def test_long_texts_are_clipped_and_valid(use_data):
    use_data(LIVE_BACKUP)
    bot = FakeBot()
    for data in ["add", "add:k:hw", "add:su:3", "add:d:2026-10-27"]:
        press(bot, data)
    run(app.on_text(FakeMessage(bot, "<b>&" * 2000)))
    item = store.data["homework"][-1]
    assert len(item["text"]) == 2000
    for data in ["add", "add:k:ev", "add:d:2026-10-27"]:
        press(bot, data)
    run(app.on_text(FakeMessage(bot, "строка\n" * 600)))
    for data in ["d:2026-10-27", "dl", "w:2026-10-26", "s:3", "home", "dl:s:3"]:
        press(bot, data)
        text = bot.last_screen().text
        assert len(text) <= 4096, data
        assert "&amp;amp;" not in text


# --- полный обход в граничные моменты времени
MOMENTS = [
    datetime(2026, 10, 27, 13, 0),  # вторник, перерыв
    datetime(2026, 10, 27, 14, 50),  # минута окончания пары
    datetime(2026, 10, 29, 13, 25),  # четверг внутри объединённого занятия
    datetime(2026, 10, 31, 23, 59),  # суббота перед воскресеньем
    datetime(2026, 11, 1, 23, 59),  # воскресенье, полночь недели
    datetime(2026, 11, 2, 0, 0),  # понедельник нижней недели
]


@pytest.mark.parametrize("moment", MOMENTS, ids=lambda m: m.strftime("%a-%H%M"))
def test_deep_crawl_at_boundaries(use_data, caplog, toasts, moment):
    data = use_data(LIVE_BACKUP)
    seed(data)
    data["events"].append({"day": 1, "month": 11, "year": 2026, "date": "2026-11-01", "text": "воскресное <дело> & ещё"})
    store.save()
    tu.set_now(moment.replace(tzinfo=tu.MSK))
    caplog.set_level(logging.WARNING)
    bot = FakeBot()
    seen = run(crawl(bot, 111, limit=800))
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors
    assert not toasts
    assert len(seen) > 80


def test_crawl_with_empty_data(use_data, caplog):
    use_data({})
    caplog.set_level(logging.WARNING)
    for user in (111, 222):
        seen = run(crawl(FakeBot(), user))
        assert len(seen) > 20
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# --- старт на живом бэкапе (из архива)
@pytest.mark.skipif(not ZIP_BACKUP.exists(), reason="нет распакованного архива")
def test_startup_with_zip_backup(use_data):
    data = use_data(ZIP_BACKUP)
    assert len(data["subjects"]) == 15 and all(s["sections"] for s in data["subjects"])
    first = encode(data)
    store._data = None
    assert encode(store.load()) == first  # нормализация идемпотентна
    bot = FakeBot()
    press(bot, "home")
    assert "Поработать над ИИ." in bot.last_screen().text  # напоминание на 26.10
    tu.set_now(datetime(2026, 10, 27, 8, 0, tzinfo=tu.MSK))
    press(bot, "home")
    text = bot.last_screen().text
    assert "Поработать над ИИ." not in text and "Латинский язык" in text


def test_bad_link_urls_are_hidden(use_data):
    data = use_data(LIVE_BACKUP)
    data["pages"][0]["links"] = [{"title": "битая", "url": "rudn.ru/page"}, {"title": "ок", "url": "https://rudn.ru"}]
    data["subjects"][3]["sections"][5]["links"] = [{"title": "x", "url": "javascript:alert(1)"}]
    store.save()
    bot = FakeBot()
    press(bot, "p:0")
    assert [btn.url for btn in buttons(bot.last_screen().markup) if btn.url] == ["https://rudn.ru"]
    press(bot, "s:3:5")
    assert all(ui.is_url(btn.url) for btn in buttons(bot.last_screen().markup) if btn.url)
