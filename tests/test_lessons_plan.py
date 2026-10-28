"""🗓 План уроков по дням: раскладка, лимиты, тяжёлые/лёгкие дни, пересчёт по
прогрессу, новые уроки и сроки, крайние случаи; экраны «Сегодня»/«План»."""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest

from conftest import LIVE_BACKUP
from fakes import FakeBot, FakeCallback, buttons, callbacks, summary_text
from rudn import lessons as content
from rudn import lessons_plan as P
from rudn.core import app, timeutil as tu
from rudn.core.registry import REGISTRY
from rudn.core.storage import store
from rudn.core.ui import LIVE_MENU
from rudn.features import lessons as feat

D = date(2026, 10, 28)  # среда
DUE = date(2026, 11, 3)
LIVE_PAIRS = {date(2026, 10, 26): 2, date(2026, 10, 27): 5, date(2026, 10, 28): 5, date(2026, 10, 29): 5,
              date(2026, 10, 30): 4, date(2026, 10, 31): 4, date(2026, 11, 1): 0, date(2026, 11, 2): 3,
              date(2026, 11, 3): 3}


@dataclass(eq=False)
class L:
    id: str
    subject: str
    order: int
    minutes: int = 15


def lat(n, start=1):
    return [L(f"lat-{i:02d}", "Латынь", i) for i in range(start, start + n)]


def plan_of(lessons, now, due=None, pairs=None, learned=None):
    due = due or {}
    learned = learned or {}
    pairs = pairs if pairs is not None else {}
    return P.build(
        lessons, now,
        lambda d: pairs.get(d, 0) if isinstance(pairs, dict) else pairs(d),
        lambda l: due.get(l.id),
        lambda l: learned.get(l.id),
    )


def layout(plan):
    return {d.day: [l.id for l in d.items] for d in plan.days.values() if d.items}


def check_invariants(plan, lessons):
    seen = []
    for d in sorted(plan.days):
        items = plan.days[d].items
        assert len(items) + len(plan.days[d].done) <= P.HARD_CAP + 1, (d, items)
        for subject in {l.subject for l in items}:
            if not plan.warnings:
                assert sum(l.subject == subject for l in items) <= P.SUBJECT_CAP, (d, items)
        seen += items
    assert len(seen) == len(set(map(id, seen))), "урок поставлен дважды"
    for subject in {l.subject for l in seen}:
        orders = [l.order for l in seen if l.subject == subject]
        assert orders == sorted(orders), (subject, orders)


def live_lessons():
    return lat(8) + [L("chem-01", "Химия", 1)]


def live_due():
    return {l.id: ("КР" if l.subject == "Латынь" else "ЛР №5", DUE) for l in live_lessons()}


# ---------------------------------------------------------------- текущие данные
@pytest.mark.parametrize("now", [datetime(2026, 10, 27, 21, 20), datetime(2026, 10, 28, 8, 0)])
def test_current_data_plan(now):
    lessons = live_lessons()
    plan = plan_of(lessons, now, live_due(), LIVE_PAIRS)
    assert layout(plan) == {
        date(2026, 10, 28): ["lat-01"],
        date(2026, 10, 29): ["lat-02"],
        date(2026, 10, 30): ["lat-03", "lat-04"],
        date(2026, 10, 31): ["lat-05", "lat-06"],
        date(2026, 11, 1): ["chem-01", "lat-07", "lat-08"],
    }
    assert [g.label for g in plan.days[date(2026, 11, 2)].review] == ["КР; ЛР №5"]
    assert not plan.days[date(2026, 11, 2)].items and not plan.days.get(DUE).items
    assert plan.days[DUE].due
    assert not plan.warnings
    check_invariants(plan, lessons)
    if now.hour >= 21:
        assert plan.start == date(2026, 10, 28) and not plan.today_day.items


def test_live_feature_plan_matches(use_data):
    data = use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 10, 28, 8, 0, tzinfo=tu.MSK))
    plan = feat.build_plan(data)
    assert {d: ids for d, ids in layout(plan).items()} == {
        date(2026, 10, 28): ["lat-01"], date(2026, 10, 29): ["lat-02"],
        date(2026, 10, 30): ["lat-03", "lat-04"], date(2026, 10, 31): ["lat-05", "lat-06"],
        date(2026, 11, 1): ["chem-01", "lat-07", "lat-08"],
    }
    assert {d: feat.day_pairs(d) for d in LIVE_PAIRS} == LIVE_PAIRS


# ---------------------------------------------------------------- лимиты и нагрузка
def test_heavy_days_get_fewer_lessons():
    lessons = lat(4)
    pairs = {D: 5, D + timedelta(1): 0, D + timedelta(2): 5, D + timedelta(3): 2}
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), {l.id: ("КР", D + timedelta(5)) for l in lessons}, pairs)
    counts = {d: len(ids) for d, ids in layout(plan).items()}
    assert counts.get(D + timedelta(1), 0) >= counts.get(D, 0)
    assert counts.get(D + timedelta(1), 0) >= counts.get(D + timedelta(2), 0)
    assert max(counts.values()) <= 2
    check_invariants(plan, lessons)


def test_base_caps():
    assert [P.base_cap(p) for p in (0, 1, 4, 5, 7)] == [3, 2, 2, 1, 1]


def test_spread_evenly_not_piled():
    lessons = lat(4)
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), {l.id: ("КР", D + timedelta(10)) for l in lessons}, {})
    assert all(len(ids) == 1 for ids in layout(plan).values())  # 9 дней на 4 урока — по одному


def test_too_many_raises_cap_minimally_with_warning():
    lessons = lat(6) + [L("chem-01", "Химия", 1), L("chem-02", "Химия", 2)]
    due = {l.id: ("КР", D + timedelta(3)) for l in lessons}  # учёба 28–30, повторение 31
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), due, {d: 5 for d in (D + timedelta(i) for i in range(4))})
    counts = [len(ids) for ids in layout(plan).values()]
    assert sum(counts) == 8
    assert max(counts) - min(counts) <= 1  # поднимаем по одному, а не всё на один день
    assert max(counts) <= P.HARD_CAP
    assert plan.warnings
    check_invariants(plan, lessons)


def test_review_day_used_before_raising_cap():
    heavy = {D + timedelta(i): 5 for i in range(5)}
    lessons = lat(3)
    due = {l.id: ("КР", D + timedelta(4)) for l in lessons}
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), due, heavy)
    # 3 тяжёлых дня по 1 уроку хватает → день повторения 31.10 свободен
    assert plan.days[D + timedelta(3)].review and not plan.days[D + timedelta(3)].items
    assert not plan.warnings
    lessons = lat(4)
    due = {l.id: ("КР", D + timedelta(4)) for l in lessons}
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), due, heavy)
    assert sum(len(v) for v in layout(plan).values()) == 4
    assert max(len(v) for v in layout(plan).values()) == 1  # день повторения взял урок, лимит не поднят
    assert plan.warnings and "повторения" in plan.warnings[0]


def test_subject_cap_and_interleave():
    lessons = lat(4) + [L("chem-01", "Химия", 1), L("chem-02", "Химия", 2)]
    due = {l.id: ("КР", D + timedelta(3)) for l in lessons}
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), due, {})  # 3 свободных дня × 3
    for ids in layout(plan).values():
        assert sum(i.startswith("lat") for i in ids) <= 2
    assert any(any(i.startswith("chem") for i in ids) and any(i.startswith("lat") for i in ids)
               for ids in layout(plan).values())
    check_invariants(plan, lessons)


def test_no_timetable_is_free_day():
    def boom(d):
        raise RuntimeError("нет расписания")
    lessons = lat(3)
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), {l.id: ("КР", D + timedelta(2)) for l in lessons}, boom)
    assert plan.days[D].pairs == 0 and plan.days[D].cap == 3
    check_invariants(plan, lessons)


# ---------------------------------------------------------------- пересчёт
def test_missed_day_rolls_forward():
    lessons = live_lessons()
    later = plan_of(lessons, datetime(2026, 10, 30, 9), live_due(), LIVE_PAIRS)  # 28 и 29 пропущены
    ids = [i for v in layout(later).values() for i in v]
    assert ids.count("lat-01") == 1 and min(layout(later)) == date(2026, 10, 30)
    assert layout(later)[date(2026, 10, 30)][0] == "lat-01"
    check_invariants(later, lessons)


def test_done_early_frees_days_and_today_counts():
    lessons = live_lessons()
    learned = {"lat-01": D, "lat-02": D, "lat-03": D}  # сделал три урока в среду
    plan = plan_of(lessons, datetime(2026, 10, 28, 18), live_due(), LIVE_PAIRS, learned)
    assert [l.id for l in plan.today_day.done] == ["lat-01", "lat-02", "lat-03"]
    assert not plan.today_day.items  # сегодня уже сверх лимита — больше не ставим
    assert sum(len(v) for v in layout(plan).values()) == 6
    assert layout(plan)[date(2026, 10, 29)] == ["lat-04"]
    plan = plan_of(lessons, datetime(2026, 10, 29, 9), live_due(), LIVE_PAIRS, learned)  # назавтра
    assert not plan.today_day.done and layout(plan)[date(2026, 10, 29)] == ["lat-04"]
    assert "lat-01" not in [l.id for l in plan.remaining]


def test_new_lessons_and_deadlines_redistribute():
    lessons = live_lessons()
    due = live_due()
    base = plan_of(lessons, datetime(2026, 10, 28, 9), due, LIVE_PAIRS)
    # среди недели добавили 4 урока анатомии к коллоквиуму 05.11 и 3 латыни к той же КР
    extra = [L(f"anat-0{i}", "Анатомия", i) for i in range(1, 5)] + lat(3, start=9)
    for l in extra:
        due[l.id] = ("Коллоквиум", date(2026, 11, 5)) if l.subject == "Анатомия" else ("КР", DUE)
    plan = plan_of(lessons + extra, datetime(2026, 10, 28, 9), due, {**LIVE_PAIRS, date(2026, 11, 4): 3})
    placed = [i for v in layout(plan).values() for i in v]
    assert sorted(placed) == sorted(l.id for l in lessons + extra)
    for d, ids in layout(plan).items():
        assert len(ids) <= P.HARD_CAP
    counts = [len(v) for v in layout(plan).values()]
    assert max(counts) - min(counts) <= 2  # без завала на один день
    assert len(placed) > len([i for v in layout(base).values() for i in v])
    # латынь до 03.11 — раньше анатомии к 05.11
    lat_days = [d for d, ids in layout(plan).items() if any(i.startswith("lat") for i in ids)]
    assert max(lat_days) < DUE
    check_invariants(plan, lessons + extra)
    # новый ранний срок у химии: план сам перестраивается
    due["chem-01"] = ("ЛР №5", date(2026, 10, 30))
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), due, LIVE_PAIRS)
    chem_day = next(d for d, ids in layout(plan).items() if "chem-01" in ids)
    assert chem_day <= date(2026, 10, 29)
    assert date(2026, 10, 29) in plan.days and plan.days[date(2026, 10, 29)].review


def test_undated_and_passed_deadline():
    lessons = lat(3)
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), {}, {})
    assert [len(v) for v in layout(plan).values()] == [1, 1, 1]
    passed = {l.id: ("КР", date(2026, 10, 20)) for l in lessons}
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), passed, {})
    assert [len(v) for v in layout(plan).values()] == [1, 1, 1]
    assert all(not d.due for d in plan.days.values())


def test_all_learned_and_empty():
    lessons = live_lessons()
    learned = {l.id: date(2026, 10, 20) for l in lessons}
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), live_due(), LIVE_PAIRS, learned)
    assert not layout(plan) and not plan.remaining and plan.next_lesson() is None
    assert plan.days[date(2026, 11, 2)].review  # повторение остаётся
    plan = plan_of([], datetime(2026, 10, 28, 9))
    assert not layout(plan) and plan.today_day is not None


def test_deadline_today_and_tomorrow():
    lessons = lat(3)
    tomorrow = {l.id: ("КР", D + timedelta(1)) for l in lessons}
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), tomorrow, {D: 5})
    assert layout(plan) == {D: ["lat-01", "lat-02", "lat-03"]} or sum(map(len, layout(plan).values())) == 3
    assert plan.warnings and plan.days[D].urgent
    today = {l.id: ("КР", D) for l in lessons}
    plan = plan_of(lessons, datetime(2026, 10, 28, 9), today, {D: 5})
    assert sum(map(len, layout(plan).values())) == 3 and set(layout(plan)) == {D}
    assert plan.days[D].due
    # вечер накануне срока: всё равно на сегодня, а не «с завтра»
    plan = plan_of(lessons, datetime(2026, 10, 28, 22), tomorrow, {D: 5})
    assert set(layout(plan)) == {D}


def test_cutoff_after_21():
    lessons = lat(2)
    due = {l.id: ("КР", D + timedelta(5)) for l in lessons}
    assert D in layout(plan_of(lessons, datetime(2026, 10, 28, 20, 59), due))
    late = plan_of(lessons, datetime(2026, 10, 28, 21, 0), due)
    assert D not in layout(late) and late.start == D + timedelta(1)
    assert late.next_lesson().id == "lat-01"


def test_minutes_estimate():
    for lesson in content.all_lessons():
        assert content.MIN_MINUTES_EST <= lesson.minutes <= content.MAX_MINUTES_EST
    lesson = content.all_lessons()[0]
    raw = json_of(lesson)
    raw["minutes"] = 25
    assert content.from_raw(raw).minutes == 25
    raw["minutes"] = 99
    assert content.validate(raw)


def json_of(lesson):
    import json
    path = next(p for p in content.CONTENT_DIR.glob("*/*.json") if json.loads(p.read_text("utf-8"))["id"] == lesson.id)
    return json.loads(path.read_text("utf-8"))


# ---------------------------------------------------------------- экраны
def run(coro):
    return asyncio.run(coro)


def press(bot, data, user_id=111):
    cb = FakeCallback(bot, data, user_id=user_id, message_id=LIVE_MENU.get(user_id))
    run(app.on_callback(cb))
    return cb


def all_ok(markup):
    for cb in callbacks(markup):
        assert len(cb.encode()) <= 64, cb
        REGISTRY.check_callback(cb)
        assert REGISTRY.handler(cb.split(":")[0]) is not None, cb
    assert "home" in callbacks(markup)


def test_today_screen_evening_27(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 10, 27, 21, 20, tzinfo=tu.MSK))
    bot = FakeBot()
    press(bot, "ls")
    shot = bot.last_screen()
    assert "📅 <b>Сегодня</b> · ВТ 27.10" in shot.text
    assert "Сегодня уже вечер" in shot.text and "Завтра (СР 28.10, ≈15 мин): Латынь 1" in shot.text
    labels = [x.text for x in buttons(shot.markup)]
    assert "➕ Взять следующий урок: Латынь 1" in labels
    assert {"🗓 План по дням", "📚 Все уроки", "⬅ Назад"} <= set(labels)
    all_ok(shot.markup)
    assert "📚 Завтра: Латынь — урок 1 (≈15 мин)" in summary_text(feat)


def test_today_screen_28_and_done(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 10, 28, 8, 0, tzinfo=tu.MSK))
    bot = FakeBot()
    press(bot, "ls")
    shot = bot.last_screen()
    assert "📅 <b>Сегодня</b> (≈15 мин) · СР 28.10 · 5 пар" in shot.text
    lesson_cbs = [c for c in callbacks(shot.markup) if c.startswith("ls:o:")]
    assert lesson_cbs == ["ls:o:lat-01"]
    label = next(x.text for x in buttons(shot.markup) if x.callback_data == "ls:o:lat-01")
    assert label.startswith("🆕 🏛 Латынь 1.") and label.endswith("· ≈15 мин")
    all_ok(shot.markup)
    assert "📚 Сегодня: Латынь — урок 1 (≈15 мин)" in summary_text(feat)
    press(bot, "ls:m:lat-01")
    press(bot, "ls")
    shot = bot.last_screen()
    assert "✅ <b>На сегодня всё</b>" in shot.text
    assert "➕ Взять следующий урок: Латынь 2" in [x.text for x in buttons(shot.markup)]
    assert "ls:o:lat-02" in callbacks(shot.markup)
    all_ok(shot.markup)
    # взял урок заранее → завтра освобождается
    press(bot, "ls:m:lat-02")
    plan = feat.build_plan(store.data)
    assert "lat-02" not in [l.id for d in plan.days.values() for l in d.items]
    assert [l.id for l in plan.days[date(2026, 10, 29)].items] == ["lat-03"]


def test_plan_screen(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 10, 28, 8, 0, tzinfo=tu.MSK))
    bot = FakeBot()
    press(bot, "ls:P")
    shot = bot.last_screen()
    text = shot.text
    for line in ["<b>СР 28.10</b> · 5 пар · ≈15 мин · сегодня", "<b>ВС 01.11</b> · нет пар",
                 "<b>ПН 02.11</b> · 3 пары", "🔁 Повторение к КР 03.11 (Латынь), ЛР №5 03.11 (Химия)",
                 "📌 Срок: КР 03.11", "Осталось: 9 ур."]:
        assert line in text, line
    assert text.index("Латынь 1.") < text.index("Латынь 2.") < text.index("Латынь 8.")
    assert len(text) < 4096
    assert [x.text for x in buttons(shot.markup)] == ["📅 Сегодня", "📚 Все уроки", "⬅ Назад", "🏠 Меню"]
    all_ok(shot.markup)


def test_review_day_screen(use_data):
    use_data(LIVE_BACKUP)
    tu.set_now(datetime(2026, 11, 2, 9, 0, tzinfo=tu.MSK))
    for lesson in content.all_lessons():
        store.data.setdefault("lessons", {})[lesson.id] = {"pos": 0, "ans": {}, "done": "2026-11-01"}
    bot = FakeBot()
    press(bot, "ls")
    shot = bot.last_screen()
    assert "🔁 Повторение к КР 03.11 (Латынь), ЛР №5 03.11 (Химия)" in shot.text
    assert "ls:v:20261103" in callbacks(shot.markup)
    all_ok(shot.markup)
    assert "📚 Сегодня: 🔁 повторение к КР / ЛР №5 03.11 (≈18 мин)" in summary_text(feat)
    press(bot, "ls:v:20261103")
    shot = bot.last_screen()
    assert "🔁 <b>Повторение к КР 03.11</b>" in shot.text
    assert len([c for c in callbacks(shot.markup) if c.startswith("ls:o:")]) == len(content.all_lessons())
    all_ok(shot.markup)
    cb = press(bot, "ls:v:20261201")
    assert cb.answers[-1][0] == "Повторение устарело"


def test_today_crawl_reaches_all_new_screens(use_data):
    use_data(LIVE_BACKUP)
    for now in (datetime(2026, 10, 27, 21, 20), datetime(2026, 10, 28, 8), datetime(2026, 11, 2, 9),
                datetime(2026, 11, 3, 9), datetime(2026, 11, 10, 9)):
        tu.set_now(now.replace(tzinfo=tu.MSK))
        bot = FakeBot()
        for data in ("ls", "ls:P", "ls:A", "ls:l:a", "ls:x"):
            press(bot, data)
            all_ok(bot.last_screen().markup)
            assert len(bot.last_screen().text) < 4096
