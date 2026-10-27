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
    return {
        "pos": pos if isinstance(pos, int) and not isinstance(pos, bool) and pos >= 0 else 0,
        "ans": {k: v for k, v in answers.items() if isinstance(k, str) and isinstance(v, dict)}
        if isinstance(answers, dict)
        else {},
        "done": done if isinstance(done, str) and done else None,
    }


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
            rows.append([b("🙈 Скрыть усвоенные", "ls")])
        else:
            rows.append([b(f"👁 Показать усвоенные ({len(learned)})", "ls:l:a")])
    rows.append(nav())
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
        rows.append([b("✅ Всё равно отметить усвоенным", f"ls:m:{lesson.id}")])
        rows.append([b("🔁 Повторить", f"ls:p:{lesson.id}"), b("📚 К урокам", "ls")])
    lines += ["", "📎 Источники: " + escape("; ".join(lesson.sources))]
    if note:
        lines += ["", note]
    rows.append([b("◀️ Назад", f"ls:g:{lesson.id}:{total - 1}"), b("🗑 Сбросить прогресс урока", f"ls:r:{lesson.id}")])
    rows.append([b("✖️ Выйти", "ls:x"), b("🏠 Меню", "home")])
    ctx.wait_text(STATE, id=lesson.id, n=total, keep=(PREFIX,), ttl=INPUT_TTL)
    await ctx.show("\n".join(lines), rows)


async def screen_reset(ctx: Ctx, lesson: content.Lesson) -> None:
    ctx.clear_pending()
    await ctx.show(
        f"🗑 Сбросить прогресс урока «{escape(lesson.title)}»?\n"
        "Ответы и отметка «усвоено» удалятся, урок снова появится в списке.",
        [[b("Да, сбросить", f"ls:R:{lesson.id}"), b("Нет", f"ls:o:{lesson.id}")], [b("📚 К урокам", "ls"), b("🏠 Меню", "home")]],
    )


# ---------------------------------------------------------------- действия
def _record(ctx: Ctx, lesson: content.Lesson, index: int, answer: dict) -> None:
    entry = progress(ctx.data, lesson.id)
    entry["ans"][str(index)] = answer
    entry["pos"] = index
    _put(ctx, lesson.id, entry)


def _int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


async def on_lessons(ctx: Ctx, args: list[str]) -> None:
    if not await ctx.require_owner():
        return
    action = args[0] if args else ""
    if action in ("", "l", "x"):
        await screen_list(ctx, show_all=action == "l" and args[1:2] == ["a"])
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
        _put(ctx, lesson.id, {"pos": 0, "ans": {}, "done": entry["done"]})
        await screen_block(ctx, lesson, 0)
    elif action == "u":
        entry = progress(ctx.data, lesson.id)
        entry["done"] = None
        _put(ctx, lesson.id, entry)
        await screen_list(ctx, notice=f"↩️ <i>Урок «{escape(lesson.title)}» снова в списке.</i>")
    elif action == "r":
        await screen_reset(ctx, lesson)
    elif action == "R":
        _put(ctx, lesson.id, None)
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
    return bool(state and state.name == STATE and Ctx.from_message(message).is_owner)


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
def summary(ctx: Ctx) -> list[str]:
    if not ctx.is_owner:
        return []
    data = ctx.data
    groups: dict[tuple[str, date], list[content.Lesson]] = {}
    for lesson in content.all_lessons():
        found = deadline(lesson, data)
        if found and (found[1] - tu.today()).days <= 14:
            groups.setdefault((lesson.subject, found[1]), []).append(lesson)
    parts = []
    for (subject, due), lessons in sorted(groups.items(), key=lambda kv: kv[0][1]):
        learned = sum(is_learned(data, l.id) for l in lessons)
        label = deadline(lessons[0], data)[0]
        parts.append(f"{escape(tu.short_name(subject))} к {escape(label)} {due.strftime('%d.%m')}: {learned}/{len(lessons)}")
    return [f"📚 Уроки: {' · '.join(parts)}"] if parts else []


def startup(target: Any) -> None:
    lessons, errors = content.load(reload=True)
    for error in errors:
        log.error("lesson content: %s", error)
    log.info("lessons: loaded %d", len(lessons))


feature = Feature(
    name="lessons",
    callbacks={PREFIX: on_lessons},
    menu=[MenuButton("📚 Уроки", PREFIX, order=32, row="subjects", owner_only=True)],
    summary=summary,
    summary_order=45,
    text_states={STATE: on_answer_text},
    router=router,
    on_startup=startup,
)
