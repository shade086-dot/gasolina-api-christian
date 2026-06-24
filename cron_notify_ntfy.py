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

# Si lo pones a false, solo usará el "click" principal al mapa visual.
NTFY_INCLUDE_ACTIONS = os.getenv("NTFY_INCLUDE_ACTIONS", "true").lower() not in {"0", "false", "no"}


def get_json(url: str, timeout: int = 300) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ascii_header(value: str, max_len: int = 200) -> str:
    """ntfy permite UTF-8 en el cuerpo, pero las cabeceras HTTP conviene dejarlas ASCII."""
    return value.encode("ascii", "ignore").decode("ascii")[:max_len]


def build_ntfy_actions(map_url: str | None, apple_url: str | None, google_url: str | None) -> str | None:
    """
    Crea botones en ntfy sin mostrar URLs largas en el texto.
    Si la cabecera queda demasiado larga, deja solo el mapa visual para evitar errores HTTP.
    """
    if not NTFY_INCLUDE_ACTIONS:
        return None

    actions = []
    if map_url:
        actions.append(f"view, Mapa visual, {map_url}, clear=true")
    if apple_url:
        actions.append(f"view, Apple Maps, {apple_url}")
    if google_url:
        actions.append(f"view, Google Maps, {google_url}")

    if not actions:
        return None

    full = "; ".join(actions)

    # Evita cabeceras enormes, sobre todo con rutas de Google con muchos waypoints.
    if len(full) > 3500 and map_url:
        return f"view, Mapa visual, {map_url}, clear=true"
    return full


def compact_route_title(data: dict) -> str:
    """Devuelve un título corto del trayecto/ciudad para la cabecera de ntfy."""
    # 1) Para modo viaje, el mapa suele traer origen/destino claros.
    map_data = data.get("map") or {}
    origin = (map_data.get("origin") or {}).get("name") or ""
    destination = (map_data.get("destination") or {}).get("name") or ""
    segment = str(data.get("segment") or "")

    if origin and destination:
        # En búsquedas por ciudad suele ser "Centro de X" -> "Zona X"; mejor simplificar.
        if segment.startswith("travel_city_"):
            city = destination.replace("Zona ", "").strip() or origin.replace("Centro de ", "").strip()
            if city:
                return f"Gasolina en {city}"
        if origin != destination:
            return f"{origin} → {destination}"

    # 2) El resumen del mapa suele tener "Origen → Destino · Recomendada: ..."
    summary = str(map_data.get("summary") or "")
    if "· Recomendada:" in summary:
        summary = summary.split("· Recomendada:", 1)[0].strip()
    if summary:
        # Acorta nombres muy largos para que el título no se corte demasiado.
        return summary

    # 3) Meteo suele traer títulos humanos: "Vuelta DSV/Cabanillas → Anchuelo", "Gasolina en Bilbao"...
    weather = data.get("weather_summary") or {}
    current = weather.get("current") or {}
    wt = str(current.get("title") or "").strip()
    if wt:
        return wt

    # 4) Fallback por segmento.
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

def post_ntfy(
    title: str,
    message: str,
    click_url: str | None = None,
    map_url: str | None = None,
    apple_url: str | None = None,
    google_url: str | None = None,
) -> str:
    url = f"{NTFY_SERVER_URL}/{urllib.parse.quote(NTFY_TOPIC)}"

    headers = {
        "Title": ascii_header(title, 80) or "Gasolina",
        "Priority": os.getenv("NTFY_PRIORITY", "default"),
        "Tags": os.getenv("NTFY_TAGS", "fuel_pump,motorcycle"),
    }

    # Al tocar la notificación se abre el mapa visual.
    if click_url:
        headers["Click"] = click_url

    actions = build_ntfy_actions(map_url, apple_url, google_url)
    if actions:
        headers["Actions"] = actions

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
    if label_contains.lower() == "apple":
        return map_data.get("apple_maps_recommended_route") or map_data.get("apple_maps_route")
    if label_contains.lower() == "google":
        return map_data.get("google_maps_recommended_route")
    return None


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

    # No mostramos URLs largas en el cuerpo. ntfy usará Click/Actions.
    if map_url:
        parts.append("\nEnlaces: toca la notificación para abrir el mapa visual.")
        if NTFY_INCLUDE_ACTIONS:
            parts.append("También deberían aparecer botones: Mapa visual, Apple Maps y Google Maps.")

    return title, "\n".join(parts), map_url, apple_url, google_url


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

        title, message, map_url, apple_url, google_url = build_message(data)
        result = post_ntfy(
            title,
            message,
            click_url=map_url,
            map_url=map_url,
            apple_url=apple_url,
            google_url=google_url,
        )
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
