#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE_URL = os.getenv("GASOLINA_API_URL", "https://gasolina-api-christian.onrender.com").rstrip("/")
MODE = os.getenv("GASOLINA_MODE", "").strip()
CITY = os.getenv("GASOLINA_CITY", "").strip()
ORIGIN = os.getenv("GASOLINA_ORIGIN", "").strip()
DESTINATION = os.getenv("GASOLINA_DESTINATION", "").strip()
SEGMENT = os.getenv("GASOLINA_SEGMENT", "auto").strip() or "auto"

PUSHOVER_USER = os.getenv("PUSHOVER_USER", os.getenv("PUSHOVER_USER_KEY", "")).strip()
PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", os.getenv("PUSHOVER_API_TOKEN", "")).strip()
PUSHOVER_ENABLED = os.getenv("PUSHOVER_ENABLED", "true").lower() not in {"0", "false", "no"}
PUSHOVER_DEVICE = os.getenv("PUSHOVER_DEVICE", "").strip()
PUSHOVER_SOUND = os.getenv("PUSHOVER_SOUND", "").strip()
PUSHOVER_PRIORITY = os.getenv("PUSHOVER_PRIORITY", "0").strip() or "0"
PUSHOVER_URL_TITLE = os.getenv("PUSHOVER_URL_TITLE", "Abrir mapa visual").strip() or "Abrir mapa visual"


def get_json(url: str, timeout: int = 300) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def build_recommend_url() -> str:
    params: dict[str, str] = {}
    if MODE == "city":
        params["mode"] = "city"
        params["city"] = CITY
    elif MODE == "route":
        params["mode"] = "route"
        params["origin"] = ORIGIN
        params["destination"] = DESTINATION
    else:
        params["segment"] = SEGMENT
    return f"{API_BASE_URL}/recommend?{urllib.parse.urlencode(params)}"


def pick_link(data: dict[str, Any], label_contains: str) -> str | None:
    needle = label_contains.lower()
    for link in data.get("links", []) or []:
        if needle in str(link.get("label", "")).lower():
            return link.get("url")
    map_data = data.get("map") or {}
    if needle == "mapa":
        return map_data.get("visual_map_url")
    if needle == "apple":
        return map_data.get("apple_maps_recommended_route") or map_data.get("apple_maps_route")
    if needle == "google":
        return map_data.get("google_maps_recommended_route")
    return None


def compact_route_title(data: dict[str, Any]) -> str:
    map_data = data.get("map") or {}
    origin = (map_data.get("origin") or {}).get("name") or ""
    destination = (map_data.get("destination") or {}).get("name") or ""
    if origin and destination and origin != destination:
        return f"{origin} → {destination}"
    weather = data.get("weather_summary") or {}
    current = weather.get("current") or {}
    wt = str(current.get("title") or "").strip()
    if wt:
        return wt
    names = {
        "cabanillas_out": "Ida Anchuelo → DSV/Cabanillas",
        "cabanillas_return": "Vuelta DSV/Cabanillas → Anchuelo",
        "forus_out": "Ida a Forus",
        "forus_return": "Vuelta Forus → Anchuelo",
        "auto": "Ruta automática",
    }
    return names.get(str(data.get("segment") or SEGMENT), "Informe gasolina")


def shorten_title(value: str, max_len: int = 80) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= max_len else value[: max_len - 1].rstrip() + "…"


def build_message(data: dict[str, Any]) -> tuple[str, str, str | None]:
    rec = data.get("recommended") or {}
    decision = data.get("decision") or {}
    weather = data.get("weather_summary") or {}
    current_weather = weather.get("current") or {}

    station = rec.get("station_name", "Sin estación")
    price = rec.get("price", "?")
    address = rec.get("address", "")
    municipality = rec.get("municipality", "")
    updated = rec.get("official_timestamp", "")
    reason = decision.get("reason", "")
    route_title = compact_route_title(data)
    title = shorten_title(f"{route_title} · SP95 {price} €/l", 80)

    weather_result = current_weather.get("result", "")
    weather_bullets = current_weather.get("bullets", []) or []
    weather_lines = "\n".join(f"- {b}" for b in weather_bullets[:4])

    alternatives = []
    for alt in (data.get("alternatives") or [])[:3]:
        alternatives.append(f"- {alt.get('station_name','?')}: {alt.get('price','?')} €/l · {alt.get('municipality','')}")
    alternatives_text = "\n".join(alternatives)

    map_url = pick_link(data, "mapa")

    parts = [f"⛽ {station}", f"SP95: {price} €/l", f"{address} · {municipality}".strip(" ·")]
    if updated:
        parts.append(f"Actualizado: {updated}")
    if reason:
        parts.append(f"\nDecisión: {reason}")
    if weather_result or weather_lines:
        parts.append(f"\nTiempo: {weather_result}")
        if weather_lines:
            parts.append(weather_lines)
    if alternatives_text:
        parts.append(f"\nAlternativas:\n{alternatives_text}")
    if map_url:
        parts.append("\nEnlaces: toca la notificación para abrir el mapa visual.")
    return title, "\n".join(parts), map_url


def trim_message(message: str, limit: int = 1000) -> str:
    text = str(message or "").strip()
    return text if len(text) <= limit else text[: limit - 2].rstrip() + "…"


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
        "priority": PUSHOVER_PRIORITY,
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
            urllib.request.urlopen(f"{API_BASE_URL}/health", timeout=120).read()
        except Exception:
            pass
        data = get_json(build_recommend_url(), timeout=300)
        if data.get("status") != "ok":
            post_pushover("Gasolina error", json.dumps(data, ensure_ascii=False)[:1000])
            return 2
        title, message, map_url = build_message(data)
        print(post_pushover(title, message, click_url=map_url))
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
