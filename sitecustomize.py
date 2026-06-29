from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

_original_urlopen = urllib.request.urlopen

PUSHOVER_USER = os.getenv("PUSHOVER_USER", os.getenv("PUSHOVER_USER_KEY", "")).strip()
PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", os.getenv("PUSHOVER_API_TOKEN", "")).strip()
PUSHOVER_ENABLED = os.getenv("PUSHOVER_ENABLED", "true").lower() not in {"0", "false", "no"}
PUSHOVER_DEVICE = os.getenv("PUSHOVER_DEVICE", "").strip()
PUSHOVER_SOUND = os.getenv("PUSHOVER_SOUND", "").strip()
PUSHOVER_PRIORITY = os.getenv("PUSHOVER_PRIORITY", "0").strip() or "0"
NTFY_FALLBACK = os.getenv("NTFY_FALLBACK", "false").lower() in {"1", "true", "yes"}
NTFY_SERVER_URL = os.getenv("NTFY_SERVER_URL", "https://ntfy.sh").rstrip("/")


def _request_url(req) -> str:
    if isinstance(req, urllib.request.Request):
        return req.full_url
    return str(req)


def _is_ntfy_post(req) -> bool:
    if not (PUSHOVER_ENABLED and PUSHOVER_USER and PUSHOVER_TOKEN):
        return False
    url = _request_url(req)
    if not url.startswith(NTFY_SERVER_URL + "/"):
        return False
    if isinstance(req, urllib.request.Request):
        method = getattr(req, "get_method", lambda: "GET")()
        return method.upper() == "POST"
    return False


def _header(req: urllib.request.Request, name: str, default: str = "") -> str:
    return req.get_header(name, default) or default


def _priority(req: urllib.request.Request, message: str) -> str:
    if os.getenv("PUSHOVER_PRIORITY"):
        return PUSHOVER_PRIORITY
    ntfy_priority = str(_header(req, "Priority", "")).lower()
    if ntfy_priority in {"urgent", "high", "max", "4", "5"} or "🚨" in message or "TOCA" in message:
        return "1"
    return "0"


def _trim(message: str, limit: int = 1000) -> str:
    if len(message) <= limit:
        return message
    return message[: limit - 2].rstrip() + "…"


def _send_pushover_from_ntfy(req: urllib.request.Request):
    message = (req.data or b"").decode("utf-8", errors="replace")
    title = _header(req, "Title", "Gasolina") or "Gasolina"
    click = _header(req, "Click", "")
    payload = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": title[:250],
        "message": _trim(message),
        "priority": _priority(req, message),
    }
    if click:
        payload["url"] = click
        payload["url_title"] = "Abrir mapa visual"
    if PUSHOVER_DEVICE:
        payload["device"] = PUSHOVER_DEVICE
    if PUSHOVER_SOUND:
        payload["sound"] = PUSHOVER_SOUND
    pushover_req = urllib.request.Request(
        "https://api.pushover.net/1/messages.json",
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        method="POST",
    )
    return _original_urlopen(pushover_req, timeout=60)


def urlopen(req, *args, **kwargs):
    if _is_ntfy_post(req):
        try:
            return _send_pushover_from_ntfy(req)
        except Exception:
            if not NTFY_FALLBACK:
                raise
    return _original_urlopen(req, *args, **kwargs)


urllib.request.urlopen = urlopen
