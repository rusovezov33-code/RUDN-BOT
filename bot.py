import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout, web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    Message,
)

TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0")) or None
BUNDLED_DATA = Path(__file__).resolve().with_name("data.json")
MSK = timezone(timedelta(hours=3))

# Якорь семестра: понедельник 26.10.2026 — верхняя неделя.
WEEK_ANCHOR = date(2026, 10, 26)
WEEK_ANCHOR_IS_UPPER = True

router = Router()
pending_uploads: dict[int, dict[str, Any]] = {}
pending_events: dict[int, str] = {}
pending_homework: dict[int, dict[str, Any]] = {}
pending_restore: dict[int, str] = {}

SCHEDULE = {
    "ПН": [
        ("12:00–13:20", "Русский язык для иностранцев", "ЕГФ ауд. 375", "both"),
        ("13:30–14:50", "Русский язык для иностранцев", "ЕГФ ауд. 375", "both"),
        ("19:30–20:50", "Введение в специальность", "лек (ДОТ)", "lower"),
    ],
    "ВТ": [
        ("9:00–10:20", "Латинский язык", "ЕГФ ауд. 284", "both"),
        ("10:30–11:50", "Латинский язык", "ЕГФ ауд. 284/281", "both"),
        ("13:30–14:50", "Химия", "ул. Орджоникидзе 3, ауд. 623", "both"),
        ("19:30–20:50", "Физика", "лек н.н. (ДОТ)", "lower"),
    ],
    "СР": [
        ("15:00–16:20", "РКИ", "ЕГФ ауд. 359", "both"),
        ("16:30–17:50", "РКИ", "ЕГФ ауд. 359", "both"),
        ("19:30–20:50", "Основы российской государственности", "лек н.н. (ДОТ)", "lower"),
    ],
    "ЧТ": [
        ("10:30–11:50", "Основы военной подготовки / БЖД", "ВПМ", "both"),
        ("12:00–13:20", "Медицинская информатика", "ЕГФ ауд. 452/451", "both"),
        ("13:30–14:10", "Медицинская информатика", "ЕГФ ауд. 452/451", "both"),
        ("18:00–19:20", "Химия биогенных элементов", "(ДОТ)", "both"),
    ],
    "ПТ": [
        ("9:00–10:20", "Медицинская элементология", "АТИ ауд. 7", "both"),
        ("15:00–16:20", "РЯВАС", "ИРЯ ауд. 303", "both"),
        ("16:30–17:50", "РЯВАС", "ИРЯ ауд. 303", "both"),
    ],
    "СБ": [
        ("9:00–10:20", "Математика", "ЕГФ ауд. 331", "lower"),
        ("11:10–13:20", "Физическая культура", "ФОК", "both"),
        ("15:00–16:20", "Основы российской государственности", "ЕГФ ауд. 285", "both"),
    ],
}

CANONICAL = [
    "Русский язык для иностранцев",
    "Введение в специальность",
    "Латинский язык",
    "Химия",
    "Физика",
    "РКИ",
    "Основы российской государственности",
    "Основы военной подготовки / БЖД",
    "Медицинская информатика",
    "Химия биогенных элементов",
    "Медицинская элементология",
    "РЯВАС",
    "Математика",
    "Физическая культура",
]

RENAMES = {
    "Основы военной подготовки": "Основы военной подготовки / БЖД",
}

LEGACY_DESCRIPTIONS = {
    "РЯдляИ / РКИ / РЯВАС",
    "Включая химию биогенных элементов",
}

DAY_MAP = {0: "ПН", 1: "ВТ", 2: "СР", 3: "ЧТ", 4: "ПТ", 5: "СБ", 6: "ВС"}
FULL_DAY = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
WEEK_NOTE = {"both": "каждую неделю", "lower": "нижняя неделя", "upper": "верхняя неделя"}

EXT_MIME = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain; charset=utf-8",
    ".zip": "application/zip",
}

ALIASES = {
    "основы военной подготовки": "основы военной подготовки / бжд",
    "бжд": "основы военной подготовки / бжд",
}


def data_path() -> Path:
    override = os.getenv("DATA_FILE")
    if override:
        return Path(override)
    volume = Path(os.getenv("DATA_DIR", "/data"))
    if volume.is_dir() and os.access(volume, os.W_OK):
        return volume / "data.json"
    return BUNDLED_DATA


def link_secret() -> str:
    return os.getenv("FILE_LINK_SECRET") or os.getenv("BOT_TOKEN") or "rudn-bot"


def public_base() -> str | None:
    for key in ("PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL"):
        value = os.getenv(key, "").strip().rstrip("/")
        if not value:
            continue
        if not value.startswith("http://") and not value.startswith("https://"):
            value = "https://" + value
        return value
    return None


def get_moscow_now() -> datetime:
    return datetime.now(MSK)


def week_type(day: date) -> str:
    monday = day - timedelta(days=day.weekday())
    weeks = (monday - WEEK_ANCHOR).days // 7
    upper = (weeks % 2 == 0) if WEEK_ANCHOR_IS_UPPER else (weeks % 2 == 1)
    return "upper" if upper else "lower"


def week_label(kind: str) -> str:
    return "верхняя" if kind == "upper" else "нижняя"


def norm_name(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def subjects_match(left: str, right: str) -> bool:
    a = ALIASES.get(norm_name(left), norm_name(left))
    b = ALIASES.get(norm_name(right), norm_name(right))
    return bool(a) and a == b


def lessons_for(day: date) -> list[tuple[str, str, str, str]]:
    kind = week_type(day)
    code = DAY_MAP[day.weekday()]
    return [item for item in SCHEDULE.get(code, []) if item[3] in ("both", kind)]


def slots_for_subject(name: str) -> list[tuple[str, str, str, str]]:
    found = []
    for code, lessons in SCHEDULE.items():
        for start, subject, place, kind in lessons:
            if subjects_match(subject, name):
                found.append((code, start, place, kind))
    return found


def upcoming_lesson_dates(name: str, start: date, days: int = 60) -> list[date]:
    found = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        if any(subjects_match(lesson[1], name) for lesson in lessons_for(day)):
            found.append(day)
    return found


def fmt_day(day: date) -> str:
    return f"{FULL_DAY[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}"


def fmt_short(day: date) -> str:
    return f"{day.day}.{day.month:02d}"


def lesson_bounds(time_str: str) -> tuple[int, int] | None:
    parts = re.split(r"\s*[–—-]\s*", time_str.strip())
    if len(parts) != 2:
        return None

    def parse(part: str) -> int:
        hour, minute = part.strip().split(":")
        return int(hour) * 60 + int(minute)

    try:
        return parse(parts[0]), parse(parts[1])
    except ValueError:
        return None


def is_now(time_str: str, now: datetime) -> bool:
    bounds = lesson_bounds(time_str)
    if not bounds:
        return False
    start, end = bounds
    current = now.hour * 60 + now.minute
    return start <= current <= end


def item_on_date(item: dict, day: date) -> bool:
    if item.get("date"):
        return item["date"] == day.isoformat()
    if item.get("day") != day.day:
        return False
    month = item.get("month")
    year = item.get("year")
    if month and year:
        return int(month) == day.month and int(year) == day.year
    if month:
        return int(month) == day.month
    created = item.get("created")
    if created:
        try:
            stamp = datetime.fromisoformat(created)
        except ValueError:
            return True
        return stamp.year == day.year and stamp.month == day.month
    return True


def item_date(item: dict) -> date | None:
    if item.get("date"):
        try:
            return date.fromisoformat(item["date"])
        except ValueError:
            return None
    day_num = item.get("day")
    if not isinstance(day_num, int):
        return None
    month = item.get("month")
    year = item.get("year")
    if not (month and year) and item.get("created"):
        try:
            stamp = datetime.fromisoformat(item["created"])
        except ValueError:
            stamp = None
        if stamp is not None:
            month = month or stamp.month
            year = year or stamp.year
    if not month or not year:
        return None
    try:
        return date(int(year), int(month), day_num)
    except ValueError:
        return None


def homework_subject_name(item: dict, data: dict) -> str:
    if item.get("subject"):
        return str(item["subject"])
    index = item.get("subject_idx")
    subjects = data.get("subjects") or []
    if isinstance(index, int) and 0 <= index < len(subjects):
        return str(subjects[index].get("name") or "")
    return ""


def section_template() -> list[dict[str, Any]]:
    return [
        {"title": "📖 Учебная литература", "text": "", "links": [], "files": []},
        {"title": "🗂 Лекции и конспекты", "text": "", "links": [], "files": []},
        {"title": "🧪 Практики / лабораторные", "text": "", "links": [], "files": []},
        {"title": "📋 Требования и баллы — БРС", "text": "", "links": [], "files": []},
        {"title": "🎓 Экзамен / зачёт", "text": "", "links": [], "files": []},
        {
            "title": "🔗 Ссылки — ТУИС, Teams и другие источники",
            "text": "",
            "links": [{"title": "ТУИС", "url": "https://esystem.rudn.ru"}],
            "files": [],
        },
    ]


def blank_subject(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "teacher": "Не указано",
        "semester": 1,
        "contacts": "Не указано",
        "description": "",
        "sections": section_template(),
    }


def subject_has_content(subject: dict, homework: list[dict], index: int) -> bool:
    for section in subject.get("sections") or []:
        if section.get("files"):
            return True
        if str(section.get("text") or "").strip():
            return True
        for link in section.get("links") or []:
            url = str(link.get("url") or "")
            if url and "esystem.rudn.ru" not in url:
                return True
    name = subject.get("name")
    for item in homework:
        if item.get("subject") == name or item.get("subject_idx") == index:
            return True
    return False


def normalize(data: dict) -> dict:
    if not isinstance(data, dict):
        data = {}
    data.setdefault("pages", [])
    data.setdefault("subjects", [])
    data.setdefault("events", [])
    data.setdefault("homework", [])
    subjects = [item for item in data["subjects"] if isinstance(item, dict)]
    homework = [item for item in data["homework"] if isinstance(item, dict)]
    data["subjects"] = subjects
    data["homework"] = homework

    for item in homework:
        if item.get("subject"):
            continue
        index = item.get("subject_idx")
        if isinstance(index, int) and 0 <= index < len(subjects):
            item["subject"] = subjects[index].get("name")

    for subject in subjects:
        renamed = RENAMES.get(subject.get("name", ""))
        if renamed:
            subject["name"] = renamed
        if subject.get("description") in LEGACY_DESCRIPTIONS:
            subject["description"] = ""
    for item in homework:
        renamed = RENAMES.get(item.get("subject", ""))
        if renamed:
            item["subject"] = renamed

    kept = []
    for index, subject in enumerate(subjects):
        if subject.get("name") == "Экономика" and not subject_has_content(subject, homework, index):
            continue
        kept.append(subject)

    existing: dict[str, dict] = {}
    extras: list[dict] = []
    for subject in kept:
        name = subject.get("name") or "Без названия"
        subject["name"] = name
        if name in CANONICAL and name not in existing:
            existing[name] = subject
        else:
            extras.append(subject)
    ordered = [existing[name] if name in existing else blank_subject(name) for name in CANONICAL]
    ordered.extend(extras)
    data["subjects"] = ordered
    index_by_name = {subject["name"]: index for index, subject in enumerate(ordered)}
    for item in homework:
        name = item.get("subject")
        if name in index_by_name:
            item["subject_idx"] = index_by_name[name]
    return data


def ensure_data_file(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if BUNDLED_DATA.exists() and BUNDLED_DATA.resolve() != path.resolve():
        path.write_text(BUNDLED_DATA.read_text(encoding="utf-8"), encoding="utf-8")
        return
    empty = {"pages": [], "subjects": [], "events": [], "homework": []}
    path.write_text(json.dumps(empty, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save(data: dict) -> None:
    path = data_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load() -> dict:
    path = data_path()
    ensure_data_file(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("не удалось прочитать %s", path)
        raw = {"pages": [], "subjects": [], "events": [], "homework": []}
    data = normalize(raw)
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        current = path.read_text(encoding="utf-8")
    except Exception:
        current = ""
    if current != encoded:
        path.write_text(encoded, encoding="utf-8")
        logging.info("данные приведены в порядок: %s", path)
    return data


def count_files(data: dict) -> int:
    total = 0
    for subject in data.get("subjects") or []:
        for section in subject.get("sections") or []:
            total += len(section.get("files") or [])
    return total


def is_owner(user_id: int | None) -> bool:
    return OWNER_ID is not None and user_id == OWNER_ID


def user_id_of(event: Any) -> int | None:
    user = getattr(event, "from_user", None)
    return user.id if user else None


def clear_pending(user_id: int | None) -> None:
    if user_id is None:
        return
    pending_uploads.pop(user_id, None)
    pending_events.pop(user_id, None)
    pending_homework.pop(user_id, None)
    pending_restore.pop(user_id, None)


def kb(rows: list[list[Btn]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def nav(back: str | None = None) -> list[Btn]:
    row = []
    if back and back != "home":
        row.append(Btn(text="⬅️ Назад", callback_data=back))
    row.append(Btn(text="🏠 Меню", callback_data="home"))
    return row


def link_rows(links: list[dict]) -> list[list[Btn]]:
    return [[Btn(text=f"🔗 {link['title']}", url=link["url"])] for link in links if link.get("url")]


def make_token(si: int, j: int, fi: int, file_id: str) -> str:
    payload = json.dumps(
        {"si": si, "j": j, "fi": fi, "id": file_id, "exp": int(time.time()) + 30 * 24 * 3600},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    blob = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(link_secret().encode(), payload, hashlib.sha256).hexdigest()
    return f"{blob}.{signature}"


def parse_token(token: str) -> dict | None:
    if not token or len(token) > 4000 or "." not in token:
        return None
    blob, signature = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    except Exception:
        return None
    expected = hmac.new(link_secret().encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        parsed = json.loads(payload)
    except Exception:
        return None
    if not isinstance(parsed, dict) or int(parsed.get("exp", 0)) < time.time():
        return None
    return parsed


def file_meta(meta: Any) -> tuple[str, str]:
    if isinstance(meta, dict):
        return str(meta.get("file_id") or ""), str(meta.get("name") or "файл")
    return str(meta or ""), "файл"


def file_link(si: int, j: int, fi: int, meta: Any) -> str | None:
    base = public_base()
    file_id, name = file_meta(meta)
    if not base or not file_id:
        return None
    safe = quote(name.replace("/", "_"))
    url = f"{base}/dl/{make_token(si, j, fi, file_id)}/{safe}"
    return url if len(url) <= 2000 else None


def file_button(si: int, j: int, fi: int, meta: Any) -> Btn:
    _, name = file_meta(meta)
    label = name if len(name) <= 40 else name[:37] + "…"
    text = f"📄 {label}"
    if len(text) > 64:
        text = text[:63] + "…"
    url = file_link(si, j, fi, meta)
    if url:
        return Btn(text=text, url=url)
    return Btn(text=text, callback_data=f"file:{si}:{j}:{fi}")


def guess_mime(name: str, stored: str | None) -> str:
    if stored and stored != "application/octet-stream":
        return stored
    return EXT_MIME.get(Path(name).suffix.lower(), "application/octet-stream")


def content_disposition(name: str) -> str:
    clean = name.replace("\\", "").replace('"', "").replace("\r", "").replace("\n", "") or "file"
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in clean) or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean)}"


def next_hw_id(items: list[dict]) -> int:
    ids = [item.get("id") for item in items if isinstance(item.get("id"), int)]
    return max(ids) + 1 if ids else 0


def clip(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def mark(active: str, key: str, title: str) -> str:
    return f"«{title}»" if active == key else title


def schedule_keyboard(active: str, ref: date) -> InlineKeyboardMarkup:
    monday = ref - timedelta(days=ref.weekday())
    rows: list[list[Btn]] = [
        [
            Btn(text=mark(active, "today", "Сегодня"), callback_data="sched:today"),
            Btn(text=mark(active, "tomorrow", "Завтра"), callback_data="sched:tomorrow"),
        ],
        [Btn(text=mark(active, "week", "Неделя"), callback_data=f"week:{monday.isoformat()}")],
    ]
    row: list[Btn] = []
    for offset in range(6):
        day = monday + timedelta(days=offset)
        row.append(Btn(text=f"{DAY_MAP[day.weekday()]} {day.day}", callback_data=f"sched:{day.isoformat()}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(nav())
    return kb(rows)


def active_key(view: str, day: date, today: date) -> str:
    if view == "week":
        return "week"
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return ""


async def show(event: CallbackQuery | Message, text: str, markup: InlineKeyboardMarkup) -> None:
    text = clip(text)
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except TelegramBadRequest as error:
            lowered = str(error).lower()
            if "message is not modified" in lowered:
                await event.answer()
                return
            try:
                await event.message.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            except Exception:
                logging.error("screen failed: %s", type(error).__name__)
        try:
            await event.answer()
        except TelegramBadRequest:
            pass
        return
    await event.answer(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def screen_home(event: CallbackQuery | Message) -> None:
    clear_pending(user_id_of(event))
    data = load()
    today = get_moscow_now().date()
    rows = [
        [
            Btn(text="📅 Сегодня", callback_data="sched:today"),
            Btn(text="⏭ Завтра", callback_data="sched:tomorrow"),
        ],
        [Btn(text="🗓 Неделя", callback_data=f"week:{today.isoformat()}")],
        [Btn(text="📚 Предметы", callback_data="subs")],
    ]
    pages = []
    for index, page in enumerate(data.get("pages") or []):
        if "Расписание" in str(page.get("title") or ""):
            continue
        pages.append(Btn(text=page.get("title") or "Страница", callback_data=f"p:{index}"))
    rows += [pages[index : index + 2] for index in range(0, len(pages), 2)]
    if is_owner(user_id_of(event)):
        rows.append([Btn(text="➕ Событие", callback_data="addevent"), Btn(text="📝 Д/з", callback_data="addhw")])
    text = (
        "🎓 <b>РУДН</b>\n"
        f"{escape(fmt_day(today))}\n"
        f"{escape(week_label(week_type(today)).capitalize())} неделя"
    )
    await show(event, text, kb(rows))


async def screen_subjects(event: CallbackQuery | Message) -> None:
    data = load()
    subjects = data.get("subjects") or []
    rows = [[Btn(text=subject["name"], callback_data=f"s:{index}")] for index, subject in enumerate(subjects)]
    rows.append(nav())
    text = "📚 <b>Предметы</b>" if subjects else "📚 <b>Предметы</b>\n\nПока ничего нет."
    await show(event, text, kb(rows))


async def screen_subject(event: CallbackQuery | Message, si: int) -> None:
    data = load()
    try:
        subject = data["subjects"][si]
    except (IndexError, KeyError, TypeError):
        await show(event, "Предмет не найден.", kb([nav()]))
        return
    lines = [f"<b>{escape(subject.get('name') or 'Без названия')}</b>"]
    if subject.get("teacher") and subject["teacher"] != "Не указано":
        lines.append(f"👤 {escape(subject['teacher'])}")
    if subject.get("contacts") and subject["contacts"] != "Не указано":
        lines.append(f"✉️ {escape(subject['contacts'])}")
    if subject.get("description"):
        lines.append(escape(subject["description"]))
    slots = slots_for_subject(subject.get("name") or "")
    if slots:
        lines.append("")
        for code, start, place, kind in slots:
            lines.append(f"🗓 {code} {escape(start)} · {escape(place)} · {WEEK_NOTE[kind]}")
    rows = []
    upcoming = upcoming_lesson_dates(subject.get("name") or "", get_moscow_now().date(), 45)
    if upcoming:
        nearest = upcoming[0]
        rows.append([Btn(text=f"📅 Ближайшая · {fmt_short(nearest)}", callback_data=f"sched:{nearest.isoformat()}")])
    rows += [[Btn(text=section["title"], callback_data=f"s:{si}:{index}")] for index, section in enumerate(subject.get("sections") or [])]
    titles = " ".join(str(section.get("title") or "") for section in subject.get("sections") or []).lower()
    if "д/з" not in titles:
        rows.append([Btn(text="📝 Д/з", callback_data=f"hwview:{si}")])
    rows.append(nav("subs"))
    await show(event, "\n".join(lines), kb(rows))


async def screen_section(event: CallbackQuery | Message, si: int, j: int) -> None:
    data = load()
    try:
        subject = data["subjects"][si]
        section = subject["sections"][j]
    except (IndexError, KeyError, TypeError):
        await show(event, "Раздел не найден.", kb([nav()]))
        return
    body = escape(section.get("text") or "")
    files = section.get("files") or []
    has_any = body or section.get("links") or files
    text = (
        f"<b>{escape(subject.get('name') or '')}</b>\n"
        f"{escape(section.get('title') or '')}\n\n"
        + (body if has_any else "Пока ничего нет.")
    )
    if files and public_base():
        text += "\n\nНажми на файл и выбери приложение. В чат он не придёт."
    elif files and is_owner(user_id_of(event)) and not public_base():
        text += "\n\nФайл пока придёт сообщением: у бота нет публичного адреса."
    rows = link_rows(section.get("links") or [])
    for index, meta in enumerate(files):
        rows.append([file_button(si, j, index, meta)])
    rows.append(nav(f"s:{si}"))
    await show(event, text, kb(rows))


async def screen_homework(event: CallbackQuery | Message, si: int) -> None:
    data = load()
    try:
        subject = data["subjects"][si]
    except (IndexError, KeyError, TypeError):
        await show(event, "Предмет не найден.", kb([nav()]))
        return
    today = get_moscow_now().date()
    items = []
    for item in data.get("homework") or []:
        if item.get("done"):
            continue
        same = item.get("subject_idx") == si or item.get("subject") == subject.get("name")
        if not same:
            continue
        due = item_date(item)
        if due is None or due >= today:
            items.append(item)
    items.sort(key=lambda item: item_date(item) or date.max)
    lines = [f"📝 <b>{escape(subject.get('name') or '')}</b>\n"]
    if not items:
        lines.append("Актуальных заданий нет.")
    for item in items:
        due = item_date(item)
        when = fmt_short(due) if due else f"{item.get('day')} числа"
        lines.append(f"• до {when}: {escape(item.get('text') or '')}")
    rows = []
    if is_owner(user_id_of(event)):
        for item in items:
            label = (item.get("text") or "задание").strip()
            short = label if len(label) <= 28 else label[:27] + "…"
            rows.append([Btn(text=f"✅ {short}", callback_data=f"hwdone:{item.get('id', 0)}")])
    rows.append(nav(f"s:{si}"))
    await show(event, "\n".join(lines), kb(rows))


async def screen_page(event: CallbackQuery | Message, pi: int) -> None:
    data = load()
    try:
        page = data["pages"][pi]
    except (IndexError, KeyError, TypeError):
        await show(event, "Страница не найдена.", kb([nav()]))
        return
    if "Расписание" in str(page.get("title") or ""):
        await screen_schedule(event, get_moscow_now().date())
        return
    text = f"<b>{escape(page.get('title') or '')}</b>\n\n" + (escape(page.get("text") or "") or "Пока ничего нет.")
    rows = link_rows(page.get("links") or [])
    rows.append(nav())
    await show(event, text, kb(rows))


def append_lessons(lines: list[str], day: date, data: dict, now: datetime, with_place: bool) -> None:
    lessons = lessons_for(day)
    if not lessons:
        lines.append("Занятий нет.")
        return
    shown: set[int] = set()
    for start, subject, place, _ in lessons:
        current = "  ← сейчас" if day == now.date() and is_now(start, now) else ""
        lines.append(f"• {escape(start)} — {escape(subject)}{current}")
        if with_place and place:
            lines.append(f"  📍 {escape(place)}")
        for hw_index, item in enumerate(data.get("homework") or []):
            if hw_index in shown or item.get("done") or not item_on_date(item, day):
                continue
            if not subjects_match(homework_subject_name(item, data), subject):
                continue
            lines.append(f"  📝 {escape(item.get('text') or '')}")
            shown.add(hw_index)


def append_events(lines: list[str], day: date, data: dict) -> None:
    events = [item for item in data.get("events") or [] if item_on_date(item, day)]
    if not events:
        return
    lines.append("\n🔔 <b>Напоминания:</b>")
    for item in events:
        lines.append(f"• {escape(item.get('text') or '')}")


async def screen_schedule(event: CallbackQuery | Message, day: date) -> None:
    data = load()
    now = get_moscow_now()
    today = now.date()
    lines = [
        f"📅 <b>{escape(fmt_day(day))}</b>",
        f"{escape(week_label(week_type(day)).capitalize())} неделя\n",
    ]
    append_lessons(lines, day, data, now, with_place=True)
    append_events(lines, day, data)
    await show(event, "\n".join(lines), schedule_keyboard(active_key("day", day, today), day))


async def screen_week(event: CallbackQuery | Message, ref: date) -> None:
    data = load()
    now = get_moscow_now()
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    lines = [
        f"🗓 <b>{escape(week_label(week_type(monday)).capitalize())} неделя</b>",
        f"{fmt_short(monday)}–{fmt_short(sunday)}\n",
    ]
    for offset in range(6):
        day = monday + timedelta(days=offset)
        lines.append(f"<b>{DAY_MAP[day.weekday()]} {fmt_short(day)}</b>")
        lessons = lessons_for(day)
        if not lessons:
            lines.append("выходной")
        shown: set[int] = set()
        for start, subject, _, _ in lessons:
            mark_hw = ""
            for hw_index, item in enumerate(data.get("homework") or []):
                if hw_index in shown or item.get("done") or not item_on_date(item, day):
                    continue
                if subjects_match(homework_subject_name(item, data), subject):
                    mark_hw = " 📝"
                    shown.add(hw_index)
                    break
            lines.append(
                f"• {escape(start)} {escape(subject)}{mark_hw}"
                + ("  ← сейчас" if day == now.date() and is_now(start, now) else "")
            )
        for item in data.get("events") or []:
            if item_on_date(item, day):
                lines.append(f"🔔 {escape(item.get('text') or '')}")
        lines.append("")
    await show(event, "\n".join(lines).strip(), schedule_keyboard("week", monday))


def date_buttons(days: list[date], prefix: str, per_row: int) -> list[list[Btn]]:
    rows: list[list[Btn]] = []
    row: list[Btn] = []
    for day in days:
        row.append(Btn(text=fmt_short(day), callback_data=f"{prefix}:{day.isoformat()}"))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def parse_picked_date(raw: str) -> date | None:
    if raw == "cancel":
        return None
    if "-" in raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    try:
        day_num = int(raw)
        today = get_moscow_now().date()
        return date(today.year, today.month, day_num)
    except ValueError:
        return None


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await screen_home(message)


@router.message(Command("backup"))
async def cmd_backup(message: Message) -> None:
    if not is_owner(user_id_of(message)):
        await message.answer("Нет доступа")
        return
    load()
    await message.answer_document(FSInputFile(data_path()), caption="Копия data.json. Сохрани её до деплоя.")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not is_owner(user_id_of(message)):
        await message.answer("Нет доступа")
        return
    data = load()
    today = get_moscow_now().date()
    base = public_base()
    lines = [
        "<b>Статус</b>",
        f"Данные: <code>{escape(str(data_path()))}</code>",
        f"Неделя: {escape(week_label(week_type(today)))}",
        f"Предметов: {len(data['subjects'])}",
        f"Файлов: {count_files(data)}",
        f"Д/з: {len(data.get('homework') or [])}",
        f"Публичный адрес: {escape(base or 'нет')}",
    ]
    if not base:
        lines.append("Пока адреса нет, файл приходит сообщением. Для кнопки «открыть в приложении» включи Public Networking.")
    await message.answer("\n".join(lines))


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    if not is_owner(user_id_of(message)):
        return
    await message.answer("Отправь это файлом, без сжатия: скрепка → Файл. Тогда его можно открыть в другом приложении.")


@router.message(F.document)
async def handle_document(message: Message) -> None:
    if not is_owner(user_id_of(message)) or not message.document:
        return
    user_id = message.from_user.id
    file_name = message.document.file_name or "document"
    if file_name.lower() == "data.json":
        if message.document.file_size and message.document.file_size > 2_000_000:
            await message.answer("Слишком большой файл для базы.")
            return
        buffer = BytesIO()
        await message.bot.download(message.document, destination=buffer)
        raw = buffer.getvalue()
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("subjects"), list):
                raise ValueError
        except Exception:
            await message.answer("Это не база бота.")
            return
        clear_pending(user_id)
        pending_restore[user_id] = raw.decode("utf-8")
        files = count_files(parsed)
        text = (
            "Заменить текущую базу этим файлом?\n"
            f"Предметов: {len(parsed['subjects'])}, файлов: {files}, "
            f"Д/з: {len(parsed.get('homework') or [])}."
        )
        await message.answer(
            text,
            reply_markup=kb(
                [[Btn(text="✅ Заменить", callback_data="restore:yes"), Btn(text="❌ Отмена", callback_data="restore:no")]]
            ),
        )
        return
    clear_pending(user_id)
    pending_uploads[user_id] = {
        "file_id": message.document.file_id,
        "file_name": file_name,
        "mime": message.document.mime_type,
    }
    data = load()
    subjects = data.get("subjects") or []
    if not subjects:
        await message.answer("Нет предметов.")
        return
    rows = [[Btn(text=subject["name"], callback_data=f"addsel:s:{index}")] for index, subject in enumerate(subjects)]
    rows.append([Btn(text="❌ Отмена", callback_data="addsel:cancel")])
    await message.answer(
        f"📎 Файл <b>{escape(file_name)}</b> получен.\nВыбери предмет:",
        reply_markup=kb(rows),
    )


@router.message(F.text.func(lambda text: bool(text) and not text.startswith("/")))
async def handle_text_input(message: Message) -> None:
    user_id = user_id_of(message)
    text = (message.text or "").strip()
    if not text:
        return
    if user_id is not None and is_owner(user_id) and user_id in pending_events:
        pending_events[user_id] = text
        start = get_moscow_now().date()
        rows = date_buttons([start + timedelta(days=offset) for offset in range(28)], "evday", 4)
        rows.append([Btn(text="❌ Отмена", callback_data="evday:cancel")])
        await message.answer("Выбери день:", reply_markup=kb(rows))
        return
    if user_id is not None and is_owner(user_id) and user_id in pending_homework:
        pending_homework[user_id]["text"] = text
        data = load()
        rows = [[Btn(text=subject["name"], callback_data=f"hwsel:s:{index}")] for index, subject in enumerate(data.get("subjects") or [])]
        rows.append([Btn(text="❌ Отмена", callback_data="hwsel:cancel")])
        await message.answer("Выбери предмет:", reply_markup=kb(rows))
        return
    await screen_home(message)


@router.callback_query(F.data == "addhw")
async def start_add_homework(callback: CallbackQuery) -> None:
    if not is_owner(user_id_of(callback)):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.from_user.id
    clear_pending(user_id)
    pending_homework[user_id] = {}
    await callback.message.answer("Напиши текст домашнего задания:")
    await callback.answer()


@router.callback_query(F.data == "addevent")
async def start_add_event(callback: CallbackQuery) -> None:
    if not is_owner(user_id_of(callback)):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.from_user.id
    clear_pending(user_id)
    pending_events[user_id] = ""
    await callback.message.answer("Напиши текст события:")
    await callback.answer()


@router.callback_query(F.data.startswith("restore:"))
async def handle_restore(callback: CallbackQuery) -> None:
    if not is_owner(user_id_of(callback)):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.from_user.id
    if callback.data == "restore:no":
        pending_restore.pop(user_id, None)
        await callback.message.edit_text("Отменено.")
        await callback.answer()
        return
    raw = pending_restore.pop(user_id, None)
    if not raw:
        await callback.answer("Сессия истекла.", show_alert=True)
        return
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("subjects"), list):
            raise ValueError
    except Exception:
        await callback.message.edit_text("Файл не похож на data.json.")
        await callback.answer()
        return
    data = normalize(parsed)
    save(data)
    await callback.message.edit_text(
        f"База заменена. Предметов: {len(data['subjects'])}, файлов: {count_files(data)}, Д/з: {len(data['homework'])}.",
        reply_markup=kb([nav()]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hwsel:"))
async def handle_hw_selection(callback: CallbackQuery) -> None:
    if not is_owner(user_id_of(callback)):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.from_user.id
    if user_id not in pending_homework:
        await callback.answer("Сессия истекла.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 2 or parts[1] == "cancel":
        pending_homework.pop(user_id, None)
        await callback.message.edit_text("Отменено.")
        await callback.answer()
        return
    if parts[1] != "s" or len(parts) < 3:
        await callback.answer()
        return
    try:
        si = int(parts[2])
        subject_name = load()["subjects"][si]["name"]
    except (IndexError, KeyError, TypeError, ValueError):
        pending_homework.pop(user_id, None)
        await callback.message.edit_text("Предмет не найден.")
        await callback.answer()
        return
    pending_homework[user_id]["si"] = si
    dates = upcoming_lesson_dates(subject_name, get_moscow_now().date(), 60)[:24]
    if not dates:
        pending_homework.pop(user_id, None)
        await callback.message.edit_text("Не нашёл дней с этим предметом в расписании.")
        await callback.answer()
        return
    rows = date_buttons(dates, "hwday", 4)
    rows.append([Btn(text="❌ Отмена", callback_data="hwsel:cancel")])
    await callback.message.edit_text(
        f"Предмет: <b>{escape(subject_name)}</b>\nВыбери день, когда есть пара:",
        reply_markup=kb(rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hwday:"))
async def handle_hw_day(callback: CallbackQuery) -> None:
    if not is_owner(user_id_of(callback)):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.from_user.id
    if user_id not in pending_homework:
        await callback.answer("Сессия истекла.", show_alert=True)
        return
    picked = parse_picked_date((callback.data or "").split(":", 1)[1])
    if picked is None:
        pending_homework.pop(user_id, None)
        await callback.message.edit_text("Не понял дату.")
        await callback.answer()
        return
    info = pending_homework.pop(user_id)
    data = load()
    si = info.get("si")
    try:
        subject_name = data["subjects"][si]["name"]
    except (IndexError, KeyError, TypeError):
        await callback.message.edit_text("Предмет не найден.")
        await callback.answer()
        return
    data.setdefault("homework", []).append(
        {
            "id": next_hw_id(data["homework"]),
            "subject": subject_name,
            "subject_idx": si,
            "text": info.get("text"),
            "day": picked.day,
            "month": picked.month,
            "year": picked.year,
            "date": picked.isoformat(),
            "done": False,
            "created": get_moscow_now().isoformat(),
        }
    )
    save(data)
    await callback.message.edit_text(
        f"✅ Д/з до {fmt_short(picked)}:\n{escape(info.get('text') or '')}",
        reply_markup=kb([[Btn(text="📚 К предмету", callback_data=f"s:{si}")], nav()]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hwdone:"))
async def mark_homework_done(callback: CallbackQuery) -> None:
    if not is_owner(user_id_of(callback)):
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        hw_id = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Не найдено", show_alert=True)
        return
    data = load()
    subject_index = None
    for item in data.get("homework") or []:
        if item.get("id") == hw_id:
            item["done"] = True
            subject_index = item.get("subject_idx")
            break
    save(data)
    await callback.answer("Отмечено")
    if subject_index is not None:
        await screen_homework(callback, subject_index)


@router.callback_query(F.data.startswith("hwview:"))
async def view_homework(callback: CallbackQuery) -> None:
    try:
        await screen_homework(callback, int((callback.data or "").split(":")[1]))
    except (IndexError, ValueError):
        await callback.answer("Не найдено", show_alert=True)


@router.callback_query(F.data.startswith("evday:"))
async def handle_event_day(callback: CallbackQuery) -> None:
    if not is_owner(user_id_of(callback)):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.from_user.id
    if user_id not in pending_events or not pending_events[user_id]:
        await callback.answer("Сессия истекла.", show_alert=True)
        return
    raw = (callback.data or "").split(":", 1)[1]
    if raw == "cancel":
        pending_events.pop(user_id, None)
        await callback.message.edit_text("Отменено.")
        await callback.answer()
        return
    picked = parse_picked_date(raw)
    if picked is None:
        await callback.answer("Не понял дату", show_alert=True)
        return
    text = pending_events.pop(user_id)
    data = load()
    data.setdefault("events", []).append(
        {
            "day": picked.day,
            "month": picked.month,
            "year": picked.year,
            "date": picked.isoformat(),
            "text": text,
            "created": get_moscow_now().isoformat(),
        }
    )
    save(data)
    await callback.message.edit_text(
        f"✅ Событие на {fmt_short(picked)}:\n{escape(text)}",
        reply_markup=kb([nav()]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("addsel:"))
async def handle_add_selection(callback: CallbackQuery) -> None:
    if not is_owner(user_id_of(callback)):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = callback.from_user.id
    if user_id not in pending_uploads:
        await callback.answer("Сессия истекла.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 2 or parts[1] == "cancel":
        pending_uploads.pop(user_id, None)
        await callback.message.edit_text("Отменено.")
        await callback.answer()
        return
    if parts[1] == "s" and len(parts) >= 3:
        try:
            si = int(parts[2])
            subject = load()["subjects"][si]
        except (IndexError, KeyError, TypeError, ValueError):
            await callback.answer("Предмет не найден", show_alert=True)
            return
        pending_uploads[user_id]["si"] = si
        rows = [
            [Btn(text=section["title"], callback_data=f"addsel:sec:{si}:{index}")]
            for index, section in enumerate(subject.get("sections") or [])
        ]
        rows.append([Btn(text="❌ Отмена", callback_data="addsel:cancel")])
        await callback.message.edit_text(
            f"Предмет: <b>{escape(subject['name'])}</b>\nВыбери раздел:",
            reply_markup=kb(rows),
        )
        await callback.answer()
        return
    if parts[1] == "sec" and len(parts) >= 4:
        try:
            si = int(parts[2])
            section_index = int(parts[3])
            info = pending_uploads.pop(user_id)
            data = load()
            section = data["subjects"][si]["sections"][section_index]
        except (IndexError, KeyError, TypeError, ValueError):
            pending_uploads.pop(user_id, None)
            await callback.message.edit_text("Ошибка.")
            await callback.answer()
            return
        section.setdefault("files", []).append(
            {"file_id": info["file_id"], "name": info["file_name"], "mime": info.get("mime")}
        )
        save(data)
        await callback.message.edit_text(
            f"✅ Файл добавлен: <b>{escape(info['file_name'])}</b>",
            reply_markup=kb([[Btn(text="📄 Открыть раздел", callback_data=f"s:{si}:{section_index}")], nav()]),
        )
        await callback.answer()
        return
    await callback.answer()


@router.callback_query()
async def on_callback(callback: CallbackQuery) -> None:
    data = callback.data or ""
    if data.startswith(("addsel:", "evday:", "hwsel:", "hwday:", "hwdone:", "hwview:", "restore:")) or data in {
        "addevent",
        "addhw",
    }:
        return
    parts = data.split(":")
    try:
        match parts:
            case ["home"]:
                await screen_home(callback)
            case ["subs"]:
                await screen_subjects(callback)
            case ["s", si]:
                await screen_subject(callback, int(si))
            case ["s", si, j]:
                await screen_section(callback, int(si), int(j))
            case ["p", pi]:
                await screen_page(callback, int(pi))
            case ["sched", "today"]:
                await screen_schedule(callback, get_moscow_now().date())
            case ["sched", "tomorrow"]:
                await screen_schedule(callback, get_moscow_now().date() + timedelta(days=1))
            case ["sched", "week"]:
                await screen_week(callback, get_moscow_now().date())
            case ["sched", iso]:
                await screen_schedule(callback, date.fromisoformat(iso))
            case ["week", iso]:
                await screen_week(callback, date.fromisoformat(iso))
            case ["file", si, j, fi]:
                if public_base():
                    await callback.answer("Открой раздел ещё раз и нажми на файл.", show_alert=True)
                    return
                try:
                    meta = load()["subjects"][int(si)]["sections"][int(j)]["files"][int(fi)]
                    file_id, _ = file_meta(meta)
                    await callback.message.answer_document(file_id)
                    await callback.answer()
                except Exception:
                    await callback.answer("Не удалось отправить", show_alert=True)
            case _:
                await callback.answer("Меню устарело", show_alert=True)
                await screen_home(callback)
    except Exception:
        logging.exception("callback failed")
        await callback.answer("Ошибка", show_alert=True)
        await screen_home(callback)


async def health_root(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def send_file(request: web.Request) -> web.Response:
    parsed = parse_token(request.match_info.get("token", ""))
    if not parsed:
        return web.Response(status=404, text="Ссылка устарела. Открой раздел в боте ещё раз.")
    try:
        si, j, fi = int(parsed["si"]), int(parsed["j"]), int(parsed["fi"])
    except (KeyError, TypeError, ValueError):
        return web.Response(status=404, text="Ссылка битая.")
    try:
        meta = load()["subjects"][si]["sections"][j]["files"][fi]
    except (KeyError, IndexError, TypeError):
        return web.Response(status=404, text="Файл не найден.")
    file_id, name = file_meta(meta)
    if not file_id or file_id != parsed.get("id"):
        return web.Response(status=404, text="Ссылка устарела. Открой раздел в боте ещё раз.")
    bot: Bot = request.app["bot"]
    http: ClientSession = request.app["http"]
    try:
        tg_file = await bot.get_file(file_id)
    except Exception:
        logging.error("get_file failed")
        return web.Response(status=413, text="Telegram не отдал файл. Если он больше 20 МБ, сожми его и загрузи снова.")
    if not tg_file.file_path or not TOKEN:
        return web.Response(status=502, text="Не удалось получить файл.")
    url = f"https://api.telegram.org/file/bot{TOKEN}/{quote(tg_file.file_path, safe='/')}"
    try:
        async with http.get(url) as response:
            if response.status != 200:
                return web.Response(status=502, text="Не удалось скачать файл.")
            body = await response.read()
            remote_mime = response.headers.get("Content-Type", "").split(";")[0].strip()
    except Exception:
        logging.error("file download failed")
        return web.Response(status=502, text="Не удалось скачать файл.")
    stored = meta.get("mime") if isinstance(meta, dict) else None
    return web.Response(
        body=body,
        headers={
            "Content-Type": guess_mime(name, stored or remote_mime),
            "Content-Disposition": content_disposition(name),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not TOKEN:
        raise SystemExit("Задай BOT_TOKEN")
    path = data_path()
    data = load()
    today = get_moscow_now().date()
    logging.info(
        "data=%s subjects=%s files=%s week=%s public=%s",
        path,
        len(data["subjects"]),
        count_files(data),
        week_label(week_type(today)),
        public_base() or "-",
    )
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    http = ClientSession(timeout=ClientTimeout(total=120))
    app = web.Application()
    app["bot"] = bot
    app["http"] = http
    app.router.add_get("/", health_root)
    app.router.add_get("/health", health_root)
    app.router.add_get("/dl/{token}", send_file)
    app.router.add_get("/dl/{token}/{name}", send_file)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT") or "8080")
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info("listening on %s", port)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await http.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
