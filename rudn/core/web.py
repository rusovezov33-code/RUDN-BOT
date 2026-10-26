"""HTTP: health-check для Railway и выдача файлов по подписанной ссылке."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import ClientSession, web

from . import config
from .storage import store

log = logging.getLogger(__name__)

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


def file_meta(meta: Any) -> tuple[str, str]:
    if isinstance(meta, dict):
        return str(meta.get("file_id") or ""), str(meta.get("name") or "файл")
    return str(meta or ""), "файл"


def make_token(si: int, j: int, fi: int, file_id: str) -> str:
    payload = json.dumps(
        {"si": si, "j": j, "fi": fi, "id": file_id, "exp": int(time.time()) + 30 * 24 * 3600},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    blob = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(config.link_secret().encode(), payload, hashlib.sha256).hexdigest()
    return f"{blob}.{signature}"


def parse_token(token: str) -> dict | None:
    if not token or len(token) > 4000 or "." not in token:
        return None
    blob, signature = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    except Exception:
        return None
    expected = hmac.new(config.link_secret().encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        parsed = json.loads(payload)
    except Exception:
        return None
    if not isinstance(parsed, dict) or int(parsed.get("exp", 0)) < time.time():
        return None
    return parsed


def file_link(si: int, j: int, fi: int, meta: Any) -> str | None:
    base = config.public_base()
    file_id, name = file_meta(meta)
    if not base or not file_id:
        return None
    url = f"{base}/dl/{make_token(si, j, fi, file_id)}/{quote(name.replace('/', '_'))}"
    return url if len(url) <= 2000 else None


def guess_mime(name: str, stored: str | None) -> str:
    if stored and stored != "application/octet-stream":
        return stored
    return EXT_MIME.get(Path(name).suffix.lower(), "application/octet-stream")


def content_disposition(name: str) -> str:
    clean = name.replace("\\", "").replace('"', "").replace("\r", "").replace("\n", "") or "file"
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in clean) or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean)}"


async def health_root(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def send_file(request: web.Request) -> web.Response:
    parsed = parse_token(request.match_info.get("token", ""))
    if not parsed:
        return web.Response(status=404, text="Ссылка устарела. Открой раздел в боте ещё раз.")
    try:
        si, j, fi = int(parsed["si"]), int(parsed["j"]), int(parsed["fi"])
        meta = store.data["subjects"][si]["sections"][j]["files"][fi]
    except (KeyError, IndexError, TypeError, ValueError):
        return web.Response(status=404, text="Файл не найден.")
    file_id, name = file_meta(meta)
    if not file_id or file_id != parsed.get("id"):
        return web.Response(status=404, text="Ссылка устарела. Открой раздел в боте ещё раз.")
    bot = request.app["bot"]
    http: ClientSession = request.app["http"]
    try:
        tg_file = await bot.get_file(file_id)
    except Exception:
        log.error("get_file failed")
        return web.Response(status=413, text="Telegram не отдал файл. Если он больше 20 МБ, сожми его и загрузи снова.")
    if not tg_file.file_path or not config.token():
        return web.Response(status=502, text="Не удалось получить файл.")
    url = f"https://api.telegram.org/file/bot{config.token()}/{quote(tg_file.file_path, safe='/')}"
    try:
        async with http.get(url) as response:
            if response.status != 200:
                return web.Response(status=502, text="Не удалось скачать файл.")
            body = await response.read()
            remote_mime = response.headers.get("Content-Type", "").split(";")[0].strip()
    except Exception:
        log.error("file download failed")
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


def build_app(bot: Any, http: ClientSession) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app["http"] = http
    app.router.add_get("/", health_root)
    app.router.add_get("/health", health_root)
    app.router.add_get("/dl/{token}", send_file)
    app.router.add_get("/dl/{token}/{name}", send_file)
    return app
