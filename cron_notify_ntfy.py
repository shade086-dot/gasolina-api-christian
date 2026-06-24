#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

API_BASE_URL = os.getenv("GASOLINA_API_URL", "https://gasolina-api-christian.onrender.com").rstrip("/")
NTFY_SERVER_URL = os.getenv("NTFY_SERVER_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "gasolina-christian-8f3k29x")
MODE = os.getenv("GASOLINA_MODE", "").strip()
CITY = os.getenv("GASOLINA_CITY", "").strip()
ORIGIN = os.getenv("GASOLINA_ORIGIN", "").strip()
DESTINATION = os.getenv("GASOLINA_DESTINATION", "").strip()
SEGMENT = os.getenv("GASOLINA_SEGMENT", "auto").strip() or "auto"


def get_json(url: str, timeout: int = 300) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_ntfy(title: str, message: str, click_url: str | None = None) -> str:
    url = f"{NTFY_SERVER_URL}/{urllib.parse.quote(NTFY_TOPIC)}"
    headers = {
        "Title": title.encode("ascii", "ignore").decode("ascii")[:80] or "Gasolina",
        "Priority": os.getenv("NTFY_PRIORITY", "default"),
        "Tags": os.getenv("NTFY_TAGS", "fuel_pump,motorcycle"),
    }
    if click_url:
        headers["Click"] = click_url

    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def build_recommend_url() -> str:
    params = {}
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


def pick_link(data: dict, label_contains: str) -> str | None:
    for link in data.get("links", []) or []:
        label = str(link.get("label", "")).lower()
        if label_contains.lower() in label:
            return link.get("url")
    map_data = data.get("map") or {}
    if label_contains.lower() == "mapa":
        return map_data.get("visual_map_url")
    return None


def build_message(data: dict) -> tuple[str, str, str | None]:
    rec = data.get("recommended") or {}
    decision = data.get("decision") or {}
    weather = data.get("weather_summary") or {}
    current_weather = weather.get("current") or {}

    station = rec.get("station_name", "Sin estación")
    price = rec.get("price", "?")
    address = rec.get("address", "")
    municipality = rec.get("municipality", "")
    updated = rec.get("official_timestamp", "")
    trust = rec.get("trust_note", "")
    reason = decision.get("reason", "")

    segment = data.get("segment", "")
    title = f"Gasolina {price} eur/l"

    weather_result = current_weather.get("result", "")
    weather_bullets = current_weather.get("bullets", []) or []
    weather_lines = "\n".join(f"- {b}" for b in weather_bullets[:4])

    alternatives = []
    for alt in (data.get("alternatives") or [])[:3]:
        alternatives.append(
            f"- {alt.get('station_name','?')}: {alt.get('price','?')} €/l · {alt.get('municipality','')}"
        )
    alternatives_text = "\n".join(alternatives)

    map_url = pick_link(data, "mapa")
    apple_url = pick_link(data, "apple")
    google_url = pick_link(data, "google")

    parts = [
        f"⛽ {station}",
        f"SP95: {price} €/l",
        f"{address} · {municipality}".strip(" ·"),
    ]
    if updated:
        parts.append(f"Actualizado: {updated}")
    if trust:
        parts.append(trust)
    if reason:
        parts.append(f"\nDecisión: {reason}")
    if weather_result or weather_lines:
        parts.append(f"\nTiempo: {weather_result}")
        if weather_lines:
            parts.append(weather_lines)
    if alternatives_text:
        parts.append(f"\nAlternativas:\n{alternatives_text}")
    if map_url:
        parts.append(f"\nMapa: {map_url}")
    if apple_url:
        parts.append(f"Apple Maps: {apple_url}")
    if google_url:
        parts.append(f"Google Maps: {google_url}")

    return title, "\n".join(parts), map_url


def main() -> int:
    try:
        # Warm up API.
        try:
            urllib.request.urlopen(f"{API_BASE_URL}/health", timeout=120).read()
        except Exception:
            pass

        data = get_json(build_recommend_url(), timeout=300)
        if data.get("status") != "ok":
            post_ntfy("Gasolina error", json.dumps(data, ensure_ascii=False)[:3000])
            return 2

        title, message, click_url = build_message(data)
        result = post_ntfy(title, message, click_url)
        print(result)
        return 0

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP ERROR {e.code}: {body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
