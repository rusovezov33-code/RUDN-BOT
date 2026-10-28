"""📚 Уроки: готовые короткие уроки из content/lessons (объяснение → пример → вопросы →
задача на подумать). Урок живёт в ОДНОМ сообщении, которое правится на месте; набранный
ответ удаляется, результат показывается в том же сообщении. Пройденный урок становится
«усвоенным» и скрывается из списка (можно показать, повторить, вернуть, сбросить).

Прогресс — data.json → "lessons": {id: {"pos": n, "ans": {"n": {...}}, "done": "YYYY-MM-DD"}}.
"""
from __future__ import annotations

import logging
from datetime import date
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from rudn import lessons as content
from rudn import lessons_plan as planner
from rudn.core import pending
from rudn.core import timeutil as tu
from rudn.core.registry import Feature, MenuButton
from rudn.core.ui import Ctx, b, nav, short

log = logging.getLogger(__name__)

PREFIX = "ls"
STATE = "ls_answer"
DATA_KEY = "lessons"
INPUT_TTL = 6 * 60 * 60  # урок можно читать долго — ждём ответ 6 часов
LEARNED_SHARE = 0.6  # доля верных ответов, чтобы урок сам стал «усвоенным»
OPTION_BUTTON_CHARS = 30  # длиннее — варианты списком в тексте, на кнопках номера
SUBJECT_ICONS = {"Латинский язык": "🏛", "Химия": "🧪"}
BLOCK_HEAD = {"explain": "📖 Объяснение", "example": "🔎 Пример", "think": "🤔 Задача на подумать"}


# ---------------------------------------------------------------- прогресс
def _table(data: dict, create: bool = False) -> dict:
    table = data.get(DATA_KEY)
    if not isinstance(table, dict):
        table = {}
        if create:
            data[DATA_KEY] = table
    return table


def progress(data: dict, lesson_id: str) -> dict:
    """Прогресс урока (копия с проверенными типами — мусор в data.json не роняет бот)."""
    raw = _table(data).get(lesson_id)
    raw = raw if isinstance(raw, dict) else {}
    pos = raw.get("pos")
    answers = raw.get("ans")
    done = raw.get("done")
    last = raw.get("last")
    entry = {
        "pos": pos if isinstance(pos, int) and not isinstance(pos, bool) and pos >= 0 else 0,
        "ans": {k: v for k, v in answers.items() if isinstance(k, str) and isinstance(v, dict)}
        if isinstance(answers, dict)
        else {},
        "done": done if isinstance(done, str) and done else None,
    }
    if isinstance(last, str) and last:
        entry["last"] = last  # результат прошлого прохода (после «Вернуть в список»/«Повторить»)
    return entry


def _put(ctx: Ctx, lesson_id: str, entry: dict | None) -> None:
    table = _table(ctx.data, create=True)
    if entry is None:
        table.pop(lesson_id, None)
    else:
        table[lesson_id] = entry
    ctx.save()


def is_learned(data: dict, lesson_id: str) -> bool:
    return progress(data, lesson_id)["done"] is not None


def stats(lesson: content.Lesson, entry: dict) -> tuple[int, int, list[int]]:
    """(верно, отвечено, номера блоков без верного ответа)."""
    good = answered = 0
    missing = []
    for index in lesson.questions:
        answer = entry["ans"].get(str(index))
        if answer is not None and answer.get("ok") is not None:
            answered += 1
            good += bool(answer.get("ok"))
        if not (answer and answer.get("ok")):
            missing.append(index)
    return good, answered, missing


# ---------------------------------------------------------------- сроки
def deadline(lesson: content.Lesson, data: dict, today: date | None = None) -> tuple[str, date] | None:
    """(«КР», дата) — если у урока есть ближайшая цель и она ещё не прошла/не сделана."""
    target = lesson.target
    if not target:
        return None
    today = today or tu.today()
    due = None
    key = target.get("key")
    item = next(
        (i for i in data.get("homework") or [] if isinstance(i, dict) and key and i.get("tuis_key") == key), None
    )
    if item is not None:
        if item.get("done"):
            return None
        from rudn.core import tasks

        due = tasks.item_date(item)
    if due is None and target.get("date"):
        try:
            due = date.fromisoformat(str(target["date"]))
        except ValueError:
            due = None
    if due is None or due < today:
        return None
    return str(target.get("label") or "цели"), due


def deadline_text(found: tuple[str, date] | None, today: date | None = None) -> str:
    if not found:
        return ""
    label, due = found
    left = (due - (today or tu.today())).days
    when = "сегодня!" if left == 0 else "уже завтра" if left == 1 else f"осталось {left} дн."
    return f"⏳ к {escape(label)} {due.strftime('%d.%m')}, {when}"


def ordered(data: dict) -> list[content.Lesson]:
    """Предметы с ближайшим сроком — первыми, внутри предмета — по order."""
    lessons = content.all_lessons()
    soonest: dict[str, date] = {}
    for lesson in lessons:
        found = deadline(lesson, data)
        if found and (lesson.subject not in soonest or found[1] < soonest[lesson.subject]):
            soonest[lesson.subject] = found[1]
    return sorted(lessons, key=lambda l: (soonest.get(l.subject, date.max), l.subject, l.order, l.id))


def _icon(subject: str) -> str:
    return SUBJECT_ICONS.get(subject, "📘")


# ---------------------------------------------------------------- экраны
async def screen_list(ctx: Ctx, show_all: bool = False, notice: str | None = None) -> None:
    ctx.clear_pending()
    data = ctx.data
    lessons = ordered(data)
    learned = [l for l in lessons if is_learned(data, l.id)]
    visible = [l for l in lessons if show_all or not is_learned(data, l.id)]
    lines = [
        "📚 <b>Уроки</b>",
        "<i>Коротко и по шагам: объяснение → пример → вопросы → задача. "
        "Ответы пиши прямо в чат — они сразу стираются. Усвоенные уроки скрываются.</i>",
    ]
    if notice:
        lines.insert(0, notice + "\n")
    rows = []
    subject = None
    for number, lesson in enumerate(visible, 1):
        if lesson.subject != subject:
            subject = lesson.subject
            hint = deadline_text(deadline(lesson, data))
            lines += ["", f"{_icon(subject)} <b>{escape(subject)}</b>" + (f" · {hint}" if hint else "")]
        entry = progress(data, lesson.id)
        if entry["done"]:
            mark, tail = "✅", " — усвоено"
        elif entry["ans"] or entry["pos"]:
            mark, tail = "▶️", f" — блок {min(entry['pos'], len(lesson.blocks) - 1) + 1}/{len(lesson.blocks)}"
        else:
            mark, tail = "🆕", ""
        lines.append(f"{mark} {number}. {escape(lesson.title)}{tail}")
        rows.append([b(f"{mark} {number}. {short(lesson.title, 40)}", f"ls:o:{lesson.id}")])
    if not lessons:
        lines += ["", "Пока нет ни одного урока."]
    elif not visible:
        lines += ["", "🎉 Все уроки усвоены! Их можно повторить — «👁 Показать усвоенные»."]
    if learned:
        if show_all:
            rows.append([b("🙈 Скрыть усвоенные", "ls:A")])
        else:
            rows.append([b(f"👁 Показать усвоенные ({len(learned)})", "ls:l:a")])
    rows.append([b("📅 Сегодня", "ls"), b("🗓 План по дням", "ls:P")])
    rows.append(nav("ls"))
    await ctx.show("\n".join(lines), rows)


# ---------------------------------------------------------------- план по дням
SUBJECT_SHORT = {"Латинский язык": "Латынь"}


def subject_short(subject: str) -> str:
    return SUBJECT_SHORT.get(subject) or tu.short_name(subject) or subject


def number_in_subject(lesson: content.Lesson) -> int:
    same = sorted((l for l in content.all_lessons() if l.subject == lesson.subject), key=lambda l: (l.order, l.id))
    return next((i for i, l in enumerate(same, 1) if l.id == lesson.id), 1)


def day_pairs(day: date) -> int:
    """Нагрузка дня в парах по расписанию (пара 12:00–14:50 считается за две)."""
    total = 0
    for item in tu.lessons_for(day):
        bounds = tu.lesson_bounds(item.time)
        length = bounds[1] - bounds[0] if bounds else 80
        total += max(1, round(length / 80))
    return total


def learned_on(data: dict, lesson_id: str) -> date | None:
    done = progress(data, lesson_id)["done"]
    try:
        return date.fromisoformat(done[:10]) if done else None
    except ValueError:
        return date.min  # усвоен, дата битая — считаем «давно»


def build_plan(data: dict) -> planner.Plan:
    return planner.build(
        content.all_lessons(),
        tu.now(),
        day_pairs,
        lambda lesson: deadline(lesson, data),
        lambda lesson: learned_on(data, lesson.id),
    )


def pairs_text(pairs: int) -> str:
    if pairs <= 0:
        return "нет пар"
    tail = "пара" if pairs % 10 == 1 and pairs % 100 != 11 else "пары" if 2 <= pairs % 10 <= 4 and not 12 <= pairs % 100 <= 14 else "пар"
    return f"{pairs} {tail}"


def minutes_text(minutes: int) -> str:
    if minutes < 60:
        return f"≈{minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"≈{hours} ч {rest} мин" if rest else f"≈{hours} ч"


def lesson_label(lesson: content.Lesson) -> str:
    return f"{_icon(lesson.subject)} {subject_short(lesson.subject)} {number_in_subject(lesson)}. {lesson.title}"


def _mark(data: dict, lesson: content.Lesson) -> str:
    entry = progress(data, lesson.id)
    if entry["done"]:
        return "✅"
    if entry["ans"] or entry["pos"]:
        return "▶️"
    return "🆕"


def _group_parts(group: planner.Group) -> list[tuple[str, list[str]]]:
    """[(«КР», [«Латынь»]), («ЛР №5», [«Химия»])] — у разных предметов свои цели."""
    data_labels: dict[str, list[str]] = {}
    for lesson in group.lessons:
        target = lesson.target or {}
        label = str(target.get("label") or "цели")
        names = data_labels.setdefault(label, [])
        if subject_short(lesson.subject) not in names:
            names.append(subject_short(lesson.subject))
    return list(data_labels.items()) or [(group.label, [subject_short(s) for s in group.subjects])]


def _group_text(group: planner.Group) -> str:
    when = group.due.strftime("%d.%m")
    return ", ".join(f"{escape(label)} {when} ({escape(', '.join(names))})" for label, names in _group_parts(group))


def _group_short(group: planner.Group) -> str:
    return " / ".join(label for label, _ in _group_parts(group)) + " " + group.due.strftime("%d.%m")


def _lesson_button(data: dict, lesson: content.Lesson) -> list:
    text = f"{_mark(data, lesson)} {short(lesson_label(lesson), 44)} · ≈{lesson.minutes} мин"
    return [b(text, f"ls:o:{lesson.id}")]


async def screen_today(ctx: Ctx, notice: str | None = None) -> None:
    ctx.clear_pending()
    data = ctx.data
    plan = build_plan(data)
    today = plan.today_day
    lines = ["📚 <b>Уроки</b>"]
    if notice:
        lines.insert(0, notice + "\n")
    rows: list[list] = []
    todo = [l for l in today.items if not is_learned(data, l.id)] if today else []
    reviews = today.review if today else []
    planned = (today.done + [l for l in today.items if l not in today.done]) if today else []
    minutes = sum(l.minutes for l in todo) + sum(planner.review_minutes(g) for g in reviews)
    head = f"📅 <b>Сегодня</b> ({minutes_text(minutes)}) · {tu.fmt_dm(plan.today)} · {pairs_text(today.pairs if today else day_pairs(plan.today))}"
    lines += ["", head if (todo or reviews) else f"📅 <b>Сегодня</b> · {tu.fmt_dm(plan.today)}"]
    for lesson in planned:
        rows.append(_lesson_button(data, lesson))
    goals = sorted((g for d in plan.days.values() for g in d.due), key=lambda g: g.due)
    for group in goals:
        left = sum(1 for l in group.lessons if not is_learned(data, l.id))
        days_left = (group.due - plan.today).days
        when = "сегодня" if days_left == 0 else f"осталось {days_left} дн."
        tail = f"{left} ур. не усвоено" if left else "всё усвоено ✅"
        lines.append(f"🎯 К {_group_text(group)}: {tail}, {when}")
    for group in reviews:
        lines.append(f"🔁 Повторение к {_group_text(group)} — {minutes_text(planner.review_minutes(group))}")
        rows.append([b(f"🔁 Повторение к {short(_group_short(group), 30)}", f"ls:v:{group.due.strftime('%Y%m%d')}")])
    if today and today.due:
        lines.append("📌 Сегодня срок: " + "; ".join(_group_text(g) for g in today.due))
    following = plan.next_lesson()
    if not todo and not reviews:
        if not plan.remaining and content.all_lessons():
            lines.append("🎉 Все уроки усвоены!")
        elif planned:
            lines.append("✅ <b>На сегодня всё</b> — отдыхай или возьми следующий урок заранее.")
        elif plan.start > plan.today:
            lines.append("Сегодня уже вечер — новые уроки с завтрашнего дня.")
            tomorrow = plan.get(plan.start)
            if tomorrow and tomorrow.items:
                lines.append(
                    f"Завтра ({tu.fmt_dm(plan.start)}, {minutes_text(tomorrow.minutes)}): "
                    + ", ".join(escape(f"{subject_short(l.subject)} {number_in_subject(l)}") for l in tomorrow.items)
                )
        elif following is None and not plan.remaining:
            lines.append("🎉 Все уроки усвоены!" if content.all_lessons() else "Пока нет ни одного урока.")
        else:
            lines.append("На сегодня уроков по плану нет.")
        if following is not None:
            rows.append([b(f"➕ Взять следующий урок: {short(subject_short(following.subject) + ' ' + str(number_in_subject(following)), 24)}", f"ls:o:{following.id}")])
    elif todo:
        lines.append("<i>Короткими блоками: один урок ≈10–15 мин, ответы пиши в чат — они сразу стираются.</i>")
    for warning in plan.warnings[:3]:
        lines += ["", escape(warning)]
    rows.append([b("🗓 План по дням", "ls:P"), b("📚 Все уроки", "ls:A")])
    rows.append(nav())
    await ctx.show("\n".join(lines), rows)


async def screen_plan(ctx: Ctx) -> None:
    ctx.clear_pending()
    data = ctx.data
    plan = build_plan(data)
    total = sum(l.minutes for l in plan.remaining)
    lines = [
        "🗓 <b>План по дням</b>",
        "<i>Пересчитывается каждый день по прогрессу: пропущенное переезжает вперёд, "
        "сделанное заранее освобождает дни, новые уроки и сроки распределяются сами. "
        f"Обычно до 2 уроков в день (в свободный — до 3), не больше {planner.SUBJECT_CAP} по одному предмету.</i>",
    ]
    if plan.remaining:
        lines.append(f"Осталось: {len(plan.remaining)} ур. {minutes_text(total)}")
    for item in plan.visible_days():
        parts = [f"<b>{tu.fmt_dm(item.day)}</b>", pairs_text(item.pairs)]
        if item.items or item.review:
            parts.append(minutes_text(item.minutes))
        if item.day == plan.today:
            parts.append("сегодня")
        lines += ["", " · ".join(parts)]
        for lesson in item.done:
            lines.append(f"  ✅ {escape(short(lesson_label(lesson), 60))}")
        for lesson in item.items:
            if lesson in item.done:
                continue
            lines.append(f"  {_mark(data, lesson)} {escape(short(lesson_label(lesson), 60))} · ≈{lesson.minutes} мин")
        for group in item.review:
            lines.append(f"  🔁 Повторение к {_group_text(group)} · {minutes_text(planner.review_minutes(group))}")
        for group in item.due:
            lines.append(f"  📌 Срок: {_group_text(group)}")
        if item.day == plan.today and not (item.items or item.done or item.review or item.due):
            lines.append("  — без уроков" + (" (уже вечер)" if plan.start > plan.today else ""))
    if not plan.remaining and not any(d.review for d in plan.days.values()):
        lines += ["", "🎉 Всё по плану пройдено — новых уроков пока нет."]
    for warning in plan.warnings:
        lines += ["", escape(warning)]
    await ctx.show("\n".join(lines), [[b("📅 Сегодня", "ls"), b("📚 Все уроки", "ls:A")], nav("ls")])


async def screen_review(ctx: Ctx, raw_due: str) -> None:
    ctx.clear_pending()
    data = ctx.data
    try:
        due = date(int(raw_due[:4]), int(raw_due[4:6]), int(raw_due[6:8]))
    except (ValueError, IndexError):
        due = None
    group = [l for l in ordered(data) if due and (deadline(l, data) or ("", None))[1] == due]
    if not group:
        ctx.toast = "Повторение устарело"
        await screen_today(ctx)
        return
    label = deadline(group[0], data)[0]
    lines = [
        f"🔁 <b>Повторение к {escape(label)} {due.strftime('%d.%m')}</b>",
        "<i>Быстро пробегись: открой урок → «🔁 Повторить» или просто перечитай итог. "
        "Неусвоенные (🆕/▶️) — в первую очередь.</i>",
    ]
    rows = []
    for lesson in sorted(group, key=lambda l: is_learned(data, l.id)):
        lines.append(f"{_mark(data, lesson)} {escape(lesson_label(lesson))}")
        rows.append([b(f"{_mark(data, lesson)} {short(lesson_label(lesson), 50)}", f"ls:o:{lesson.id}")])
    rows.append(nav("ls"))
    await ctx.show("\n".join(lines), rows)


def _header(ctx: Ctx, lesson: content.Lesson) -> list[str]:
    lessons = [l for l in content.all_lessons() if l.subject == lesson.subject]
    position = next((i for i, l in enumerate(sorted(lessons, key=lambda l: (l.order, l.id)), 1) if l.id == lesson.id), 1)
    lines = [f"📚 {_icon(lesson.subject)} <b>{escape(tu.short_name(lesson.subject))}</b> · урок {position}/{len(lessons)}",
             f"<b>{escape(lesson.title)}</b>"]
    hint = deadline_text(deadline(lesson, ctx.data))
    if hint:
        lines.append(hint)
    return lines


def _bar(done: int, total: int) -> str:
    return "▰" * done + "▱" * (total - done)


def _options_inline(block: dict) -> bool:
    return all(len(o) <= OPTION_BUTTON_CHARS for o in block["options"])


def _result_lines(block: dict, answer: dict) -> list[str]:
    kind = block["type"]
    given = escape(short(str(answer.get("a") or ""), 120))
    lines = [""]
    if kind == "think":
        if given:
            lines.append(f"📝 Твой ответ: «{given}»")
            if answer.get("ok"):
                lines.append("✅ Совпадает с эталоном!")
        lines += ["", f"💡 <b>Разбор:</b> {block['answer']}"]
        return lines
    right = escape(content.correct_text(block))
    if answer.get("shown"):
        lines.append(f"👀 Правильный ответ: <b>{right}</b>")
    elif answer.get("ok"):
        lines.append("✅ <b>Верно!</b>")
        if answer.get("typo"):
            lines.append(f"Небольшая опечатка — правильно: <b>{right}</b>")
    else:
        lines.append(f"❌ <b>Неверно.</b> Твой ответ: «{given}»")
        if answer.get("near"):
            lines.append("Почти! Отличается одна буква — в латыни это обычно окончание.")
        lines.append(f"✔️ Правильно: <b>{right}</b>")
    lines.append(f"💡 {block['explain']}")
    return lines


async def screen_block(ctx: Ctx, lesson: content.Lesson, index: int, note: str | None = None) -> None:
    total = len(lesson.blocks)
    if index >= total:
        await screen_summary(ctx, lesson)
        return
    index = max(0, index)
    entry = progress(ctx.data, lesson.id)
    if entry["pos"] != index:
        entry["pos"] = index
        _put(ctx, lesson.id, entry)
    block = lesson.blocks[index]
    kind = block["type"]
    answer = entry["ans"].get(str(index))
    lines = _header(ctx, lesson)
    lines += [f"Блок {index + 1}/{total} {_bar(index + 1, total)}", ""]
    rows: list[list] = []
    if kind in content.INFO_TYPES:
        head = BLOCK_HEAD[kind]
        lines.append(f"{head.split()[0]} <b>{escape(block['title'])}</b>" if block.get("title") else f"<b>{head}</b>")
        lines += ["", block["text"]]
    elif kind == "think":
        lines += [f"<b>{BLOCK_HEAD['think']}</b>", "", block["q"]]
        if answer:
            lines += _result_lines(block, answer)
            rows.append([b("🔁 Ещё раз", f"ls:t:{lesson.id}:{index}")])
        else:
            lines += ["", "<i>Подумай и напиши ответ сообщением — или сразу открой разбор.</i>"]
            rows.append([b("💡 Показать разбор", f"ls:s:{lesson.id}:{index}")])
    else:
        number, count = lesson.question_number(index), len(lesson.questions)
        lines += [f"❓ <b>Вопрос {number}/{count}</b>", "", block["q"]]
        if kind == "choice" and not _options_inline(block):
            lines.append("")
            lines += [f"{i}) {escape(o)}" for i, o in enumerate(block["options"], 1)]
        if answer:
            lines += _result_lines(block, answer)
            rows.append([b("🔁 Ещё раз", f"ls:t:{lesson.id}:{index}")])
        elif kind == "choice":
            inline = _options_inline(block)
            buttons = [
                b(o if inline else str(i), f"ls:a:{lesson.id}:{index}:{i - 1}")
                for i, o in enumerate(block["options"], 1)
            ]
            if inline and max(len(o) for o in block["options"]) > 14:
                rows += [[button] for button in buttons]
            else:
                rows += [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
            lines += ["", "<i>Жми вариант (или напиши номер).</i>"]
        else:
            lines += ["", "<i>Напиши ответ сообщением — регистр, точки и ё/е не важны.</i>"]
            rows.append([b("🤷 Не знаю — показать ответ", f"ls:s:{lesson.id}:{index}")])
    if note:
        lines += ["", note]
    step = []
    if index > 0:
        step.append(b("◀️ Назад", f"ls:g:{lesson.id}:{index - 1}"))
    step.append(b("Далее ▶️" if index + 1 < total else "🏁 Итог", f"ls:g:{lesson.id}:{index + 1}"))
    rows.append(step)
    rows.append([b("✖️ Выйти", "ls:x"), b("🗑 Сброс", f"ls:r:{lesson.id}"), b("🏠 Меню", "home")])
    ctx.wait_text(STATE, id=lesson.id, n=index, keep=(PREFIX,), ttl=INPUT_TTL)
    await ctx.show("\n".join(lines), rows)


async def screen_summary(ctx: Ctx, lesson: content.Lesson, note: str | None = None) -> None:
    entry = progress(ctx.data, lesson.id)
    good, answered, missing = stats(lesson, entry)
    count = len(lesson.questions)
    total = len(lesson.blocks)
    if entry["pos"] != total:
        entry["pos"] = total
        _put(ctx, lesson.id, entry)
    auto = not entry["done"] and answered == count and good >= LEARNED_SHARE * count
    if auto:
        entry["done"] = tu.today().isoformat()
        _put(ctx, lesson.id, entry)
    lines = _header(ctx, lesson) + ["", f"🏁 <b>Итог</b>: верно {good} из {count}"]
    if answered < count:
        lines.append(f"Без ответа: {count - answered}")
    if entry.get("last"):
        lines.append(f"<i>Прошлый проход: верно {escape(entry['last'])}</i>")
    rows: list[list] = []
    if entry["done"]:
        lines.append(
            "✅ <b>Урок усвоен</b> — он скрыт из списка «📚 Уроки». "
            "Найти его: «👁 Показать усвоенные»."
        )
        rows.append([b("📚 К урокам", "ls")])
        rows.append([b("🔁 Повторить", f"ls:p:{lesson.id}"), b("↩️ Вернуть в список", f"ls:u:{lesson.id}")])
    else:
        need = int(-(-LEARNED_SHARE * count // 1))
        lines.append(
            f"Чтобы урок стал усвоенным, ответь на все вопросы и хотя бы на {need} — верно."
        )
        if missing:
            first = missing[0]
            numbers = ", ".join(str(lesson.question_number(i)) for i in missing)
            lines.append(f"Повторить вопросы: {numbers}")
            rows.append([b(f"➡️ К вопросу {lesson.question_number(first)}", f"ls:g:{lesson.id}:{first}")])
        rows.append([b("✅ Отметить усвоенным", f"ls:m:{lesson.id}")])
        rows.append([b("🔁 Пройти заново", f"ls:r:{lesson.id}:1"), b("📚 К урокам", "ls")])
    lines += ["", "📎 Источники: " + escape("; ".join(lesson.sources))]
    if note:
        lines += ["", note]
    if entry["done"]:
        rows.append([b("◀️ Назад", f"ls:g:{lesson.id}:{total - 1}"), b("🗑 Сбросить прогресс урока", f"ls:r:{lesson.id}")])
    else:
        rows.append([b("◀️ Назад", f"ls:g:{lesson.id}:{total - 1}")])
    rows.append([b("✖️ Выйти", "ls:x"), b("🏠 Меню", "home")])
    ctx.wait_text(STATE, id=lesson.id, n=total, keep=(PREFIX,), ttl=INPUT_TTL)
    await ctx.show("\n".join(lines), rows)


async def screen_reset(ctx: Ctx, lesson: content.Lesson, again: bool = False) -> None:
    ctx.clear_pending()
    if again:
        text = (
            f"🔁 Пройти урок «{escape(lesson.title)}» заново?\n"
            "Ответы удалятся, урок откроется с блока 1."
        )
        yes = b("Да, заново", f"ls:R:{lesson.id}:1")
    else:
        text = (
            f"🗑 Сбросить прогресс урока «{escape(lesson.title)}»?\n"
            "Ответы и отметка «усвоено» удалятся, урок снова появится в списке."
        )
        yes = b("Да, сбросить", f"ls:R:{lesson.id}")
    await ctx.show(text, [[yes, b("Нет", f"ls:o:{lesson.id}")], [b("📚 К урокам", "ls"), b("🏠 Меню", "home")]])


# ---------------------------------------------------------------- действия
def _record(ctx: Ctx, lesson: content.Lesson, index: int, answer: dict) -> None:
    entry = progress(ctx.data, lesson.id)
    entry["ans"][str(index)] = answer
    entry["pos"] = index
    _put(ctx, lesson.id, entry)


def _fresh(lesson: content.Lesson, entry: dict, done: str | None) -> dict:
    """Новый проход: ответы стёрты, позиция — блок 1, счёт прошлого прохода сохранён."""
    good, answered, _ = stats(lesson, entry)
    fresh = {"pos": 0, "ans": {}, "done": done}
    if answered:
        fresh["last"] = f"{good} из {len(lesson.questions)}"
    elif entry.get("last"):
        fresh["last"] = entry["last"]
    return fresh


def _int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


async def on_lessons(ctx: Ctx, args: list[str]) -> None:
    if not ctx.can_learn:  # владелец и тестер (у тестера свой прогресс в access.json)
        await ctx.answer("Нет доступа", alert=True)
        return
    action = args[0] if args else ""
    if action in ("", "x"):
        await screen_today(ctx)
        return
    if action in ("A", "l"):
        await screen_list(ctx, show_all=action == "l" and args[1:2] == ["a"])
        return
    if action == "P":
        await screen_plan(ctx)
        return
    if action == "v":
        await screen_review(ctx, args[1] if len(args) > 1 else "")
        return
    lesson = content.get(args[1]) if len(args) > 1 else None
    if lesson is None:
        ctx.toast = "Урок не найден"
        await screen_list(ctx, notice="⚠️ <i>Урок не найден — список обновился.</i>")
        return
    index = _int(args[2] if len(args) > 2 else None)
    total = len(lesson.blocks)
    if action == "o":
        entry = progress(ctx.data, lesson.id)
        if entry["done"] or entry["pos"] >= total:
            await screen_summary(ctx, lesson)
        else:
            await screen_block(ctx, lesson, entry["pos"])
    elif action == "g" and index is not None:
        await screen_block(ctx, lesson, min(max(index, 0), total))
    elif action == "a" and index is not None and len(args) > 3:
        index = min(max(index, 0), total - 1)
        block = lesson.blocks[index]
        picked = _int(args[3])
        if block["type"] != "choice" or picked is None or not 0 <= picked < len(block["options"]):
            await screen_block(ctx, lesson, index)
            return
        ok = picked == block["correct"]
        ctx.toast = "✅ Верно" if ok else "❌ Неверно"
        _record(ctx, lesson, index, {"ok": ok, "a": block["options"][picked]})
        await screen_block(ctx, lesson, index)
    elif action == "s" and index is not None:
        index = min(max(index, 0), total - 1)
        block = lesson.blocks[index]
        if block["type"] in content.QUESTION_TYPES + ("think",) and str(index) not in progress(ctx.data, lesson.id)["ans"]:
            shown = {"ok": False, "a": "", "shown": True} if block["type"] != "think" else {"ok": None, "a": "", "shown": True}
            _record(ctx, lesson, index, shown)
        await screen_block(ctx, lesson, index)
    elif action == "t" and index is not None:
        index = min(max(index, 0), total - 1)
        entry = progress(ctx.data, lesson.id)
        entry["ans"].pop(str(index), None)
        entry["pos"] = index
        _put(ctx, lesson.id, entry)
        await screen_block(ctx, lesson, index)
    elif action == "f":
        await screen_summary(ctx, lesson)
    elif action == "m":
        entry = progress(ctx.data, lesson.id)
        entry["done"] = entry["done"] or tu.today().isoformat()
        _put(ctx, lesson.id, entry)
        ctx.toast = "✅ Отмечено усвоенным"
        await screen_summary(ctx, lesson)
    elif action == "p":
        entry = progress(ctx.data, lesson.id)
        _put(ctx, lesson.id, _fresh(lesson, entry, done=entry["done"]))
        await screen_block(ctx, lesson, 0)
    elif action == "u":
        # вернуть в список = пройти заново: старые ответы стираются (иначе итог
        # тут же снова засчитал бы урок усвоенным), их счёт — в «last»
        entry = progress(ctx.data, lesson.id)
        _put(ctx, lesson.id, _fresh(lesson, entry, done=None))
        await screen_list(ctx, notice=f"↩️ <i>Урок «{escape(lesson.title)}» снова в списке — откроется с блока 1.</i>")
    elif action == "r":
        await screen_reset(ctx, lesson, again=args[2:3] == ["1"])
    elif action == "R":
        _put(ctx, lesson.id, None)
        if args[2:3] == ["1"]:
            await screen_block(ctx, lesson, 0)
        else:
            await screen_list(ctx, notice=f"🗑 <i>Прогресс урока «{escape(lesson.title)}» сброшен.</i>")
    else:
        await screen_block(ctx, lesson, progress(ctx.data, lesson.id)["pos"])


async def on_answer_text(ctx: Ctx, text: str) -> None:
    state = ctx.pending()
    lesson = content.get(str(state.data.get("id"))) if state else None
    if lesson is None:
        await screen_list(ctx, notice="⚠️ <i>Урок не найден — список обновился.</i>")
        return
    index = state.data.get("n")
    index = index if isinstance(index, int) else 0
    if index >= len(lesson.blocks):
        await screen_summary(ctx, lesson, note="💡 <i>Урок закончен — выбери действие кнопкой.</i>")
        return
    block = lesson.blocks[index]
    kind = block["type"]
    if kind in content.INFO_TYPES:
        await screen_block(ctx, lesson, index, note="💡 <i>Здесь ответ не нужен — жми «Далее ▶️».</i>")
        return
    if str(index) in progress(ctx.data, lesson.id)["ans"]:
        await screen_block(ctx, lesson, index, note="💡 <i>Ответ уже есть. Ответить заново — «🔁 Ещё раз».</i>")
        return
    if kind == "choice":
        verdict, picked = content.check_choice(block, text)
        given = block["options"][picked] if picked is not None else text
    else:
        verdict = content.match(text, block.get("accept") or [])
        given = text
    if verdict.too_long:
        await screen_block(ctx, lesson, index, note=f"✂️ <i>Слишком длинно — здесь хватит пары слов (до {content.MAX_ANSWER_CHARS} знаков).</i>")
        return
    if verdict.empty:
        await screen_block(ctx, lesson, index, note="🙈 <i>Не вижу ответа — напиши его буквами.</i>")
        return
    if kind == "choice" and picked is None:
        await screen_block(ctx, lesson, index, note=f"💡 <i>Жми кнопку варианта или напиши номер 1–{len(block['options'])}.</i>")
        return
    if kind == "think":
        answer: dict[str, Any] = {"ok": verdict.ok if block.get("accept") else None, "a": short(given, 200)}
    else:
        answer = {"ok": verdict.ok, "a": short(given, 200)}
        if verdict.typo:
            answer["typo"] = True
        if verdict.near:
            answer["near"] = True
    _record(ctx, lesson, index, answer)
    await screen_block(ctx, lesson, index)


# ---------------------------------------------------------------- не текст во время урока
def _in_lesson(message: Message) -> bool:
    user = getattr(message, "from_user", None)
    state = pending.get(user.id) if user else None
    return bool(state and state.name == STATE and Ctx.from_message(message).can_learn)


router = Router(name="lessons-input")


@router.message(_in_lesson, ~F.text, ~F.document)
async def on_not_text(message: Message) -> None:
    """Стикер/фото/голосовое во время урока: удаляем и напоминаем, что ответ нужен текстом."""
    ctx = Ctx.from_message(message)
    ctx.defer_save = True
    ctx.edit_live = True
    await ctx.delete_incoming()
    state = ctx.pending()
    lesson = content.get(str(state.data.get("id"))) if state else None
    note = "🙈 <i>Ответ нужен текстом — напиши его сообщением.</i>"
    try:
        if lesson is None:
            await screen_list(ctx)
        elif isinstance(state.data.get("n"), int) and state.data["n"] < len(lesson.blocks):
            await screen_block(ctx, lesson, state.data["n"], note=note)
        else:
            await screen_summary(ctx, lesson, note=note)
    finally:
        await ctx.flush()


# ---------------------------------------------------------------- меню и сводка
def _assignment(data: dict, lessons: list[content.Lesson]) -> str:
    by_subject: dict[str, list[int]] = {}
    for lesson in lessons:
        by_subject.setdefault(subject_short(lesson.subject), []).append(number_in_subject(lesson))
    parts = []
    for name, numbers in by_subject.items():
        word = "урок" if len(numbers) == 1 else "уроки"
        parts.append(f"{name} — {word} {', '.join(map(str, numbers))}")
    return ", ".join(parts)


def summary(ctx: Ctx) -> list[str]:
    """На главном — только задание на сегодня (а не весь объём)."""
    if not ctx.can_learn or not content.all_lessons():
        return []
    data = ctx.data
    plan = build_plan(data)
    today = plan.today_day
    todo = [l for l in today.items if not is_learned(data, l.id)] if today else []
    reviews = today.review if today else []
    if todo or reviews:
        parts = []
        if todo:
            parts.append(_assignment(data, todo))
        for group in reviews:
            parts.append(f"🔁 повторение к {_group_short(group)}")
        minutes = sum(l.minutes for l in todo) + sum(planner.review_minutes(g) for g in reviews)
        return [escape(f"📚 Сегодня: {'; '.join(parts)} ({minutes_text(minutes)})")]
    if today and today.done:
        return ["📚 Сегодня: ✅ уроки на сегодня сделаны"]
    if plan.start > plan.today:
        tomorrow = plan.get(plan.start)
        if tomorrow and tomorrow.items:
            return [escape(f"📚 Завтра: {_assignment(data, tomorrow.items)} ({minutes_text(tomorrow.minutes)})")]
    return []


def startup(target: Any) -> None:
    lessons, errors = content.load(reload=True)
    for error in errors:
        log.error("lesson content: %s", error)
    log.info("lessons: loaded %d", len(lessons))


feature = Feature(
    name="lessons",
    callbacks={PREFIX: on_lessons},
    menu=[MenuButton("🧠 Учить", PREFIX, order=21, row="m2")],
    summary=summary,
    summary_order=45,
    text_states={STATE: on_answer_text},
    router=router,
    on_startup=startup,
)
