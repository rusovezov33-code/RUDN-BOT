"""📚 Уроки: загрузка и проверка файлов content/lessons/<предмет>/<id>.json,
нормализация и проверка ответов. Никакого ИИ и внешних запросов — только готовый текст.

Формат урока (подробно — README.md, раздел «Уроки»):
{"id": "lat-01", "subject": "Латинский язык", "order": 1, "title": "…",
 "sources": ["…, с. 10–11"], "target": {"key": "<ключ события tuis>", "label": "КР", "date": "2026-11-03"},
 "blocks": [{"type": "explain"|"example", "title": "…", "text": "…"},
            {"type": "choice", "q": "…", "options": ["…"], "correct": 0, "explain": "…"},
            {"type": "text", "q": "…", "accept": ["…"], "explain": "…"},
            {"type": "think", "q": "…", "answer": "…", "accept": ["…"]}]}
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONTENT_DIR = Path(__file__).resolve().parents[1] / "content" / "lessons"
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,15}$")
INFO_TYPES = ("explain", "example")
QUESTION_TYPES = ("choice", "text")
BLOCK_TYPES = INFO_TYPES + QUESTION_TYPES + ("think",)
ALLOWED_TAGS = ("b", "i", "u", "code")
MAX_BLOCK_CHARS = 2600  # текст блока; с шапкой и разбором ответа экран остаётся < 3900
MAX_OPTIONS = 6
MAX_ANSWER_CHARS = 300
MIN_QUESTIONS, MAX_QUESTIONS = 3, 6


class LessonError(ValueError):
    pass


@dataclass(frozen=True)
class Lesson:
    id: str
    subject: str
    order: int
    title: str
    sources: tuple[str, ...]
    target: dict | None
    blocks: tuple[dict, ...]

    @property
    def questions(self) -> list[int]:
        """Номера блоков-вопросов (без «задачи на подумать»)."""
        return [i for i, block in enumerate(self.blocks) if block["type"] in QUESTION_TYPES]

    def question_number(self, index: int) -> int:
        return self.questions.index(index) + 1 if index in self.questions else 0


# ---------------------------------------------------------------- проверка файлов
_TAG_RE = re.compile(r"<(/?)([a-zA-Z]+)\s*>")
_ENTITY_RE = re.compile(r"&(amp|lt|gt|quot);")


def check_html(text: str) -> str | None:
    """None — разметка годится для Telegram (только b/i/u/code, теги парные)."""
    stack: list[str] = []
    pos = 0
    for match in _TAG_RE.finditer(text):
        if "<" in text[pos:match.start()] or ">" in text[pos:match.start()]:
            return "лишний символ < или >"
        pos = match.end()
        closing, tag = match.group(1), match.group(2)
        if tag not in ALLOWED_TAGS:
            return f"тег <{tag}> не разрешён"
        if closing:
            if not stack or stack.pop() != tag:
                return f"непарный </{tag}>"
        else:
            stack.append(tag)
    if "<" in text[pos:] or ">" in text[pos:]:
        return "лишний символ < или >"
    if stack:
        return f"не закрыт <{stack[-1]}>"
    if "&" in _ENTITY_RE.sub("", text):
        return "символ & без экранирования"
    return None


def _str(raw: dict, key: str, errors: list[str], where: str, html: bool = False, required: bool = True) -> str:
    value = raw.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: нет текста «{key}»")
        return ""
    if html:
        problem = check_html(value)
        if problem:
            errors.append(f"{where}.{key}: {problem}")
    if len(value) > MAX_BLOCK_CHARS:
        errors.append(f"{where}.{key}: длиннее {MAX_BLOCK_CHARS} знаков")
    return value


def _accept(raw: dict, errors: list[str], where: str, required: bool) -> None:
    accept = raw.get("accept")
    if accept is None and not required:
        return
    if not isinstance(accept, list) or not accept or not all(isinstance(a, str) and normalize(a) for a in accept):
        errors.append(f"{where}: «accept» — непустой список ответов")


def validate(raw: Any, where: str = "урок") -> list[str]:
    """Список ошибок (пустой — урок корректен)."""
    if not isinstance(raw, dict):
        return [f"{where}: ожидался объект"]
    errors: list[str] = []
    lesson_id = raw.get("id")
    if not isinstance(lesson_id, str) or not ID_RE.match(lesson_id):
        errors.append(f"{where}: id должен быть вида lat-01 (a-z, 0-9, -, до 16 знаков)")
    _str(raw, "subject", errors, where)
    _str(raw, "title", errors, where)
    if not isinstance(raw.get("title"), str) or len(raw.get("title") or "") > 80:
        errors.append(f"{where}: title до 80 знаков")
    if not isinstance(raw.get("order"), int) or isinstance(raw.get("order"), bool):
        errors.append(f"{where}: order — целое число")
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources or not all(isinstance(s, str) and s.strip() for s in sources):
        errors.append(f"{where}: sources — непустой список источников (страницы пособия)")
    target = raw.get("target")
    if target is not None:
        if not isinstance(target, dict) or not isinstance(target.get("label"), str):
            errors.append(f"{where}: target — объект с label")
        elif target.get("date") is not None:
            try:
                date.fromisoformat(str(target["date"]))
            except ValueError:
                errors.append(f"{where}: target.date — YYYY-MM-DD")
    blocks = raw.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return errors + [f"{where}: нет блоков"]
    kinds = []
    for index, block in enumerate(blocks):
        at = f"{where}.blocks[{index}]"
        if not isinstance(block, dict) or block.get("type") not in BLOCK_TYPES:
            errors.append(f"{at}: type — один из {', '.join(BLOCK_TYPES)}")
            continue
        kind = block["type"]
        kinds.append(kind)
        if kind in INFO_TYPES:
            _str(block, "title", errors, at, required=False)
            _str(block, "text", errors, at, html=True)
        elif kind == "choice":
            _str(block, "q", errors, at, html=True)
            _str(block, "explain", errors, at, html=True)
            options = block.get("options")
            if not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS or not all(
                isinstance(o, str) and o.strip() and len(o) <= 120 for o in options
            ):
                errors.append(f"{at}: options — от 2 до {MAX_OPTIONS} строк (до 120 знаков)")
                options = []
            elif len({normalize(o) for o in options}) != len(options):
                errors.append(f"{at}: одинаковые варианты")
            correct = block.get("correct")
            if not isinstance(correct, int) or isinstance(correct, bool) or not 0 <= correct < max(len(options), 1):
                errors.append(f"{at}: correct — номер верного варианта (с 0)")
        elif kind == "text":
            _str(block, "q", errors, at, html=True)
            _str(block, "explain", errors, at, html=True)
            _accept(block, errors, at, required=True)
        else:  # think
            _str(block, "q", errors, at, html=True)
            _str(block, "answer", errors, at, html=True)
            _accept(block, errors, at, required=False)
    questions = sum(1 for k in kinds if k in QUESTION_TYPES)
    if "explain" not in kinds:
        errors.append(f"{where}: нужен хотя бы один блок explain")
    if "example" not in kinds:
        errors.append(f"{where}: нужен хотя бы один блок example")
    if not MIN_QUESTIONS <= questions <= MAX_QUESTIONS:
        errors.append(f"{where}: вопросов {questions}, нужно {MIN_QUESTIONS}–{MAX_QUESTIONS}")
    if kinds.count("think") != 1 or (kinds and kinds[-1] != "think"):
        errors.append(f"{where}: ровно одна задача think — последним блоком")
    return errors


def from_raw(raw: dict) -> Lesson:
    return Lesson(
        id=raw["id"],
        subject=raw["subject"].strip(),
        order=raw["order"],
        title=raw["title"].strip(),
        sources=tuple(raw["sources"]),
        target=dict(raw["target"]) if isinstance(raw.get("target"), dict) else None,
        blocks=tuple(dict(block) for block in raw["blocks"]),
    )


def load_dir(root: Path = CONTENT_DIR) -> tuple[list[Lesson], list[str]]:
    """Все уроки из папки + ошибки. Файл с ошибкой пропускается (бот не падает)."""
    lessons: list[Lesson] = []
    errors: list[str] = []
    seen: set[str] = set()
    for path in sorted(root.glob("*/*.json")) if root.is_dir() else []:
        where = f"{path.parent.name}/{path.name}"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            errors.append(f"{where}: не читается ({error})")
            continue
        problems = validate(raw, where)
        if not problems and raw["id"] != path.stem:
            problems.append(f"{where}: id «{raw['id']}» не совпадает с именем файла")
        if not problems and raw["id"] in seen:
            problems.append(f"{where}: повтор id «{raw['id']}»")
        if problems:
            errors.extend(problems)
            continue
        seen.add(raw["id"])
        lessons.append(from_raw(raw))
    lessons.sort(key=lambda l: (l.subject, l.order, l.id))
    return lessons, errors


_cache: tuple[list[Lesson], list[str]] | None = None


def load(reload: bool = False) -> tuple[list[Lesson], list[str]]:
    global _cache
    if _cache is None or reload:
        _cache = load_dir()
    return _cache


def all_lessons() -> list[Lesson]:
    return load()[0]


def get(lesson_id: str) -> Lesson | None:
    return next((l for l in all_lessons() if l.id == lesson_id), None)


# ---------------------------------------------------------------- проверка ответов
_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)
# кириллица, похожая на латиницу (набрал «nervus» в русской раскладке частично)
_HOMOGLYPHS = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x",
                             "у": "y", "к": "k", "м": "m", "т": "t", "і": "i", "н": "h", "в": "b"})


def normalize(text: str) -> str:
    """Регистр, ё/е, ударения, знаки препинания и лишние пробелы не важны."""
    text = (text or "").lower().replace("ё", "е").replace("й", "и")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(_PUNCT_RE.sub(" ", text).split())


def _latin(text: str) -> bool:
    return bool(text) and all(ch.isascii() for ch in text)


def _distance(a: str, b: str, limit: int = 1) -> int:
    """Расстояние Левенштейна (с ранним выходом, если больше limit)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    typo: bool = False  # русский ответ с опечаткой в одну букву — засчитан
    near: bool = False  # латынь: одна буква не та — НЕ засчитано (в латыни это окончание/форма)
    empty: bool = False  # после очистки ничего не осталось (эмодзи, точки)
    too_long: bool = False


def match(given: str, accept: list[str] | tuple[str, ...]) -> Verdict:
    if len(given or "") > MAX_ANSWER_CHARS:
        return Verdict(False, too_long=True)
    norm = normalize(given)
    if not norm:
        return Verdict(False, empty=True)
    typo = near = False
    for variant in accept:
        expected = normalize(variant)
        latin = _latin(expected)
        candidate = norm.translate(_HOMOGLYPHS) if latin else norm
        if candidate == expected:
            return Verdict(True)
        if _distance(candidate, expected) <= 1:
            if latin:
                near = True
            elif len(expected) >= 8:
                typo = True
    if typo:
        return Verdict(True, typo=True)
    return Verdict(False, near=near)


def check_choice(block: dict, given: str) -> tuple[Verdict, int | None]:
    """Ответ текстом на вопрос с вариантами: номер варианта или его текст."""
    if len(given or "") > MAX_ANSWER_CHARS:
        return Verdict(False, too_long=True), None
    norm = normalize(given)
    if not norm:
        return Verdict(False, empty=True), None
    options = block["options"]
    if norm.isdigit() and 1 <= int(norm) <= len(options) and not any(normalize(o) == norm for o in options):
        picked = int(norm) - 1
    else:
        picked = next((i for i, o in enumerate(options) if normalize(o) == norm), None)
        if picked is None:
            return Verdict(False), None
    return Verdict(picked == block["correct"]), picked


def correct_text(block: dict) -> str:
    if block["type"] == "choice":
        return block["options"][block["correct"]]
    if block["type"] == "text":
        return block["accept"][0]
    return ""
