"""📚 Предметы: список в 2 колонки со значками, карточка предмета, разделы, файлы."""
from __future__ import annotations

from html import escape

from rudn import tuis
from rudn.core import config, tasks, timeutil as tu
from rudn.core.registry import CommandSpec, Feature, MenuButton
from rudn.core.storage import common_urls
from rudn.core.ui import Ctx, b, chunk, is_url, maybe, nav, row_of, short, url_btn
from rudn.core.web import file_link, file_meta


def own_links(section: dict) -> list[dict]:
    commons = common_urls()
    return [
        link
        for link in section.get("links") or []
        if isinstance(link, dict) and is_url(link.get("url")) and link["url"] not in commons
    ]


def section_size(section: dict) -> int:
    """Сколько полезного в разделе (общая ссылка ТУИС не считается)."""
    size = len(section.get("files") or []) + len(own_links(section))
    if str(section.get("text") or "").strip():
        size += 1
    return size


def doc_block(name: str, section: dict) -> tuple[str, list[dict]]:
    """Что добавить в раздел из документов ТУИС (БРС, литература, план занятий)."""
    try:
        return tuis.section_block(name, str(section.get("title") or ""))
    except Exception:  # данные ТУИС — дополнение, раздел должен открываться всегда
        return "", []


def section_total(name: str, section: dict) -> int:
    text, links = doc_block(name, section)
    return section_size(section) + (1 if text else 0) + len(links)


def file_count(subject: dict) -> int:
    return sum(len(section.get("files") or []) for section in subject.get("sections") or [])


def section_label(title: str) -> str:
    return short(str(title or "Раздел").split(" — ")[0], 28)


def common_link_row() -> list:
    return [url_btn(f"🔗 {link['title']}", link["url"]) for link in config.subjects().common_links if is_url(link.get("url"))]


CARD_TASKS = 6


def open_task_lines(open_tasks: list[dict], today) -> list[str]:
    """Блок «Открытые задания» карточки: число = все строки, которые есть в «📝 Задания»;
    видно, сколько из них экзамены/зачёты и сколько не поместилось."""
    exams = sum(1 for item in open_tasks if tasks.kind_of(item) == "exam")
    split = f" — дедлайнов {len(open_tasks) - exams}, экзаменов/зачётов {exams}" if exams and exams < len(open_tasks) else ""
    lines = [f"📝 <b>Открытые задания ({len(open_tasks)}){split}:</b>"]
    for item in open_tasks[:CARD_TASKS]:
        label = tasks.kind_label(item) if tasks.kind_of(item) == "exam" else ""
        body = " — ".join(part for part in (label, str(item.get("text") or "")) if part) or "—"
        lines.append(f"• {escape(tasks.due_text(item, today))} · {tasks.kind_icon(item)} {escape(short(body, 110))}")
    if len(open_tasks) > CARD_TASKS:
        lines.append(f"… и ещё {len(open_tasks) - CARD_TASKS} — в «📝 Задания»")
    return lines


async def screen_subjects(ctx: Ctx) -> None:
    data = ctx.data
    subjects = data.get("subjects") or []
    buttons = []
    for index, subject in enumerate(subjects):
        badges = ""
        files = file_count(subject)
        if files:
            badges += f" 📄{files}"
        open_tasks = len(tasks.open_items(data, tasks.ALL_KINDS, si=index))
        if open_tasks:
            badges += f" 📝{open_tasks}"
        buttons.append(b(short(tu.short_name(subject.get("name") or "Без названия"), 24) + badges, f"s:{index}"))
    rows = chunk(buttons, 2)
    rows.append(common_link_row())
    rows.append(nav())
    text = "📚 <b>Предметы</b>\n📄 — файлы, 📝 — открытые задания (вместе с экзаменами/зачётами)" if subjects else "📚 <b>Предметы</b>\n\nПока ничего нет."
    await ctx.show(text, rows)


async def screen_subject(ctx: Ctx, si: int) -> None:
    data = ctx.data
    try:
        subject = data["subjects"][si]
    except (IndexError, KeyError, TypeError):
        await ctx.show("Предмет не найден.", [nav("subs")])
        return
    today = tu.today()
    name = subject.get("name") or "Без названия"
    lines = [f"📘 <b>{escape(name)}</b>"]
    if subject.get("teacher") and subject["teacher"] != "Не указано":
        lines.append(f"👤 {escape(str(subject['teacher']))}")
    if subject.get("contacts") and subject["contacts"] != "Не указано":
        lines.append(f"✉️ {escape(str(subject['contacts']))}")
    if subject.get("description"):
        lines.append(escape(str(subject["description"])))
    slots = tu.slots_for_subject(name)
    if slots:
        lines.append("")
        for code, lesson in slots:
            place = f" · {escape(lesson.place)}" if lesson.place else ""
            lines.append(f"🗓 {code} {escape(lesson.time)}{place} · {tu.WEEK_NOTE[lesson.weeks]}")
    found = tuis.courses_for(name)
    if found:
        lines.append("")
        for c in found:
            got, done_max = tuis.total(data, c)
            mine = f" · мои баллы: <b>{tuis.fmt_num(got)}</b>" if tuis.has_points(data, c) else ""
            label = f"{escape(c['name'])}: " if len(found) > 1 else ""
            lines.append(f"📋 БРС: {label}{tuis.brs_short(c)}{mine}")
    elif tuis.no_data_for(name):
        lines.append("")
        lines.append("📋 БРС: нет данных в документах ТУИС")
    open_tasks = sorted(tasks.open_items(data, tasks.ALL_KINDS, si=si), key=tasks.sort_key)
    if open_tasks:
        lines.append("")
        lines += open_task_lines(open_tasks, today)
    rows: list[list] = []
    upcoming = tu.upcoming_lesson_dates(name, today, 45, moment=tu.now())
    first_row = []
    if upcoming:
        first_row.append(maybe(f"📅 Ближайшая · {tu.fmt_dm(upcoming[0])}", f"d:{upcoming[0].isoformat()}|s:{si}"))
    first_row.append(maybe(f"📝 Задания ({len(open_tasks)})" if open_tasks else "📝 Задания", f"dl:s:{si}"))
    rows.append(row_of(*first_row))
    if ctx.is_owner:
        rows.append(row_of(maybe("➕ Добавить: Д/з · контрольная · экзамен", f"add:s:{si}")))
    if found:
        rows.append(
            row_of(
                *[
                    maybe(f"📊 Баллы{' · ' + short(tu.short_name(c['name']), 22) if len(found) > 1 else ''}", f"sc:c:{c['key']}|s:{si}")
                    for c in found
                ]
            )
        )
    filled = [
        b(f"{section_label(section.get('title'))} ({section_total(name, section)})", f"s:{si}:{index}")
        for index, section in enumerate(subject.get("sections") or [])
        if isinstance(section, dict) and section_total(name, section)
    ]
    if filled:
        rows += chunk(filled, 2)
    else:
        lines.append("")
        lines.append("📂 Материалы пока не добавлены.")
        if ctx.is_owner:
            lines.append("Чтобы добавить файл — просто пришли его боту документом.")
    rows.append(nav("subs"))
    await ctx.show("\n".join(lines), rows)


async def screen_section(ctx: Ctx, si: int, j: int) -> None:
    data = ctx.data
    try:
        subject = data["subjects"][si]
        section = subject["sections"][j]
    except (IndexError, KeyError, TypeError):
        await ctx.show("Раздел не найден.", [nav(f"s:{si}")])
        return
    body = escape(str(section.get("text") or ""))
    files = section.get("files") or []
    links = own_links(section)
    doc_text, doc_links = doc_block(subject.get("name") or "", section)
    lines = [f"<b>{escape(subject.get('name') or '')}</b>", escape(section.get("title") or "")]
    if body:
        lines += ["", body]
    elif not (links or files or doc_text):
        lines += ["", "Пока ничего нет."]
    if doc_text:
        lines += ["", "📑 <b>Из документов ТУИС:</b>", doc_text]
    if files and config.public_base():
        lines.append("\nНажми на файл — телефон предложит приложение.")
    elif files:
        lines.append("\nНажми на файл — бот пришлёт его сообщением.")
    own = {link["url"] for link in links}
    links = links + [link for link in doc_links if is_url(link.get("url")) and link["url"] not in own]
    rows = [[url_btn(f"🔗 {short(link.get('title') or link['url'], 40)}", link["url"])] for link in links]
    for index, meta in enumerate(files):
        _, file_name = file_meta(meta)
        text = f"📄 {short(file_name, 40)}"
        url = file_link(si, j, index, meta)
        rows.append([url_btn(text, url) if url else b(text, f"file:{si}:{j}:{index}")])
    rows.append(nav(f"s:{si}"))
    await ctx.show("\n".join(lines), rows)


async def on_subjects(ctx: Ctx, args: list[str]) -> None:
    await screen_subjects(ctx)


async def on_subject(ctx: Ctx, args: list[str]) -> None:
    try:
        numbers = [int(value) for value in args[:2]]
        if not numbers or min(numbers) < 0:
            raise ValueError
    except ValueError:
        await ctx.show("Предмет не найден.", [nav("subs")])
        return
    if len(numbers) == 2:
        await screen_section(ctx, numbers[0], numbers[1])
    else:
        await screen_subject(ctx, numbers[0])


async def on_file(ctx: Ctx, args: list[str]) -> None:
    try:
        si, j, fi = (int(value) for value in args[:3])
        meta = ctx.data["subjects"][si]["sections"][j]["files"][fi]
    except (ValueError, IndexError, KeyError, TypeError):
        await ctx.answer("Файл не найден", alert=True)
        return
    file_id, _ = file_meta(meta)
    await ctx.answer("Отправляю…")
    try:
        await ctx.bot.send_document(ctx.chat_id, file_id)
    except Exception:
        await ctx.bot.send_message(ctx.chat_id, "Не удалось отправить файл.")


async def cmd_subjects(ctx: Ctx) -> None:
    await screen_subjects(ctx)


feature = Feature(
    name="subjects",
    callbacks={"subs": on_subjects, "s": on_subject, "file": on_file},
    menu=[MenuButton("📚 Предметы", "subs", order=30, row="subjects")],
    commands=[CommandSpec("subjects", "Предметы и материалы", cmd_subjects)],
)
