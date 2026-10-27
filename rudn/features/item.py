"""Экран одного пункта (Д/з, лаба, контрольная, экзамен/зачёт): ✅ / ✏️ / 📅 / 🗑.

Открывается кнопкой «✏️» рядом с ✅ в «🔥 Дедлайнах», «📝 Заданиях» предмета и
«🎓 Экзаменах». Удаление — с подтверждением; засеянный из ТУИС пункт после
удаления больше не появляется (tasks.delete_item).

callback_data: it:<действие>:<id>:<откуда>
  откуда: a — все дедлайны, sN — задания предмета N, e — экзамены, p — прошедшие экзамены
"""
from __future__ import annotations

from html import escape

from rudn.core import tasks, timeutil as tu
from rudn.core.registry import Feature
from rudn.core.ui import Ctx, b, cancel_row, nav, short
from rudn.features import deadlines, exams

STATE = "item_edit"
ORIGINS = ("a", "e", "p")


def origin_ok(origin: str) -> str:
    if origin in ORIGINS or deadlines.parse_view(origin) is not None:
        return origin
    return "a"


def back_of(origin: str) -> str:
    if origin == "e":
        return "ex"
    if origin == "p":
        return "ex:p"
    return "dl" if origin == "a" else f"dl:{origin}"


async def back_to_list(ctx: Ctx, origin: str, undo: dict | None = None) -> None:
    if origin in ("e", "p"):
        await exams.render(ctx, past=origin == "p", undo=undo if origin == "e" else None)
    else:
        await deadlines.render(ctx, origin, undo=undo)


def item_text(item: dict, data: dict) -> str:
    today = tu.today()
    is_exam = tasks.kind_of(item) == "exam"
    lines = [f"{tasks.kind_icon(item)} <b>{escape(tasks.kind_label(item))}</b>"]
    subject = tasks.subject_name(item, data)
    if subject:
        lines.append(f"Предмет: <b>{escape(subject)}</b>")
    due = tasks.item_date(item)
    when = escape(tasks.due_text(item, today))
    lines.append(f"{'Дата' if is_exam else 'Срок'}: <b>{when}</b>")
    hint = str(item.get("date_hint") or "")
    if due and hint == "week":
        lines.append("≈ дата вычислена по номеру недели (от начала занятий группы)")
    elif due and hint == "regular":
        lines.append("⚠️ дата основного потока, уточнить")
    details = ", ".join(escape(str(item[key])) for key in ("time", "place") if item.get(key))
    if details:
        lines.append(f"🕘 {details}")
    lines.append("")
    lines.append(escape(tasks.body_text(item) or "—"))
    if item.get("tuis_key"):
        lines.append("")
        lines.append("<i>Из документов ТУИС.</i>")
    if item.get("done"):
        lines.append("")
        lines.append("☑️ Отмечено как " + ("сдано." if is_exam else "сделано."))
    return "\n".join(lines)


async def screen_item(ctx: Ctx, item: dict, origin: str) -> None:
    item_id = item["id"]
    is_exam = tasks.kind_of(item) == "exam"
    if item.get("done"):
        status = b("↩ Не сделано" if not is_exam else "↩ Не сдано", f"it:u:{item_id}:{origin}")
    else:
        status = b("✅ Сдал" if is_exam else "✅ Готово", f"it:d:{item_id}:{origin}")
    rows = [
        [status],
        [b("✏️ Текст", f"it:t:{item_id}:{origin}"), b("📅 Дата" if is_exam else "📅 Срок", f"it:w:{item_id}:{origin}")],
        [b("🗑 Удалить", f"it:x:{item_id}:{origin}")],
        nav(back_of(origin)),
    ]
    await ctx.show(item_text(item, ctx.data), rows)


async def screen_confirm(ctx: Ctx, item: dict, origin: str) -> None:
    label = f"{tasks.kind_label(item)}: {tasks.body_text(item) or '—'}"
    subject = tasks.subject_name(item, ctx.data)
    lines = ["🗑 <b>Удалить насовсем?</b>", ""]
    if subject:
        lines.append(f"<b>{escape(subject)}</b>")
    lines.append(escape(short(label, 300)))
    lines.append("")
    lines.append("Если уже сделано — лучше «✅ Готово»: пункт уйдёт в «☑️ Выполненные».")
    rows = [
        [b("🗑 Да, удалить", f"it:X:{item['id']}:{origin}"), b("Отмена", f"it:o:{item['id']}:{origin}")],
        nav(f"it:o:{item['id']}:{origin}"),
    ]
    await ctx.show("\n".join(lines), rows)


async def screen_ask(ctx: Ctx, item: dict, origin: str, field: str, error: str = "") -> None:
    ctx.wait_text(STATE, keep=("it",), id=item["id"], origin=origin, field=field)
    if field == "text":
        prompt = ["✏️ <b>Новый текст</b>", "", "Сейчас:", escape(str(item.get("text") or "—"))]
    else:
        due = tasks.item_date(item)
        prompt = [
            "📅 <b>Новая дата</b>",
            "",
            f"Сейчас: {escape(tu.fmt_dm(due)) if due else 'без срока'}",
            "Пришли дату, например <code>15.12</code> или <code>15.12.2026</code>.",
            "«-» — убрать срок.",
        ]
    if error:
        prompt.insert(0, f"⚠️ {escape(error)}\n")
    back = f"it:o:{item['id']}:{origin}"
    await ctx.show("\n".join(prompt), [cancel_row(back), [b("🏠 Меню", "home")]])


def set_date(item: dict, due) -> None:
    for key in ("day", "month", "year", "date", "date_hint"):
        item.pop(key, None)
    if due is not None:
        item.update(day=due.day, month=due.month, year=due.year, date=due.isoformat())


async def on_item_text(ctx: Ctx, text: str) -> None:
    state = ctx.pending()
    if state is None:
        return
    info = state.data
    origin = origin_ok(str(info.get("origin") or "a"))
    item = tasks.find(ctx.data, info.get("id"))
    if item is None:
        ctx.clear_pending()
        ctx.notice = "Пункт уже удалён."
        await back_to_list(ctx, origin)
        return
    if info.get("field") == "date":
        if text.strip() in ("-", "—", "нет"):
            due = None
        else:
            due = tu.parse_user_date(text)
            if due is None:
                await screen_ask(ctx, item, origin, "date", "Не понял дату. Пример: 15.12 или 15.12.2026")
                return
        set_date(item, due)
        ctx.notice = "📅 Срок изменён."
    else:
        if not text:
            await screen_ask(ctx, item, origin, "text", "Пустой текст")
            return
        item["text"] = text[:2000]
        ctx.notice = "✏️ Текст изменён."
    ctx.clear_pending()
    ctx.save()
    await screen_item(ctx, item, origin)


async def on_item(ctx: Ctx, args: list[str]) -> None:
    action = args[0] if args else ""
    try:
        item_id = int(args[1])
    except (IndexError, ValueError):
        await ctx.answer("Не найдено", alert=True)
        return
    origin = origin_ok(args[2] if len(args) > 2 else "a")
    if not await ctx.require_owner():
        return
    item = tasks.find(ctx.data, item_id)
    if item is None:
        await ctx.answer("Пункт уже удалён", alert=True)
        await back_to_list(ctx, origin)
        return
    if action == "o":
        await screen_item(ctx, item, origin)
    elif action in ("d", "u"):
        tasks.mark_done(item, action == "d")
        ctx.save()
        if action == "d":
            ctx.toast = "🎉 Отмечено как сдано" if tasks.kind_of(item) == "exam" else "✅ Готово"
            await back_to_list(ctx, origin, undo=item)
        else:
            ctx.toast = "↩ Вернул в список"
            await screen_item(ctx, item, origin)
    elif action in ("t", "w"):
        await screen_ask(ctx, item, origin, "text" if action == "t" else "date")
    elif action == "x":
        await screen_confirm(ctx, item, origin)
    elif action == "X":
        tasks.delete_item(ctx.data, item)
        ctx.save()
        ctx.toast = "🗑 Удалено"
        await back_to_list(ctx, origin)
    else:
        await screen_item(ctx, item, origin)


feature = Feature(
    name="item",
    callbacks={"it": on_item},
    text_states={STATE: on_item_text},
)
