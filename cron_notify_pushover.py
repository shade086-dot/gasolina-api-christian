#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Reutilizamos toda la lógica ya probada del informe: calendario, gasolina, tiempo,
# enlaces y bloque Mapit. Este script solo cambia el transporte de notificación.
import cron_notify_ntfy as report

PUSHOVER_USER = os.getenv("PUSHOVER_USER", os.getenv("PUSHOVER_USER_KEY", "")).strip()
PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", os.getenv("PUSHOVER_API_TOKEN", "")).strip()
PUSHOVER_ENABLED = os.getenv("PUSHOVER_ENABLED", "true").lower() not in {"0", "false", "no"}
PUSHOVER_DEVICE = os.getenv("PUSHOVER_DEVICE", "").strip()
PUSHOVER_SOUND = os.getenv("PUSHOVER_SOUND", "").strip()
PUSHOVER_PRIORITY = os.getenv("PUSHOVER_PRIORITY", "0").strip() or "0"
PUSHOVER_URL_TITLE = os.getenv("PUSHOVER_URL_TITLE", "Abrir mapa visual").strip() or "Abrir mapa visual"


def trim_message(message: str, limit: int = 1000) -> str:
    text = str(message or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 2].rstrip() + "…"


def pushover_priority(message: str) -> str:
    if os.getenv("PUSHOVER_PRIORITY"):
        return PUSHOVER_PRIORITY
    text = str(message or "")
    if "🚨" in text or "Mantenimiento pendiente" in text or "TOCA" in text:
        return "1"
    return "0"


def post_pushover(title: str, message: str, click_url: str | None = None) -> str:
    if not PUSHOVER_ENABLED:
        raise RuntimeError("Pushover está deshabilitado: PUSHOVER_ENABLED=false")
    if not PUSHOVER_USER or not PUSHOVER_TOKEN:
        raise RuntimeError("Faltan variables PUSHOVER_USER y/o PUSHOVER_TOKEN")

    payload = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": (title or "Gasolina")[:250],
        "message": trim_message(message),
        "priority": pushover_priority(message),
    }
    if click_url:
        payload["url"] = click_url
        payload["url_title"] = PUSHOVER_URL_TITLE
    if PUSHOVER_DEVICE:
        payload["device"] = PUSHOVER_DEVICE
    if PUSHOVER_SOUND:
        payload["sound"] = PUSHOVER_SOUND

    req = urllib.request.Request(
        "https://api.pushover.net/1/messages.json",
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def main() -> int:
    try:
        try:
            urllib.request.urlopen(f"{report.API_BASE_URL}/health", timeout=120).read()
        except Exception:
            pass

        data = report.get_json(report.build_recommend_url(), timeout=300)
        if data.get("status") != "ok":
            post_pushover("Gasolina error", json.dumps(data, ensure_ascii=False)[:1000])
            return 2

        title, message, map_url, _apple_url, _google_url = report.build_message(data)
        result = post_pushover(title, message, click_url=map_url)
        print(result)
        return 0

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"PUSHOVER HTTP ERROR {exc.code}: {body}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
