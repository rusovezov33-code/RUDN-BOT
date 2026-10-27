from datetime import date, datetime

from rudn.core import config, tasks, timeutil as tu
from rudn.core.ui import clip_html


def test_schedule_config_is_valid_and_has_official_pairs():
    cfg = config.schedule()
    assert cfg.week_anchor == date(2026, 10, 26) and cfg.anchor_is_upper
    for code, lessons in cfg.days.items():
        for l in lessons:
            assert l.weeks in ("both", "upper", "lower"), (code, l)
            assert l.time and l.subject, (code, l)
    tue = [(l.time, l.subject, l.weeks) for l in cfg.days["ВТ"]]
    # Официальное расписание осень 2026: физика во вторник по верхней неделе
    assert ("16:30–17:50", "Физика", "upper") in tue
    assert ("19:30–20:50", "Физика", "lower") in tue


def test_week_types():
    assert tu.week_type(date(2026, 10, 26)) == "upper"
    assert tu.week_type(date(2026, 11, 1)) == "upper"
    assert tu.week_type(date(2026, 11, 2)) == "lower"
    assert tu.week_type(date(2026, 10, 19)) == "lower"


def test_sunday_shows_next_week():
    assert tu.default_week_monday(date(2026, 11, 1)) == date(2026, 11, 2)
    assert tu.default_week_monday(date(2026, 10, 31)) == date(2026, 10, 26)
    assert tu.default_week_monday(date(2026, 10, 26)) == date(2026, 10, 26)


def test_parse_user_date():
    ref = date(2026, 10, 26)
    assert tu.parse_user_date("15.01", ref) == date(2027, 1, 15)
    assert tu.parse_user_date("28.10", ref) == date(2026, 10, 28)
    assert tu.parse_user_date("15.01.2027", ref) == date(2027, 1, 15)
    assert tu.parse_user_date("2027-01-15", ref) == date(2027, 1, 15)
    assert tu.parse_user_date("31.02", ref) is None
    assert tu.parse_user_date("привет", ref) is None


def test_deadline_grouping():
    today = date(2026, 10, 26)
    data = {"subjects": [{"name": "Химия"}], "homework": []}
    for offset, kind in [(-2, "hw"), (0, "lab"), (1, "hw"), (5, "lab"), (20, "hw")]:
        tasks.new_item(data, kind, 0, f"t{offset}", date.fromordinal(today.toordinal() + offset))
    tasks.new_item(data, "hw", 0, "nodate", None)
    tasks.new_item(data, "exam", 0, "экзамен", date(2026, 10, 27))
    done = tasks.new_item(data, "hw", 0, "done", today)
    tasks.mark_done(done)
    groups = tasks.group_deadlines(tasks.open_items(data), today)
    assert [g[0] for g in groups] == ["overdue", "today", "tomorrow", "week", "later", "nodate"]
    assert all(len(g[2]) == 1 for g in groups)
    counts = tasks.urgent_counts(data, today)
    assert counts["overdue"] == 1 and counts["today"] == 1
    assert [i["text"] for i in tasks.upcoming_exams(data, today)] == ["экзамен"]
    assert "done_at" in done
    tasks.mark_done(done, False)
    assert "done_at" not in done


def test_clip_html_keeps_tags():
    text = "\n".join(f"<b>строка {i}</b>" for i in range(1000))
    clipped = clip_html(text)
    assert len(clipped) <= 3900
    assert clipped.count("<b>") == clipped.count("</b>")
    long_line = "<b>" + "x" * 5000 + "</b>"
    assert "<b>" not in clip_html(long_line)


def test_lesson_state():
    moment = datetime(2026, 10, 26, 10, 0, tzinfo=tu.MSK)
    assert tu.lesson_state("09:00 – 10:20", moment) == "now"
    assert tu.lesson_state("10:30 – 11:50", moment) == "future"
    assert tu.lesson_state("08:00 – 09:20", moment) == "past"
