#!/usr/bin/env python3
import base64
import json
import os
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

API_BASE_URL = os.getenv("GASOLINA_API_URL", "https://gasolina-api-christian.onrender.com").rstrip("/")
NTFY_SERVER_URL = os.getenv("NTFY_SERVER_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "gasolina-christian-8f3k29x")
MODE = os.getenv("GASOLINA_MODE", "").strip()
CITY = os.getenv("GASOLINA_CITY", "").strip()
ORIGIN = os.getenv("GASOLINA_ORIGIN", "").strip()
DESTINATION = os.getenv("GASOLINA_DESTINATION", "").strip()
SEGMENT = os.getenv("GASOLINA_SEGMENT", "auto").strip() or "auto"

NTFY_INCLUDE_ACTIONS = os.getenv("NTFY_INCLUDE_ACTIONS", "true").lower() not in {"0", "false", "no"}
NTFY_ICON_URL = os.getenv("NTFY_ICON_URL", "").strip()

MAPIT_STATUS_ENABLED = os.getenv("MAPIT_STATUS_ENABLED", "true").lower() not in {"0", "false", "no"}
MAPIT_GITHUB_REPO = os.getenv("MAPIT_GITHUB_REPO", "shade086-dot/mapit-pdf-mantenimiento").strip()
MAPIT_GITHUB_BRANCH = os.getenv("MAPIT_GITHUB_BRANCH", "main").strip() or "main"
MAPIT_GITHUB_DB_PATH = os.getenv("MAPIT_GITHUB_DB_PATH", "data/moto_maintenance.db").strip()
MAPIT_GITHUB_TOKEN = os.getenv("MAPIT_GITHUB_TOKEN", os.getenv("GITHUB_TOKEN", "")).strip()
MAPIT_ALWAYS_SHOW = os.getenv("MAPIT_ALWAYS_SHOW", "false").lower() in {"1", "true", "yes"}
MAPIT_SHOW_EVERY = int(os.getenv("MAPIT_SHOW_EVERY", "4") or "4")
MAPIT_COUNTER_FILE = os.getenv("MAPIT_COUNTER_FILE", "/tmp/gasolina_mapit_counter.txt")

CHAIN_GREASE_INTERVAL_KM = float(os.getenv("CHAIN_GREASE_INTERVAL_KM", "1000"))
CHAIN_CLEAN_INTERVAL_KM = float(os.getenv("CHAIN_CLEAN_INTERVAL_KM", "2000"))
WHEELS_INTERVAL_KM = float(os.getenv("WHEELS_INTERVAL_KM", "4000"))
REVISION_INTERVAL_KM = float(os.getenv("REVISION_INTERVAL_KM", os.getenv("OIL_INTERVAL_KM", "120000")))


def get_json(url: str, timeout: int = 300) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ascii_header(value: str, max_len: int = 200) -> str:
    return value.encode("ascii", "ignore").decode("ascii")[:max_len]


def build_ntfy_actions(map_url: str | None, apple_url: str | None, google_url: str | None) -> str | None:
    if not NTFY_INCLUDE_ACTIONS:
        return None
    actions = []
    if map_url:
        actions.append(f"view, 1 Mapa visual, {map_url}, clear=true")
    if apple_url:
        actions.append(f"view, 2 Apple Maps, {apple_url}")
    if google_url:
        actions.append(f"view, 3 Google Maps, {google_url}")
    if not actions:
        return None
    full = "; ".join(actions)
    if len(full) > 3500 and map_url:
        return f"view, 1 Mapa visual, {map_url}, clear=true"
    return full


def compact_route_title(data: dict) -> str:
    map_data = data.get("map") or {}
    origin = (map_data.get("origin") or {}).get("name") or ""
    destination = (map_data.get("destination") or {}).get("name") or ""
    segment = str(data.get("segment") or "")
    if origin and destination:
        if segment.startswith("travel_city_"):
            city = destination.replace("Zona ", "").strip() or origin.replace("Centro de ", "").strip()
            if city:
                return f"Gasolina en {city}"
        if origin != destination:
            return f"{origin} → {destination}"
    summary = str(map_data.get("summary") or "")
    if "· Recomendada:" in summary:
        summary = summary.split("· Recomendada:", 1)[0].strip()
    if summary:
        return summary
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
        "alcala": "Trayecto Alcalá",
        "auto": "Ruta automática",
    }
    return names.get(segment, segment or "Informe gasolina")


def shorten_title(value: str, max_len: int = 80) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "…"


def post_ntfy(title: str, message: str, click_url: str | None = None, map_url: str | None = None, apple_url: str | None = None, google_url: str | None = None) -> str:
    url = f"{NTFY_SERVER_URL}/{urllib.parse.quote(NTFY_TOPIC)}"
    headers = {
        "Title": ascii_header(title, 80) or "Gasolina",
        "Priority": os.getenv("NTFY_PRIORITY", "default"),
        "Tags": os.getenv("NTFY_TAGS", "fuel_pump,motorcycle"),
    }
    if NTFY_ICON_URL:
        headers["Icon"] = NTFY_ICON_URL
    if click_url:
        headers["Click"] = click_url
    actions = build_ntfy_actions(map_url, apple_url, google_url)
    if actions:
        headers["Actions"] = actions
    req = urllib.request.Request(url, data=message.encode("utf-8"), headers=headers, method="POST")
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
    if label_contains.lower() == "apple":
        return map_data.get("apple_maps_recommended_route") or map_data.get("apple_maps_route")
    if label_contains.lower() == "google":
        return map_data.get("google_maps_recommended_route")
    return None


def mapit_should_show(level: str) -> bool:
    if MAPIT_ALWAYS_SHOW or level in {"due", "soon"}:
        return True
    if MAPIT_SHOW_EVERY <= 0:
        return False
    try:
        current = 0
        if os.path.exists(MAPIT_COUNTER_FILE):
            current = int((open(MAPIT_COUNTER_FILE, "r", encoding="utf-8").read() or "0").strip() or "0")
        current += 1
        with open(MAPIT_COUNTER_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(current))
        return current % MAPIT_SHOW_EVERY == 0
    except Exception:
        return False


def github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if MAPIT_GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {MAPIT_GITHUB_TOKEN}"
    return headers


def download_mapit_db() -> bytes | None:
    if not MAPIT_STATUS_ENABLED or not MAPIT_GITHUB_REPO or not MAPIT_GITHUB_DB_PATH:
        return None
    url = f"https://api.github.com/repos/{MAPIT_GITHUB_REPO}/contents/{MAPIT_GITHUB_DB_PATH}?{urllib.parse.urlencode({'ref': MAPIT_GITHUB_BRANCH})}"
    req = urllib.request.Request(url, headers=github_headers())
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return base64.b64decode(payload.get("content") or "")


def total_trip_km(con: sqlite3.Connection) -> float:
    return float(con.execute("SELECT COALESCE(SUM(distance_km), 0) FROM trips").fetchone()[0] or 0.0)


def km_offset(con: sqlite3.Connection) -> float:
    try:
        row = con.execute("SELECT value FROM reminder_state WHERE key = 'km_offset'").fetchone()
        return float(row[0] or 0) if row else 0.0
    except Exception:
        return 0.0


def count_trips(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM trips").fetchone()[0] or 0)


def km_since_event(con: sqlite3.Connection, event_type: str) -> float:
    current = total_trip_km(con)
    row = con.execute("SELECT trip_total_km FROM maintenance_events WHERE event_type = ? ORDER BY event_at DESC, id DESC LIMIT 1", (event_type,)).fetchone()
    if not row:
        return current
    return max(0.0, current - float(row[0] or 0.0))


def last_report_days(con: sqlite3.Connection) -> int | None:
    row = con.execute("SELECT MAX(imported_at) FROM trips").fetchone()
    value = row[0] if row else None
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days)
    except Exception:
        return None


def counter_status(km_done: float, interval: float) -> dict:
    remaining = max(0.0, interval - km_done)
    due = remaining <= 0
    soon = (not due) and remaining <= interval * 0.15
    level = "due" if due else "soon" if soon else "ok"
    return {"km": km_done, "interval": interval, "remaining": remaining, "due": due, "soon": soon, "level": level}


def visual_bar(counter: dict, width: int = 10) -> str:
    interval = float(counter.get("interval") or 1)
    done = min(float(counter.get("km") or 0), interval)
    used = max(0, min(width, round((done / interval) * width)))
    return "█" * used + "░" * (width - used)


def build_mapit_status() -> dict | None:
    try:
        db_bytes = download_mapit_db()
        if not db_bytes:
            return None
        with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
            tmp.write(db_bytes)
            tmp.flush()
            con = sqlite3.connect(tmp.name)
            raw_total = total_trip_km(con)
            offset = km_offset(con)
            trips = count_trips(con)
            chain = counter_status(km_since_event(con, "engrase_cadena"), CHAIN_GREASE_INTERVAL_KM)
            clean = counter_status(km_since_event(con, "limpieza_cadena"), CHAIN_CLEAN_INTERVAL_KM)
            wheels = counter_status(km_since_event(con, "ruedas"), WHEELS_INTERVAL_KM)
            revision = counter_status(km_since_event(con, "aceite"), REVISION_INTERVAL_KM)
            report_days = last_report_days(con)
            con.close()
        levels = [chain["level"], clean["level"], wheels["level"], revision["level"]]
        alert_level = "due" if "due" in levels else "soon" if "soon" in levels else "ok"
        return {"ok": True, "km_totales": raw_total + offset, "km_mapit": raw_total, "km_ajuste": offset, "trayectos_guardados": trips, "alert_level": alert_level, "cadena": chain, "limpieza": clean, "ruedas": wheels, "revision": revision, "last_report_days": report_days}
    except Exception as exc:
        print(f"Aviso: no pude leer estado Mapit: {exc}", file=sys.stderr)
        return None


def build_mapit_block(status: dict | None) -> str:
    if not status:
        return ""
    level = status.get("alert_level", "ok")
    if not mapit_should_show(level):
        return ""
    cadena = status["cadena"]
    limpieza = status["limpieza"]
    ruedas = status["ruedas"]
    revision = status["revision"]
    report_days = status.get("last_report_days")
    lines = ["🏍️ Moto", f"Km reales estimados: {status.get('km_totales', 0):.1f} km"]
    if abs(float(status.get("km_ajuste") or 0)) >= 0.001:
        lines.append(f"Km Mapit: {status.get('km_mapit', 0):.1f} km · ajuste {status.get('km_ajuste', 0):+.1f} km")
    lines.extend([
        f"⛓️ Cadena:   {visual_bar(cadena)} {cadena['km']:.0f}/{cadena['interval']:.0f} km · quedan {cadena['remaining']:.0f}",
        f"🧽 Limpieza: {visual_bar(limpieza)} {limpieza['km']:.0f}/{limpieza['interval']:.0f} km · quedan {limpieza['remaining']:.0f}",
        f"🛞 Ruedas:   {visual_bar(ruedas)} {ruedas['km']:.0f}/{ruedas['interval']:.0f} km · quedan {ruedas['remaining']:.0f}",
        f"🔧 Revisión: {visual_bar(revision)} {revision['km']:.0f}/{revision['interval']:.0f} km · quedan {revision['remaining']:.0f}",
    ])
    if report_days is not None:
        lines.append(f"Último informe Mapit: hace {report_days} días")
    if level == "due":
        lines.append("🚨 Mantenimiento pendiente. Si ya lo hiciste: mapit actualizar / mapit revision")
    elif level == "soon":
        lines.append("🔶 Mantenimiento próximo. Revísalo antes de una ruta larga.")
    else:
        lines.append("✅ Mantenimiento OK")
    return "\n".join(lines)


def build_message(data: dict) -> tuple[str, str, str | None, str | None, str | None]:
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
    apple_url = pick_link(data, "apple")
    google_url = pick_link(data, "google")
    parts = [f"⛽ {station}", f"SP95: {price} €/l", f"{address} · {municipality}".strip(" ·")]
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
    mapit_block = build_mapit_block(build_mapit_status())
    if mapit_block:
        parts.append("\n" + mapit_block)
    if map_url:
        parts.append("\nEnlaces: toca la notificación para abrir el mapa visual.")
        if NTFY_INCLUDE_ACTIONS:
            parts.append("También deberían aparecer botones: 1 Mapa visual, 2 Apple Maps y 3 Google Maps.")
    return title, "\n".join(parts), map_url, apple_url, google_url


def main() -> int:
    try:
        try:
            urllib.request.urlopen(f"{API_BASE_URL}/health", timeout=120).read()
        except Exception:
            pass
        data = get_json(build_recommend_url(), timeout=300)
        if data.get("status") != "ok":
            post_ntfy("Gasolina error", json.dumps(data, ensure_ascii=False)[:3000])
            return 2
        title, message, map_url, apple_url, google_url = build_message(data)
        result = post_ntfy(title, message, click_url=map_url, map_url=map_url, apple_url=apple_url, google_url=google_url)
        print(result)
        return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP ERROR {e.code}: {body}", file=sys.stderr)
        if e.code == 429:
            print("⚠️ ntfy ha limitado la notificación. El informe se generó, pero no marco el cron como fallido.", file=sys.stderr)
            return 0
        return 1
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
