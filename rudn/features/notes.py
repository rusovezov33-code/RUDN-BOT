"""📌 Заметки: свои объявления владельца на главном экране.

Только то, что владелец добавил сам (data.json → "notes"). Добавить — кнопкой
«➕ Добавить заметку» и текстом сообщением; удалить — 🗑 у заметки (с подтверждением).
Тестер видит список, но не меняет его."""
from __future__ import annotations

from html import escape

from rudn.core import tasks, timeutil as tu
from rudn.core.registry import Feature, MenuButton
from rudn.core.ui import Ctx, b, cancel_row, nav, short

KEY = "notes"
STATE = "notes_add"
PREFIX = "nt"
MAX_LEN = 1000
HOME_LIMIT = 10


def notes_of(data: dict) -> list[dict]:
    raw = data.get(KEY)
    return [n for n in raw if isinstance(n, dict) and str(n.get("text") or "").strip()] if isinstance(raw, list) else []


def _note_lines(text: str, limit: int | None = None) -> list[str]:
    parts = [p for p in str(text).split("\n") if p.strip()] or [""]
    out = []
    for index, part in enumerate(parts):
        body = escape(short(part, limit) if limit else part)
        out.append(("• " if index == 0 else "  ") + body)
    return out


def summary(ctx: Ctx) -> list[str]:
    """Главный экран: напоминания на сегодня (добавлены через ➕ → 🔔) и заметки. Пусто — ничего."""
    data = ctx.data
    lines: list[str] = []
    # Простым текстом, без заголовков и маркеров: сначала напоминания на сегодня, потом заметки.
    for item in tasks.events_on(data, tu.today()):
        lines += _plain_lines(item.get("text") or "", 200)
    notes = notes_of(data)
    for note in notes[:HOME_LIMIT]:
        lines += _plain_lines(note["text"], 200)
    if len(notes) > HOME_LIMIT:
        lines.append(f"и ещё {len(notes) - HOME_LIMIT}")
    return lines


def _plain_lines(text: str, limit: int) -> list[str]:
    return [escape(short(part, limit)) for part in str(text).split("\n") if part.strip()]


async def screen_notes(ctx: Ctx, notice: str | None = None) -> None:
    notes = notes_of(ctx.data)
    lines = ["📌 <b>Заметки</b>"]
    if notice:
        lines.append(notice)
    lines.append("")
    if notes:
        for index, note in enumerate(notes, 1):
            body = _note_lines(note["text"])
            body[0] = f"{index}. " + body[0][2:]
            lines += body
    else:
        lines.append("Пока пусто.")
    rows: list[list] = []
    if ctx.is_owner:
        rows.append([b("➕ Добавить", f"{PREFIX}:a")])
        for index, note in enumerate(notes):
            first = str(note["text"]).split("\n")[0]
            rows.append([b(f"🗑 {index + 1}. {short(first, 14)}", f"{PREFIX}:d:{index}")])
    rows.append(nav())
    await ctx.show("\n".join(lines), rows)


async def on_notes(ctx: Ctx, args: list[str]) -> None:
    action = args[0] if args else ""
    if action == "":
        await screen_notes(ctx)
        return
    if not await ctx.require_owner():
        return
    if action == "a":
        ctx.wait_text(STATE)
        await ctx.show(
            "📌 <b>Новая заметка</b>\n\nНапиши текст сообщением — он появится на главном экране.\n<i>(ожидаю 15 минут)</i>",
            [cancel_row(PREFIX), nav(PREFIX)],
        )
        return
    if action == "d":
        notes = notes_of(ctx.data)
        try:
            index = int(args[1])
            note = notes[index]
        except (IndexError, ValueError):
            await ctx.answer("Заметка уже удалена", alert=True)
            await screen_notes(ctx)
            return
        if len(args) > 2 and args[2] == "y":
            ctx.data[KEY] = [n for n in notes if n is not note]
            ctx.save()
            ctx.toast = "🗑 Удалено"
            await screen_notes(ctx)
            return
        text = "\n".join(escape(part) for part in str(note["text"]).split("\n"))
        await ctx.show(
            f"🗑 <b>Удалить заметку?</b>\n\n{text}",
            [[b("🗑 Да, удалить", f"{PREFIX}:d:{index}:y"), b("Отмена", PREFIX)], nav(PREFIX)],
        )
        return
    await screen_notes(ctx)


async def on_note_text(ctx: Ctx, text: str) -> None:
    if not ctx.is_owner:
        return
    if not text:
        await ctx.show("📌 Пустой текст — напиши заметку сообщением.", [cancel_row(PREFIX), nav(PREFIX)])
        return
    ctx.clear_pending()
    notes = notes_of(ctx.data)
    notes.append({"text": text[:MAX_LEN], "created": tu.now().isoformat()})
    ctx.data[KEY] = notes
    ctx.save()
    await screen_notes(ctx, notice="✅ Добавлено — видно на главном экране.")


feature = Feature(
    name="notes",
    callbacks={PREFIX: on_notes},
    text_states={STATE: on_note_text},
    menu=[MenuButton("📌 Заметки", PREFIX, order=30, row="m3")],
    summary=summary,
    summary_order=20,
)
