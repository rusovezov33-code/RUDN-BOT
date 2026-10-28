"""Хранилище data.json: кэш в памяти, атомарная запись, нормализация один раз.

Формат обратно совместим: читаются любые старые файлы, новые поля только
необязательные (см. README, раздел «Формат данных»).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)
TOP_LEVEL = ("pages", "subjects", "events", "homework")


def empty_data() -> dict[str, Any]:
    return {key: [] for key in TOP_LEVEL}


def section_template() -> list[dict[str, Any]]:
    return copy.deepcopy(list(config.subjects().section_template))


def blank_subject(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "teacher": "Не указано",
        "semester": 1,
        "contacts": "Не указано",
        "description": "",
        "sections": section_template(),
    }


def common_urls() -> set[str]:
    return {str(link.get("url") or "") for link in config.subjects().common_links}


def subject_has_content(subject: dict, homework: list[dict], index: int) -> bool:
    commons = common_urls()
    for section in subject.get("sections") or []:
        if not isinstance(section, dict):
            continue
        if section.get("files") or str(section.get("text") or "").strip():
            return True
        for link in section.get("links") or []:
            url = str((link or {}).get("url") or "")
            if url and url not in commons and "esystem.rudn.ru" not in url:
                return True
    name = subject.get("name")
    return any(item.get("subject") == name or item.get("subject_idx") == index for item in homework)


def _fix_ids(homework: list[dict]) -> None:
    """Каждому заданию — уникальный целый id (старые записи могли быть без id)."""
    used: set[int] = set()
    missing: list[dict] = []
    for item in homework:
        value = item.get("id")
        if isinstance(value, int) and not isinstance(value, bool) and value not in used:
            used.add(value)
        else:
            missing.append(item)
    next_id = max(used) + 1 if used else 0
    for item in missing:
        item["id"] = next_id
        used.add(next_id)
        next_id += 1


def normalize(data: Any) -> dict[str, Any]:
    cfg = config.subjects()
    if not isinstance(data, dict):
        data = {}
    for key in TOP_LEVEL:
        if not isinstance(data.get(key), list):
            data[key] = []
    data["pages"] = [item for item in data["pages"] if isinstance(item, dict)]
    data["events"] = [item for item in data["events"] if isinstance(item, dict)]
    subjects = [item for item in data["subjects"] if isinstance(item, dict)]
    homework = [item for item in data["homework"] if isinstance(item, dict)]
    data["homework"] = homework

    for item in homework:
        if item.get("subject"):
            continue
        index = item.get("subject_idx")
        if isinstance(index, int) and 0 <= index < len(subjects):
            item["subject"] = subjects[index].get("name")

    for subject in subjects:
        renamed = cfg.renames.get(subject.get("name", ""))
        if renamed:
            subject["name"] = renamed
        if subject.get("description") in cfg.legacy_descriptions:
            subject["description"] = ""
        if not isinstance(subject.get("sections"), list) or not subject["sections"]:
            subject["sections"] = section_template()
    for item in homework:
        renamed = cfg.renames.get(item.get("subject", ""))
        if renamed:
            item["subject"] = renamed

    kept = [
        subject
        for index, subject in enumerate(subjects)
        if not (subject.get("name") in cfg.drop_if_empty and not subject_has_content(subject, homework, index))
    ]
    existing: dict[str, dict] = {}
    extras: list[dict] = []
    for subject in kept:
        name = subject.get("name") or "Без названия"
        subject["name"] = name
        if name in cfg.canonical and name not in existing:
            existing[name] = subject
        else:
            extras.append(subject)
    ordered = [existing.get(name) or blank_subject(name) for name in cfg.canonical]
    ordered.extend(extras)
    data["subjects"] = ordered
    index_by_name = {subject["name"]: index for index, subject in enumerate(ordered)}
    for item in homework:
        name = item.get("subject")
        if name in index_by_name:
            item["subject_idx"] = index_by_name[name]
    _fix_ids(homework)
    return data


def encode(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


_WRITE_LOCK = threading.Lock()


def atomic_write(path: Path, text: str) -> None:
    with _WRITE_LOCK:  # запись бывает и из потока (save_async) — по одной за раз
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)


class Store:
    """Единственный источник данных. Читает файл один раз, пишет атомарно."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._data: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return self._path or config.data_path()

    def _ensure_file(self) -> None:
        path = self.path
        if path.exists():
            return
        bundled = config.BUNDLED_DATA
        if bundled.exists() and bundled.resolve() != path.resolve():
            atomic_write(path, bundled.read_text(encoding="utf-8"))
        else:
            atomic_write(path, encode(empty_data()))

    def _read_raw(self) -> Any:
        path = self.path
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("не удалось прочитать %s", path)
            # Не затираем испорченный файл: откладываем копию и берём базу из репозитория.
            try:
                shutil.copy2(path, path.with_name(f"{path.name}.corrupt-{int(time.time())}"))
            except Exception:
                log.exception("не удалось сохранить копию испорченного файла")
            try:
                return json.loads(config.BUNDLED_DATA.read_text(encoding="utf-8"))
            except Exception:
                return empty_data()

    def load(self, force: bool = False) -> dict[str, Any]:
        if self._data is not None and not force:
            return self._data
        self._ensure_file()
        raw = self._read_raw()
        data = normalize(raw)
        encoded = encode(data)
        try:
            current = self.path.read_text(encoding="utf-8")
        except Exception:
            current = ""
        if current != encoded:
            atomic_write(self.path, encoded)
            log.info("данные приведены в порядок: %s", self.path)
        self._data = data
        return data

    @property
    def data(self) -> dict[str, Any]:
        return self.load()

    def save(self) -> None:
        if self._data is None:
            return
        atomic_write(self.path, encode(self._data))

    async def save_async(self) -> None:
        """Как save(), но запись с fsync — в отдельном потоке, не блокируя бота.
        Снимок данных берётся сразу (в потоке цикла), поэтому порядок сохранений верный."""
        if self._data is None:
            return
        text = encode(self._data)
        lock = self._async_lock()
        async with lock:
            await asyncio.to_thread(atomic_write, self.path, text)

    def _async_lock(self) -> asyncio.Lock:
        if getattr(self, "_alock", None) is None:
            self._alock = asyncio.Lock()
        return self._alock

    def replace(self, raw: Any) -> dict[str, Any]:
        self._data = normalize(raw)
        self.save()
        return self._data


store = Store()
