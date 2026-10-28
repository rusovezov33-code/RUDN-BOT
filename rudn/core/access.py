"""Кто пользуется ботом: владелец (OWNER_ID), тестеры (только просмотр) и все
остальные (бот им ничего не показывает, кроме запроса доступа по /start).

Тестеры и запросы лежат в отдельном файле access.json рядом с data.json —
данные владельца (data.json) от действий тестера не меняются ни на байт.
Там же — свой прогресс уроков каждого тестера.

Правило для тестера — белый список `TESTER_READ`: префикс кнопки → какие
действия можно. Всё, чего нет в списке (новые фичи тоже), запрещено.
Плюс страховка: у не-владельца `ctx.store` — песочница (копия данных),
её save() пишет только access.json.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

from . import config
from .storage import atomic_write, store as owner_store

log = logging.getLogger(__name__)

OWNER = "owner"
TESTER = "tester"
TESTER_DENIED = "👀 Режим тестера: изменения недоступны"
REQUEST_SENT = "📨 Запрос на доступ отправлен владельцу"
REQUEST_REPEAT_SECONDS = 60  # повторный ответ «запрос уже отправлен» — не чаще
DENY_COOLDOWN_SECONDS = 24 * 3600  # после отказа новый запрос — через сутки
MAX_OPEN_REQUESTS = 10  # больше открытых запросов от чужих — молча игнорируем

_ANY: Callable[[list[str]], bool] = lambda args: True


def _not(*actions: str) -> Callable[[list[str]], bool]:
    return lambda args: (args[0] if args else "") not in actions


def _only(*actions: str) -> Callable[[list[str]], bool]:
    return lambda args: (args[0] if args else "") in actions


# префикс callback_data → можно ли тестеру это действие (по аргументам)
TESTER_READ: dict[str, Callable[[list[str]], bool]] = {
    "home": _ANY,
    "x": _ANY,  # отмена ввода (своего)
    "d": _ANY,  # день расписания
    "w": _ANY,  # неделя
    "dl": _not("ok", "un", "rm"),  # дедлайны: смотреть, но не отмечать/удалять
    "ex": _not("ok", "un", "rm"),  # экзамены: то же
    "subs": _ANY,  # список предметов
    "s": _ANY,  # предмет / раздел
    "file": _ANY,  # открыть файл (книга, задание)
    "sc": _only("", "c"),  # баллы: только просмотр
    "p": _ANY,  # страницы (МФЦ и т.п.)
    "ls": _ANY,  # уроки — у тестера свой прогресс
    # запрещено целиком: it (правка задания), add, adm, up, rs, evd, acc
    "it": lambda args: False,
    "add": lambda args: False,
    "adm": lambda args: False,
    "up": lambda args: False,
    "rs": lambda args: False,
    "evd": lambda args: False,
    "acc": lambda args: False,
}
TESTER_COMMANDS = {"start", "menu", "cancel", "today", "tomorrow", "week", "deadlines", "exams", "subjects"}
TESTER_TEXT_STATES = {"ls_answer"}  # ответы в уроке — в свой прогресс


def tester_may(prefix: str, args: list[str]) -> bool:
    rule = TESTER_READ.get(prefix)
    return bool(rule and rule(args))


# ---------------------------------------------------------------- файл доступа
def _empty() -> dict[str, Any]:
    return {"testers": {}, "requests": {}, "denied": {}, "revoked": {}, "progress": {}}


class AccessStore:
    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None
        self._path: Path | None = None

    @property
    def path(self) -> Path:
        return config.data_path().with_name("access.json")

    def load(self) -> dict[str, Any]:
        path = self.path
        if self._data is not None and self._path == path:
            return self._data
        data = _empty()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for key in data:
                        if isinstance(raw.get(key), dict):
                            data[key] = raw[key]
            except Exception:
                log.exception("не удалось прочитать %s", path)
        self._data, self._path = data, path
        return data

    @property
    def data(self) -> dict[str, Any]:
        return self.load()

    def save(self) -> None:
        if self._data is None:
            return
        atomic_write(self.path, json.dumps(self._data, ensure_ascii=False, indent=1))

    def reset(self) -> None:
        self._data = None
        self._path = None

    def progress(self, user_id: int) -> dict:
        table = self.data["progress"]
        entry = table.get(str(user_id))
        if not isinstance(entry, dict):
            entry = table[str(user_id)] = {}
        return entry


ACCESS = AccessStore()


def preallow() -> tuple[set[int], set[str]]:
    """config/testers.json: {"ids": [...], "usernames": [...]} — пускать без запроса."""
    try:
        raw = json.loads((config.CONFIG_DIR / "testers.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set(), set()
    except Exception:
        log.exception("config/testers.json")
        return set(), set()
    ids = {int(x) for x in raw.get("ids") or [] if str(x).lstrip("-").isdigit()}
    names = {str(x).lstrip("@").lower() for x in raw.get("usernames") or [] if str(x).strip()}
    return ids, names


def role(user_id: int | None, username: str | None = None) -> str | None:
    if user_id is None:
        return None
    owner = config.owner_id()
    if owner is not None and user_id == owner:
        return OWNER
    data = ACCESS.data
    key = str(user_id)
    if key in data["testers"]:
        return TESTER
    if key in data["revoked"]:
        return None
    ids, names = preallow()
    if user_id in ids or (username and username.lstrip("@").lower() in names):
        return TESTER
    return None


def display_name(user: Any) -> str:
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    name = f"{first} {last}".strip()
    return name or (getattr(user, "username", None) or "") or str(getattr(user, "id", "?"))


def user_info(user: Any) -> dict:
    return {
        "name": display_name(user)[:64],
        "username": getattr(user, "username", None) or None,
    }


# ---------------------------------------------------------------- песочница
class SandboxStore:
    """Хранилище для не-владельца: копия данных владельца; уроки — свой прогресс
    из access.json. save() пишет только access.json (а у чужих — ничего)."""

    def __init__(self, user_id: int | None, persist: bool) -> None:
        self.user_id = user_id
        self.persist = persist and user_id is not None
        self._data: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return owner_store.path

    def load(self, force: bool = False) -> dict[str, Any]:
        if self._data is None or force:
            data = copy.deepcopy(owner_store.data)
            data["lessons"] = ACCESS.progress(self.user_id) if self.persist else {}
            self._data = data
        return self._data

    @property
    def data(self) -> dict[str, Any]:
        return self.load()

    def save(self) -> None:
        if self.persist:
            ACCESS.save()

    async def save_async(self) -> None:
        self.save()

    def replace(self, raw: Any) -> dict[str, Any]:
        raise PermissionError("песочница тестера: замена базы запрещена")


# ---------------------------------------------------------------- запросы
def open_request(user: Any) -> str:
    """Записать запрос. Возвращает: "new" — отправить владельцу,
    "repeat" — напомнить «уже отправлен», "quiet" — молча игнорировать."""
    data = ACCESS.data
    key = str(user.id)
    now = time.time()
    denied = data["denied"].get(key)
    if isinstance(denied, dict) and now - float(denied.get("ts") or 0) < DENY_COOLDOWN_SECONDS:
        return "quiet"
    existing = data["requests"].get(key)
    if isinstance(existing, dict):
        if now - float(existing.get("replied") or 0) >= REQUEST_REPEAT_SECONDS:
            existing["replied"] = now
            ACCESS.save()
            return "repeat"
        return "quiet"
    if len(data["requests"]) >= MAX_OPEN_REQUESTS:
        log.warning("too many access requests, ignoring %s", key)
        return "quiet"
    data["requests"][key] = {**user_info(user), "ts": now, "replied": now}
    data["denied"].pop(key, None)
    ACCESS.save()
    return "new"


def approve(user_id: int) -> dict:
    data = ACCESS.data
    key = str(user_id)
    info = data["requests"].pop(key, None) or data["testers"].get(key) or {}
    data["testers"][key] = {
        "name": info.get("name") or key,
        "username": info.get("username"),
        "added": time.strftime("%Y-%m-%d %H:%M"),
    }
    data["revoked"].pop(key, None)
    data["denied"].pop(key, None)
    ACCESS.save()
    return data["testers"][key]


def deny(user_id: int) -> dict:
    data = ACCESS.data
    key = str(user_id)
    info = data["requests"].pop(key, None) or {}
    data["denied"][key] = {"name": info.get("name"), "ts": time.time()}
    ACCESS.save()
    return info


def revoke(user_id: int) -> dict:
    data = ACCESS.data
    key = str(user_id)
    info = data["testers"].pop(key, None) or {}
    data["revoked"][key] = {"name": info.get("name"), "ts": time.time()}
    ACCESS.save()
    return info
