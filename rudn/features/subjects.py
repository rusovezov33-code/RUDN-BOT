"""📚 Предметы: список в 2 колонки со значками, карточка предмета, разделы, файлы."""
from __future__ import annotations

from html import escape

from rudn import tuis
from rudn.core import config, dresscode, tasks, timeutil as tu
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


def has_tuis(name: str) -> bool:
    """Есть ли у предмета данные из документов ТУИС (кнопка «🎓 ТУИС»)."""
    try:
        return tuis.has_section(name)
    except Exception:  # данные ТУИС — дополнение, карточка должна открываться всегда
        return False


def file_count(subject: dict) -> int:
    return sum(len(section.get("files") or []) for section in subject.get("sections") or [])


def short_person(name: str) -> str:
    """«Провоторова Елена Аркадьевна» → «Провоторова Е. А.»; иное — как есть."""
    parts = name.split()
    if len(parts) == 3 and all(p[:1].isupper() and p.isalpha() for p in parts):
        return f"{parts[0]} {parts[1][0]}. {parts[2][0]}."
    return name


def section_label(title: str) -> str:
    return short(str(title or "Раздел").split(" — ")[0], 28)


def common_link_row() -> list:
    return [url_btn(f"🔗 {link['title']}", link["url"]) for link in config.subjects().common_links if is_url(link.get("url"))]


CARD_TASKS = 3


def open_task_lines(open_tasks: list[dict], today) -> list[str]:
    """Ближайшие задания карточки: «срок  текст» без значков, остальное — «и ещё N» (в «✅ Задания»)."""
    lines = []
    for item in open_tasks[:CARD_TASKS]:
        body = (tasks.titled_text(item) if tasks.kind_of(item) == "exam" else tasks.plain_text(item)) or "—"
        lines.append(f"{escape(tasks.due_text(item, today))}  {escape(short(body, 40))}")
    if len(open_tasks) > CARD_TASKS:
        lines.append(f"и ещё {len(open_tasks) - CARD_TASKS}")
    return lines


async def screen_subjects(ctx: Ctx) -> None:
    data = ctx.data
    subjects = data.get("subjects") or []
    buttons = [
        b(short(tu.short_name(subject.get("name") or "Без названия"), 20), f"s:{index}")
        for index, subject in enumerate(subjects)
    ]
    rows = chunk(buttons, 2)
    rows.append(row_of(maybe("📊 Все баллы", "sc|subs"), *common_link_row()))
    rows.append([b("⬅ Назад", "home")])
    text = "📚 <b>Предметы</b>" if subjects else "📚 <b>Предметы</b>\n\nПока ничего нет."
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
    lines = [f"<b>{escape(name)}</b>"]
    teacher = subject.get("teacher") if subject.get("teacher") not in (None, "", "Не указано") else tuis.teacher_for(name)
    if teacher:
        lines.append(escape(str(teacher)))
    if subject.get("contacts") and subject["contacts"] != "Не указано":
        lines.append(escape(str(subject["contacts"])))
    if subject.get("description"):
        lines.append(escape(str(subject["description"])))
    form = dresscode.form_line(name)
    if form:
        lines.append(form)
    # аудитории и время пар — по кнопке «📅 Пара …» (экран дня), не на карточке
    found = tuis.courses_for(name)
    open_tasks = sorted(tasks.open_items(data, tasks.ALL_KINDS, si=si), key=tasks.sort_key)
    if open_tasks:
        lines.append("")
        lines += open_task_lines(open_tasks, today)
    rows: list[list] = []
    upcoming = tu.upcoming_lesson_dates(name, today, 45, moment=tu.now())
    first_row = [maybe(f"✅ Задания {len(open_tasks)}", f"dl:s:{si}") if open_tasks else None]
    if upcoming:
        first_row.append(maybe(f"📅 Пара {tu.fmt_dm(upcoming[0])}", f"d:{upcoming[0].isoformat()}|s:{si}"))
    rows.append(row_of(*first_row))
    score_btns = [
        maybe(f"📊 Баллы{' · ' + short(tu.short_name(c['name']), 10) if len(found) > 1 else ''}", f"sc:c:{c['key']}|s:{si}")
        for c in found
    ]
    tuis_btn = maybe("🎓 ТУИС", f"s:{si}:t") if has_tuis(name) else None
    if len(score_btns) == 1:
        rows.append(row_of(tuis_btn, *score_btns))  # «🎓 ТУИС» и «📊 Баллы» — одной строкой
    else:
        rows.append(row_of(tuis_btn))
        if score_btns:
            rows += chunk(score_btns, 2)
    filled = [
        b(short(section_label(section.get("title")), 20), f"s:{si}:{index}")
        for index, section in enumerate(subject.get("sections") or [])
        if isinstance(section, dict) and section_size(section)
    ]
    rows += chunk(filled, 2)  # пустые разделы не показываем
    if ctx.is_owner:
        rows.append(row_of(maybe("➕ Добавить", f"add:s:{si}")))
    rows = [row for row in rows if row]
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
    lines = [f"<b>{escape(subject.get('name') or '')}</b>", escape(section.get("title") or "")]
    if body:
        lines += ["", body]
    elif not (links or files):
        lines += ["", "Пока ничего нет."]
    rows = [[url_btn(f"🔗 {short(link.get('title') or link['url'], 40)}", link["url"])] for link in links]
    for index, meta in enumerate(files):
        _, file_name = file_meta(meta)
        text = f"📄 {short(file_name, 40)}"
        url = file_link(si, j, index, meta)
        rows.append([url_btn(text, url) if url else b(text, f"file:{si}:{j}:{index}")])
    rows.append(nav(f"s:{si}"))
    await ctx.show("\n".join(lines), rows)


async def screen_tuis(ctx: Ctx, si: int) -> None:
    """🎓 ТУИС предмета: короткая сводка + кнопки на БРС, литературу, планы, итоговый контроль."""
    data = ctx.data
    try:
        subject = data["subjects"][si]
    except (IndexError, KeyError, TypeError):
        await ctx.show("Предмет не найден.", [nav("subs")])
        return
    name = subject.get("name") or ""
    try:
        summary = tuis.overview_lines(data, name)
        parts = tuis.parts_for(name)
    except Exception:
        summary, parts = [], []
    lines = [f"🎓 <b>ТУИС · {escape(name)}</b>", ""]
    lines += summary or ["Документов ТУИС по предмету нет."]
    rows = chunk([b(tuis.PARTS[key][0], f"s:{si}:t:{key}") for key in parts], 2)
    found = tuis.courses_for(name)
    if found:
        rows.append(
            row_of(
                *[
                    maybe(f"📊 Мои баллы{' · ' + short(tu.short_name(c['name']), 18) if len(found) > 1 else ''}", f"sc:c:{c['key']}|s:{si}:t")
                    for c in found
                ]
            )
        )
    rows.append(nav(f"s:{si}"))
    await ctx.show("\n".join(lines), rows)


async def screen_tuis_part(ctx: Ctx, si: int, key: str) -> None:
    data = ctx.data
    try:
        subject = data["subjects"][si]
    except (IndexError, KeyError, TypeError):
        await ctx.show("Предмет не найден.", [nav("subs")])
        return
    if key not in tuis.PARTS:
        await screen_tuis(ctx, si)
        return
    name = subject.get("name") or ""
    try:
        text, links = tuis.part_text(name, key)
    except Exception:
        text, links = "", []
    lines = [f"<b>{escape(name)}</b> · ТУИС", f"<b>{tuis.PARTS[key][1]}</b>", "", text or "Нет данных."]
    rows = [
        [url_btn(f"🔗 {short(link.get('title') or link['url'], 40)}", link["url"])]
        for link in links
        if isinstance(link, dict) and is_url(link.get("url"))
    ]
    rows.append(nav(f"s:{si}:t"))
    await ctx.show("\n".join(lines), rows)


async def on_subjects(ctx: Ctx, args: list[str]) -> None:
    await screen_subjects(ctx)


async def on_subject(ctx: Ctx, args: list[str]) -> None:
    if len(args) >= 2 and args[1] == "t":  # s:{si}:t — раздел ТУИС, s:{si}:t:{часть}
        try:
            si = int(args[0])
            if si < 0:
                raise ValueError
        except ValueError:
            await ctx.show("Предмет не найден.", [nav("subs")])
            return
        if len(args) >= 3:
            await screen_tuis_part(ctx, si, args[2])
        else:
            await screen_tuis(ctx, si)
        return
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
    menu=[MenuButton("📚 Предметы", "subs", order=20, row="m2")],
    commands=[CommandSpec("subjects", "Предметы и материалы", cmd_subjects, hidden=True)],
)
