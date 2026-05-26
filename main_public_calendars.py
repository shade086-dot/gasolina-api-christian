from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urlencode
import html

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "gasolina_history.sqlite3"

PRECIOIL_BASE_URL = "https://api.precioil.es"

# Zonas de búsqueda Precioil.
# Alcalá/Daganzo: útil para Forus -> Anchuelo y Anchuelo -> Forus.
PRECIOIL_ALCALA_LAT = 40.48198
PRECIOIL_ALCALA_LON = -3.36354

# Punto intermedio Cabanillas/Azuqueca: útil para DSV/Cabanillas -> Anchuelo.
PRECIOIL_CABANILLAS_AZUQUECA_LAT = 40.6000
PRECIOIL_CABANILLAS_AZUQUECA_LON = -3.2500

# Precioil usa radio en km, no en metros.
PRECIOIL_SEARCH_RADIUS_KM = 15

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://gasolina-api-christian.onrender.com").rstrip("/")

# Coordenadas aproximadas para pintar mapa visual orientativo.
# Las coordenadas exactas de las gasolineras vienen de Precioil.
ROUTE_ENDPOINTS = {
    "cabanillas_return": {
        "origin_name": "DSV / Cabanillas del Campo",
        "origin_lat": 40.6302,
        "origin_lon": -3.2357,
        "destination_name": "Anchuelo",
        "destination_lat": 40.4667,
        "destination_lon": -3.2687,
    },
    "forus_return": {
        "origin_name": "Forus Alcalá Forjas",
        "origin_lat": 40.4930,
        "origin_lon": -3.3790,
        "destination_name": "Anchuelo",
        "destination_lat": 40.4667,
        "destination_lon": -3.2687,
    },
    "forus_out": {
        "origin_name": "Anchuelo",
        "origin_lat": 40.4667,
        "origin_lon": -3.2687,
        "destination_name": "Forus Alcalá Forjas",
        "destination_lat": 40.4930,
        "destination_lon": -3.3790,
    },
    "alcala": {
        "origin_name": "Forus Alcalá Forjas",
        "origin_lat": 40.4930,
        "origin_lon": -3.3790,
        "destination_name": "Anchuelo",
        "destination_lat": 40.4667,
        "destination_lon": -3.2687,
    },
}

PRECIOIL_REGIONS = {
    "alcala_daganzo": {
        "latitud": PRECIOIL_ALCALA_LAT,
        "longitud": PRECIOIL_ALCALA_LON,
        "radio": PRECIOIL_SEARCH_RADIUS_KM,
    },
    "cabanillas_azuqueca": {
        "latitud": PRECIOIL_CABANILLAS_AZUQUECA_LAT,
        "longitud": PRECIOIL_CABANILLAS_AZUQUECA_LON,
        "radio": PRECIOIL_SEARCH_RADIUS_KM,
    },
}

app = FastAPI(title="Gasolina Christian API", version="1.2.0")


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def norm(value: Any) -> str:
    s = "" if value is None else str(value)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.upper().strip()


def parse_price(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


def get_field(row: dict[str, Any], *names: str) -> str:
    for n in names:
        if n in row and row[n]:
            return str(row[n])
    return ""


def first_present(row: dict[str, Any], candidates: list[str]) -> Any:
    for key in candidates:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def flatten_values(value: Any) -> list[Any]:
    values: list[Any] = []
    if isinstance(value, dict):
        for v in value.values():
            values.extend(flatten_values(v))
    elif isinstance(value, list):
        for item in value:
            values.extend(flatten_values(item))
    else:
        values.append(value)
    return values


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_key TEXT NOT NULL,
            station_name TEXT,
            address TEXT,
            municipality TEXT,
            price REAL,
            official_timestamp TEXT,
            fetched_at TEXT NOT NULL,
            raw_json TEXT NOT NULL
        )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_obs_key_time ON observations(station_key, fetched_at)")


@app.on_event("startup")
def startup() -> None:
    init_db()


async def fetch_official_data() -> dict[str, Any]:
    cfg = load_config()

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 gasolina-api-christian/1.0",
                "Accept": "application/json,text/plain,*/*",
            },
        ) as client:
            r = await client.get(cfg["official_endpoint"])
            r.raise_for_status()
            return r.json()

    except httpx.TimeoutException as e:
        raise HTTPException(
            status_code=504,
            detail=f"Timeout consultando fuente oficial de carburantes: {type(e).__name__}: {e}",
        )

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Fuente oficial devolvió error HTTP {e.response.status_code}",
        )

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar con la fuente oficial: {type(e).__name__}: {e}",
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error inesperado consultando fuente oficial: {type(e).__name__}: {e}",
        )


async def fetch_precioil_json(path: str, params: dict[str, Any] | None = None) -> Any:
    api_key = os.environ.get("PRECIOIL_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="Falta configurar PRECIOIL_API_KEY en Render.")

    url = f"{PRECIOIL_BASE_URL}{path}"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=15.0),
            follow_redirects=True,
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "gasolina-api-christian/1.0",
            },
        ) as client:
            r = await client.get(url, params=params or {})

            if r.status_code in (401, 403):
                r = await client.get(
                    url,
                    params=params or {},
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "application/json",
                        "User-Agent": "gasolina-api-christian/1.0",
                    },
                )

            r.raise_for_status()
            return r.json()

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Precioil devolvió HTTP {e.response.status_code}: {e.response.text[:300]}",
        )

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar con Precioil: {type(e).__name__}: {e}",
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error inesperado consultando Precioil: {type(e).__name__}: {e}",
        )


def station_matches(row: dict[str, Any], station_cfg: dict[str, Any]) -> bool:
    municipio = norm(get_field(row, "Municipio", "municipio", "localidad", "Localidad", "city", "poblacion"))
    rotulo = norm(get_field(row, "Rótulo", "Rotulo", "rotulo", "nombre", "Nombre", "marca", "Marca", "brand"))
    direccion = norm(get_field(row, "Dirección", "Direccion", "direccion", "address", "Address"))
    provincia = norm(get_field(row, "Provincia", "provincia"))

    wanted_municipality = norm(station_cfg.get("municipality", ""))
    if wanted_municipality and wanted_municipality not in municipio:
        if municipio:
            return False

    brand_terms = [norm(x) for x in station_cfg.get("brand_contains", [])]
    name_terms = [norm(x) for x in station_cfg.get("name_contains", [])]
    address_terms = [norm(x) for x in station_cfg.get("address_contains", [])]

    text_name = f"{rotulo} {direccion} {provincia} {municipio}"

    if brand_terms and not any(t in text_name for t in brand_terms):
        return False

    if name_terms and not any(t in text_name for t in name_terms):
        return False

    if address_terms and not any(t in direccion for t in address_terms):
        return False

    return True


def extract_relevant_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = load_config()
    fuel_field = cfg.get("fuel_field", "Precio Gasolina 95 E5")
    rows = payload.get("ListaEESSPrecio", [])
    official_timestamp = payload.get("Fecha", "")

    found: list[dict[str, Any]] = []
    for station_cfg in cfg["stations"]:
        matches = [r for r in rows if station_matches(r, station_cfg)]
        for r in matches:
            price = parse_price(r.get(fuel_field))
            found.append({
                "station_key": station_cfg["key"],
                "station_name": get_field(r, "Rótulo", "Rotulo"),
                "address": get_field(r, "Dirección", "Direccion"),
                "municipality": get_field(r, "Municipio"),
                "price": price,
                "official_timestamp": official_timestamp,
                "route_tags": station_cfg.get("route_tags", []),
                "trust_note": station_cfg.get("trust_note", ""),
                "source": "official",
                "raw": r,
            })
    return found


def precioil_station_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict):
        for key in ("estaciones", "stations", "data", "results", "items", "lista", "ListaEESSPrecio"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]

        if any(k in payload for k in ("nombre", "rotulo", "direccion", "precio", "precios")):
            return [payload]

    return []


def extract_price_95_from_precioil(row: dict[str, Any]) -> Optional[float]:
    direct_candidates = [
        "Precio Gasolina 95 E5",
        "precio_gasolina_95_e5",
        "gasolina_95_e5",
        "Gasolina95",
        "gasolina95",
        "gasolina_95",
        "SP95",
        "sp95",
        "precio95",
        "precio_95",
    ]
    direct = first_present(row, direct_candidates)
    price = parse_price(direct)
    if price is not None:
        return price

    precios = row.get("precios") or row.get("prices") or row.get("carburantes") or row.get("fuels")

    if isinstance(precios, dict):
        for k, v in precios.items():
            nk = norm(k)
            if "95" in nk and ("GASOLINA" in nk or "SP" in nk or "E5" in nk):
                price = parse_price(v)
                if price is not None:
                    return price

    if isinstance(precios, list):
        for item in precios:
            if not isinstance(item, dict):
                continue
            text = norm(" ".join(str(x) for x in flatten_values(item)))
            if "95" in text and ("GASOLINA" in text or "SP" in text or "E5" in text):
                for price_key in ("precio", "price", "importe", "valor"):
                    price = parse_price(item.get(price_key))
                    if price is not None:
                        return price

    return None


def extract_relevant_rows_precioil(payload: Any) -> list[dict[str, Any]]:
    cfg = load_config()
    items = precioil_station_items(payload)
    fetched_timestamp = datetime.now().isoformat(timespec="seconds")

    found: list[dict[str, Any]] = []
    for station_cfg in cfg["stations"]:
        matches = [r for r in items if station_matches(r, station_cfg)]
        for r in matches:
            price = extract_price_95_from_precioil(r)
            station_name = str(first_present(r, ["Rótulo", "Rotulo", "rotulo", "nombre", "Nombre", "marca", "Marca", "brand"]) or "")
            address = str(first_present(r, ["Dirección", "Direccion", "direccion", "address", "Address"]) or "")
            municipality = str(first_present(r, ["Municipio", "municipio", "localidad", "Localidad", "city", "poblacion"]) or "")
            updated_at = first_present(
                r,
                [
                    "lastUpdate",
                    "fecha_actualizacion",
                    "fechaActualizacion",
                    "updated_at",
                    "updatedAt",
                    "ultima_actualizacion",
                ],
            )

            found.append({
                "station_key": station_cfg["key"],
                "station_name": station_name,
                "address": address,
                "municipality": municipality,
                "price": price,
                "official_timestamp": str(updated_at or fetched_timestamp),
                "route_tags": station_cfg.get("route_tags", []),
                "trust_note": station_cfg.get("trust_note", ""),
                "source": "precioil",
                "raw": r,
            })

    return found


def precioil_regions_for_segment(segment: str = "all") -> list[str]:
    if segment == "cabanillas_return":
        return ["cabanillas_azuqueca"]
    if segment in ("forus_out", "forus_return", "alcala"):
        return ["alcala_daganzo"]
    return ["alcala_daganzo", "cabanillas_azuqueca"]


def dedupe_precioil_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []

    for item in items:
        station_id = first_present(item, ["idEstacion", "id", "station_id"])
        key = str(station_id) if station_id is not None else f"{norm(item.get('marca'))}|{norm(item.get('direccion'))}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped


async def fetch_precioil_relevant_rows(segment: str = "all") -> tuple[Any, list[dict[str, Any]]]:
    all_items: list[dict[str, Any]] = []
    region_errors: dict[str, str] = {}

    for region_name in precioil_regions_for_segment(segment):
        try:
            payload = await fetch_precioil_json(
                "/estaciones/radio",
                params=PRECIOIL_REGIONS[region_name],
            )
            for item in precioil_station_items(payload):
                item = dict(item)
                item["precioil_region"] = region_name
                all_items.append(item)
        except HTTPException as e:
            region_errors[region_name] = str(e.detail)

    all_items = dedupe_precioil_items(all_items)

    if not all_items and region_errors:
        raise HTTPException(
            status_code=502,
            detail=f"Precioil no devolvió datos en ninguna zona: {region_errors}",
        )

    rows = extract_relevant_rows_precioil(all_items)
    return {"items": all_items, "region_errors": region_errors}, rows


def save_observations(rows: list[dict[str, Any]]) -> None:
    fetched_at = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as con:
        for r in rows:
            con.execute("""
            INSERT INTO observations
            (station_key, station_name, address, municipality, price, official_timestamp, fetched_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["station_key"], r["station_name"], r["address"], r["municipality"],
                r["price"], r["official_timestamp"], fetched_at,
                json.dumps(r["raw"], ensure_ascii=False),
            ))


def choose_best(rows: list[dict[str, Any]], segment: str) -> dict[str, Any]:
    candidates = []
    for r in rows:
        tags = set(r.get("route_tags", []))
        if segment == "cabanillas_return":
            ok = "cabanillas_return" in tags or "cabanillas" in tags
        elif segment in ("forus_out", "forus_return", "alcala"):
            ok = "forus" in tags or "alcala" in tags
        else:
            ok = True

        if ok and r.get("price") is not None:
            candidates.append(r)

    if not candidates:
        return {"error": "No hay candidatos con precio para ese tramo.", "segment": segment}

    def score(r: dict[str, Any]) -> float:
        p = float(r["price"])
        key = r["station_key"]
        penalty = 0.0
        if key == "alcampo_dehesa":
            penalty += 0.025
        if key == "family_energy_azuqueca" and segment != "cabanillas_return":
            penalty += 0.10
        if key == "ballenoil_varsovia" and segment in ("forus_out", "forus_return", "alcala"):
            penalty -= 0.005
        return p + penalty

    best = sorted(candidates, key=score)[0]
    alternatives = sorted(candidates, key=score)[1:4]
    return {"segment": segment, "recommended": best, "alternatives": alternatives}


def station_lat_lon(row: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    lat = first_present(raw, ["latitud", "lat", "latitude"])
    lon = first_present(raw, ["longitud", "lon", "lng", "longitude"])
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def format_price_label(price: Any) -> str:
    if price is None:
        return "precio no disponible"
    try:
        return f"{float(price):.3f} €/l"
    except (TypeError, ValueError):
        return str(price)


def map_marker_from_station(row: dict[str, Any], role: str) -> dict[str, Any]:
    lat, lon = station_lat_lon(row)
    return {
        "role": role,
        "station_key": row.get("station_key"),
        "name": row.get("station_name") or row.get("station_key"),
        "address": row.get("address"),
        "municipality": row.get("municipality"),
        "price": row.get("price"),
        "price_label": format_price_label(row.get("price")),
        "updated_at": row.get("official_timestamp"),
        "source": row.get("source"),
        "lat": lat,
        "lon": lon,
        "google_maps_place": google_maps_place_link(row),
        "apple_maps_place": apple_maps_place_link(row),
    }


def coord_text(lat: float, lon: float) -> str:
    return f"{lat:.6f},{lon:.6f}"


def google_maps_place_link(row: dict[str, Any]) -> str:
    lat, lon = station_lat_lon(row)
    query = " ".join(str(x) for x in [row.get("station_name"), row.get("address"), row.get("municipality")] if x)
    if lat is not None and lon is not None:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}&query_place_id=" if query else f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"


def apple_maps_place_link(row: dict[str, Any]) -> str:
    lat, lon = station_lat_lon(row)
    label = " ".join(str(x) for x in [row.get("station_name"), row.get("address"), format_price_label(row.get("price"))] if x)
    if lat is not None and lon is not None:
        return f"https://maps.apple.com/?ll={lat},{lon}&q={quote_plus(label)}"
    return f"https://maps.apple.com/?q={quote_plus(label)}"


def build_google_directions(origin: dict[str, Any], destination: dict[str, Any], waypoints: list[dict[str, Any]]) -> str:
    origin_coord = coord_text(origin["origin_lat"], origin["origin_lon"])
    destination_coord = coord_text(destination["destination_lat"], destination["destination_lon"])
    waypoint_coords = []
    for station in waypoints:
        lat, lon = station_lat_lon(station)
        if lat is not None and lon is not None:
            waypoint_coords.append(coord_text(lat, lon))
    params = {
        "api": "1",
        "origin": origin_coord,
        "destination": destination_coord,
        "travelmode": "driving",
    }
    if waypoint_coords:
        params["waypoints"] = "|".join(waypoint_coords)
    return "https://www.google.com/maps/dir/?" + urlencode(params)


def build_apple_directions(origin: dict[str, Any], station: Optional[dict[str, Any]] = None) -> str:
    saddr = coord_text(origin["origin_lat"], origin["origin_lon"])
    if station:
        lat, lon = station_lat_lon(station)
        daddr = coord_text(lat, lon) if lat is not None and lon is not None else str(station.get("address") or station.get("station_name"))
    else:
        daddr = coord_text(origin["destination_lat"], origin["destination_lon"])
    return f"https://maps.apple.com/?saddr={quote_plus(saddr)}&daddr={quote_plus(daddr)}&dirflg=d"


def build_map_payload(segment: str, result: dict[str, Any]) -> dict[str, Any]:
    endpoint = ROUTE_ENDPOINTS.get(segment, ROUTE_ENDPOINTS["forus_return"])
    recommended = result.get("recommended") if isinstance(result.get("recommended"), dict) else None
    alternatives = [x for x in result.get("alternatives", []) if isinstance(x, dict)]
    stations = ([recommended] if recommended else []) + alternatives
    markers = [map_marker_from_station(station, "recommended" if idx == 0 else "alternative") for idx, station in enumerate(stations)]
    valid_stations = [station for station in stations if station_lat_lon(station)[0] is not None]
    return {
        "type": "visual_map_links",
        "note": "Mapa visual orientativo. Los precios se muestran en los marcadores/popup y en el informe; la ruta exacta puede variar según navegación/tráfico.",
        "visual_map_url": f"{PUBLIC_BASE_URL}/map?segment={quote_plus(segment)}",
        "google_maps_recommended_route": build_google_directions(endpoint, endpoint, valid_stations[:1]),
        "google_maps_all_candidates_route": build_google_directions(endpoint, endpoint, valid_stations),
        "apple_maps_recommended_station": build_apple_directions(endpoint, recommended) if recommended else build_apple_directions(endpoint),
        "origin": {
            "name": endpoint["origin_name"],
            "lat": endpoint["origin_lat"],
            "lon": endpoint["origin_lon"],
        },
        "destination": {
            "name": endpoint["destination_name"],
            "lat": endpoint["destination_lat"],
            "lon": endpoint["destination_lon"],
        },
        "markers": markers,
    }


def render_visual_map_html(segment: str, result: dict[str, Any], map_payload: dict[str, Any]) -> str:
    markers_json = json.dumps(map_payload.get("markers", []), ensure_ascii=False)
    origin_json = json.dumps(map_payload.get("origin", {}), ensure_ascii=False)
    destination_json = json.dumps(map_payload.get("destination", {}), ensure_ascii=False)
    recommended = result.get("recommended", {}) if isinstance(result.get("recommended"), dict) else {}
    alternatives = [x for x in result.get("alternatives", []) if isinstance(x, dict)]
    title = f"Gasolina — {segment}"
    subtitle = f"{map_payload.get('origin', {}).get('name', 'Origen')} → {map_payload.get('destination', {}).get('name', 'Destino')}"
    google_recommended_link = html.escape(map_payload.get("google_maps_recommended_route", ""))
    google_all_link = html.escape(map_payload.get("google_maps_all_candidates_route", ""))
    apple_link = html.escape(map_payload.get("apple_maps_recommended_station", ""))

    def station_card(row: dict[str, Any], role: str) -> str:
        if not row:
            return ""
        name = html.escape(str(row.get("station_name") or row.get("station_key") or "N/D"))
        address = html.escape(str(row.get("address") or ""))
        municipality = html.escape(str(row.get("municipality") or ""))
        price = html.escape(format_price_label(row.get("price")))
        updated = html.escape(str(row.get("official_timestamp") or "N/D"))
        badge = "RECOMENDADA" if role == "recommended" else "ALTERNATIVA"
        klass = "recommended" if role == "recommended" else "alternative"
        return f"""
        <div class=\"station-card {klass}\">
          <div class=\"station-top\">
            <span class=\"badge\">{badge}</span>
            <span class=\"station-price\">{price}</span>
          </div>
          <h3>{name}</h3>
          <p>{address}<br><span>{municipality}</span></p>
          <small>Actualizado: {updated}</small>
        </div>
        """

    alt_cards = "".join(station_card(a, "alternative") for a in alternatives[:3])
    recommended_card = station_card(recommended, "recommended")

    return f"""
<!doctype html>
<html lang=\"es\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" />
  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
  <style>
    :root {{ --green:#16803c; --orange:#d97706; --ink:#1f2933; --muted:#5f6b76; --blue:#2563eb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f4f6f8; color: var(--ink); }}
    .page {{ max-width: 1180px; margin: 0 auto; padding: 14px; }}
    .hero {{ background: #fff; border: 1px solid #dbe4ee; border-radius: 18px; padding: 14px 16px; box-shadow: 0 4px 18px rgba(31,41,51,.06); }}
    .hero h1 {{ margin: 0 0 4px; font-size: 22px; letter-spacing: .2px; }}
    .hero p {{ margin: 0; color: var(--muted); font-size: 14px; }}
    .layout {{ display: grid; grid-template-columns: minmax(280px, 1fr) minmax(280px, 1fr); gap: 12px; margin-top: 12px; }}
    .croquis {{ background: #fff; border: 1px solid #dbe4ee; border-radius: 18px; padding: 14px; box-shadow: 0 4px 18px rgba(31,41,51,.06); }}
    .croquis-title {{ display: flex; justify-content: space-between; gap: 8px; align-items: center; margin-bottom: 10px; }}
    .croquis-title h2 {{ margin: 0; font-size: 17px; }}
    .pill {{ background: #e8f2ff; color:#174a8b; padding: 5px 9px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .route-line {{ display: grid; grid-template-columns: 1fr 36px 1fr 36px 1fr; align-items: center; margin: 12px 0 14px; }}
    .node {{ background:#f9fafb; border:1px solid #d7dee8; border-radius: 14px; padding: 10px; min-height: 76px; }}
    .node b {{ display:block; font-size: 15px; }}
    .node span {{ color:var(--muted); font-size: 12px; }}
    .arrow {{ height: 3px; background:#445566; position: relative; }}
    .arrow:after {{ content:''; position:absolute; right:-1px; top:-5px; width:0; height:0; border-top:7px solid transparent; border-bottom:7px solid transparent; border-left:9px solid #445566; }}
    .cards {{ display: grid; grid-template-columns: 1fr; gap: 10px; }}
    .station-card {{ border-radius: 15px; padding: 12px; border: 2px solid #e5e7eb; background:#fff; }}
    .station-card.recommended {{ border-color: var(--green); background: #ecfdf3; }}
    .station-card.alternative {{ border-color: var(--orange); background: #fff7ed; }}
    .station-top {{ display:flex; justify-content:space-between; gap:10px; align-items:center; }}
    .badge {{ display:inline-block; border-radius:999px; padding:4px 8px; font-size:11px; font-weight:800; color:#fff; background:var(--green); }}
    .alternative .badge {{ background:var(--orange); }}
    .station-price {{ font-weight:900; font-size:20px; }}
    .station-card h3 {{ margin: 8px 0 4px; font-size: 17px; }}
    .station-card p {{ margin: 0 0 6px; color: var(--muted); font-size: 13px; line-height:1.35; }}
    .station-card small {{ color: var(--muted); }}
    .map-wrap {{ background:#fff; border: 1px solid #dbe4ee; border-radius: 18px; padding: 12px; margin-top: 12px; box-shadow: 0 4px 18px rgba(31,41,51,.06); }}
    .map-head {{ display:flex; justify-content:space-between; gap:10px; align-items:center; margin-bottom: 8px; }}
    .map-head h2 {{ margin:0; font-size:17px; }}
    .distance {{ color:var(--muted); font-size:13px; }}
    #map {{ height: 52vh; min-height: 420px; width: 100%; border-radius: 14px; overflow:hidden; }}
    .actions {{ background:#fff; border:1px solid #dbe4ee; border-radius:18px; padding:14px; margin-top:12px; box-shadow: 0 4px 18px rgba(31,41,51,.06); }}
    .actions h2 {{ margin:0 0 10px; font-size:17px; }}
    .links {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }}
    .links a {{ display:block; text-align:center; background: #111827; color: white; padding: 12px 10px; border-radius: 13px; text-decoration: none; font-size: 14px; font-weight: 700; }}
    .links a.secondary {{ background:#374151; }}
    .links a.apple {{ background:#0f766e; }}
    .note {{ margin-top:10px; color:var(--muted); font-size:12px; }}
    .price {{ font-weight: 800; font-size: 16px; }}
    @media (max-width: 820px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .route-line {{ grid-template-columns: 1fr; gap: 8px; }}
      .arrow {{ height: 28px; width: 3px; margin-left: 18px; }}
      .arrow:after {{ right:-5px; top:21px; border-left:7px solid transparent; border-right:7px solid transparent; border-top:9px solid #445566; border-bottom:0; }}
      .links {{ grid-template-columns: 1fr; }}
      #map {{ height: 55vh; min-height: 360px; }}
    }}
  </style>
</head>
<body>
  <main class=\"page\">
    <section class=\"hero\">
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(subtitle)} · Fuente principal: API Render / Precioil · SP95</p>
    </section>

    <section class=\"layout\">
      <div class=\"croquis\">
        <div class=\"croquis-title\"><h2>Croquis de decisión</h2><span class=\"pill\" id=\"routeDistance\">Calculando distancia…</span></div>
        <div class=\"route-line\">
          <div class=\"node\"><b id=\"originName\">Origen</b><span>Salida</span></div>
          <div class=\"arrow\"></div>
          <div class=\"node\"><b id=\"recommendedNode\">Repostaje recomendado</b><span id=\"recommendedPrice\">Precio</span></div>
          <div class=\"arrow\"></div>
          <div class=\"node\"><b id=\"destinationName\">Destino</b><span>Llegada</span></div>
        </div>
        <div class=\"cards\">
          {recommended_card}
          {alt_cards}
        </div>
      </div>

      <div class=\"croquis\">
        <div class=\"croquis-title\"><h2>Resumen rápido</h2><span class=\"pill\">Ruta orientativa</span></div>
        <p style=\"margin:0 0 10px;color:var(--muted);line-height:1.45\">
          El mapa de abajo te ayuda a ver dónde cae cada gasolinera respecto al trayecto.
          La decisión principal se mantiene por precio, comodidad de ruta y datos actualizados.
        </p>
        <div class=\"station-card recommended\">
          <div class=\"station-top\"><span class=\"badge\">MEJOR PARADA</span><span class=\"station-price\">{html.escape(format_price_label(recommended.get('price')))}</span></div>
          <h3>{html.escape(str(recommended.get('station_name') or 'N/D'))}</h3>
          <p>{html.escape(str(recommended.get('address') or ''))}<br>{html.escape(str(recommended.get('municipality') or ''))}</p>
          <small>Actualizado: {html.escape(str(recommended.get('official_timestamp') or 'N/D'))}</small>
        </div>
        <p class=\"note\">Toca un marcador del mapa para ver precio, hora de actualización y fuente.</p>
      </div>
    </section>

    <section class=\"map-wrap\">
      <div class=\"map-head\">
        <h2>Mapa visual con estaciones y precios</h2>
        <span class=\"distance\" id=\"mapDistance\">Distancia orientativa pendiente</span>
      </div>
      <div id=\"map\"></div>
    </section>

    <section class=\"actions\">
      <h2>Rutas disponibles</h2>
      <div class=\"links\">
        <a href=\"{google_recommended_link}\" target=\"_blank\">Google Maps · recomendada</a>
        <a class=\"secondary\" href=\"{google_all_link}\" target=\"_blank\">Google Maps · con alternativas</a>
        <a class=\"apple\" href=\"{apple_link}\" target=\"_blank\">Apple Maps · estación</a>
      </div>
      <p class=\"note\">Mapa orientativo: para navegación real usa Google Maps o Apple Maps. El tráfico puede variar la ruta final.</p>
    </section>
  </main>

<script>
const markers = {markers_json};
const origin = {origin_json};
const destination = {destination_json};
const map = L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap'
}}).addTo(map);
const bounds = [];
const routePoints = [];
function haversine(a,b,c,d) {{
  const R=6371; const toRad=x=>x*Math.PI/180;
  const dLat=toRad(c-a), dLon=toRad(d-b);
  const s=Math.sin(dLat/2)**2+Math.cos(toRad(a))*Math.cos(toRad(c))*Math.sin(dLon/2)**2;
  return 2*R*Math.asin(Math.sqrt(s));
}}
function addPoint(lat, lon, popup, color, radius=8) {{
  if (lat == null || lon == null) return;
  bounds.push([lat, lon]);
  routePoints.push([lat, lon]);
  L.circleMarker([lat, lon], {{ radius, color, fillColor: color, fillOpacity: 0.88, weight: 3 }}).addTo(map).bindPopup(popup);
}}
const recommended = markers.find(m => m.role === 'recommended') || markers[0];
document.getElementById('originName').textContent = origin.name || 'Origen';
document.getElementById('destinationName').textContent = destination.name || 'Destino';
if (recommended) {{
  document.getElementById('recommendedNode').textContent = recommended.name || 'Repostaje';
  document.getElementById('recommendedPrice').textContent = recommended.price_label || '';
}}
addPoint(origin.lat, origin.lon, '<b>Origen</b><br>' + (origin.name || ''), '#111827', 7);
if (recommended) {{
  const popup = `<span class=\"badge\">Recomendada</span><br><b>${{recommended.name || ''}}</b><br>${{recommended.address || ''}}<br><span class=\"price\">${{recommended.price_label || ''}}</span><br>Actualizado: ${{recommended.updated_at || 'N/D'}}<br>Fuente: ${{recommended.source || 'N/D'}}`;
  addPoint(recommended.lat, recommended.lon, popup, '#16803c', 10);
}}
addPoint(destination.lat, destination.lon, '<b>Destino</b><br>' + (destination.name || ''), '#111827', 7);
markers.forEach((m) => {{
  if (recommended && m.station_key === recommended.station_key) return;
  const popup = `<span class=\"badge\" style=\"background:#d97706\">Alternativa</span><br><b>${{m.name || ''}}</b><br>${{m.address || ''}}<br><span class=\"price\">${{m.price_label || ''}}</span><br>Actualizado: ${{m.updated_at || 'N/D'}}<br>Fuente: ${{m.source || 'N/D'}}`;
  addPoint(m.lat, m.lon, popup, '#d97706', 8);
}});
if (routePoints.length >= 2) {{
  L.polyline(routePoints.slice(0,3), {{color:'#2563eb', weight:4, opacity:.75, dashArray:'8,8'}}).addTo(map);
}}
if (bounds.length) {{ map.fitBounds(bounds, {{ padding: [35, 35] }}); }} else {{ map.setView([40.5, -3.3], 11); }}
let distText = 'Distancia orientativa';
if (origin.lat && origin.lon && destination.lat && destination.lon) {{
  let d = 0;
  if (recommended && recommended.lat && recommended.lon) {{
    d = haversine(origin.lat, origin.lon, recommended.lat, recommended.lon) + haversine(recommended.lat, recommended.lon, destination.lat, destination.lon);
  }} else {{
    d = haversine(origin.lat, origin.lon, destination.lat, destination.lon);
  }}
  distText = '≈ ' + d.toFixed(1).replace('.', ',') + ' km orientativos';
}}
document.getElementById('routeDistance').textContent = distText;
document.getElementById('mapDistance').textContent = distText;
</script>
</body>
</html>
"""


def manual_fallback(segment: str) -> dict[str, Any]:
    fallback_by_segment = {
        "forus_return": {
            "station_key": "ballenoil_varsovia",
            "station_name": "BALLENOIL Varsovia / Vía Complutense",
            "municipality": "ALCALA DE HENARES",
            "price": None,
            "reason": "Fallback por ruta cómoda y validación presencial previa.",
        },
        "forus_out": {
            "station_key": "ballenoil_varsovia",
            "station_name": "BALLENOIL Varsovia / Vía Complutense",
            "municipality": "ALCALA DE HENARES",
            "price": None,
            "reason": "Fallback por ruta cómoda hacia Forus y validación presencial previa.",
        },
        "cabanillas_return": {
            "station_key": "family_energy_azuqueca",
            "station_name": "FAMILY ENERGY / Family Cash Azuqueca",
            "municipality": "AZUQUECA DE HENARES",
            "price": None,
            "reason": "Fallback recomendado solo para Cabanillas -> Anchuelo.",
        },
        "alcala": {
            "station_key": "ballenoil_varsovia",
            "station_name": "BALLENOIL Varsovia / Vía Complutense",
            "municipality": "ALCALA DE HENARES",
            "price": None,
            "reason": "Fallback por ruta cómoda y validación presencial previa.",
        },
    }
    return fallback_by_segment.get(segment, fallback_by_segment["forus_return"])




def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def split_env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def local_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))


def public_calendar_ics_urls() -> list[str]:
    urls: list[str] = []
    urls.extend(split_env_list("PUBLIC_CALENDAR_ICS_URLS"))
    for key in (
        "FORUS_CALENDAR_ICS_URL",
        "PERSONAL_CALENDAR_ICS_URL",
        "GOOGLE_FORUS_ICS_URL",
        "GOOGLE_PERSONAL_ICS_URL",
    ):
        value = os.getenv(key, "").strip()
        if value:
            urls.append(value)

    # Deduplica manteniendo orden.
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def ics_unescape(value: str) -> str:
    return (
        value.replace(r"\n", "\n")
        .replace(r"\N", "\n")
        .replace(r"\,", ",")
        .replace(r"\;", ";")
        .replace(r"\\", "\\")
    )


def unfold_ics_lines(text: str) -> list[str]:
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for line in raw_lines:
        if not line:
            continue
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def parse_ics_property(line: str) -> tuple[str, dict[str, str], str]:
    left, _, value = line.partition(":")
    parts = left.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, _, val = part.partition("=")
        if key:
            params[key.upper()] = val.strip('"')
    return name, params, ics_unescape(value)


def parse_ics_datetime(value: str, params: dict[str, str], tz: ZoneInfo) -> datetime | None:
    try:
        if params.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and value.isdigit()):
            d = datetime.strptime(value[:8], "%Y%m%d").date()
            return datetime.combine(d, datetime.min.time(), tzinfo=tz)

        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

        parsed = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
        tzid = params.get("TZID")
        event_tz = ZoneInfo(tzid) if tzid else tz
        return parsed.replace(tzinfo=event_tz).astimezone(tz)
    except Exception:
        return None


def parse_ics_events(text: str, calendar_name: str = "") -> list[dict[str, Any]]:
    tz = local_tz()
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in unfold_ics_lines(text):
        if line == "BEGIN:VEVENT":
            current = {"calendar": calendar_name}
            continue
        if line == "END:VEVENT":
            if current and current.get("start"):
                if not current.get("end"):
                    current["end"] = current["start"]
                events.append(current)
            current = None
            continue
        if current is None:
            continue

        name, params, value = parse_ics_property(line)
        if name == "SUMMARY":
            current["summary"] = value
        elif name == "LOCATION":
            current["location"] = value
        elif name == "DESCRIPTION":
            current["description"] = value
        elif name == "DTSTART":
            current["start"] = parse_ics_datetime(value, params, tz)
        elif name == "DTEND":
            current["end"] = parse_ics_datetime(value, params, tz)

    return events


async def fetch_public_calendar_events_for_today() -> list[dict[str, Any]]:
    urls = public_calendar_ics_urls()
    if not urls:
        return []

    tz = local_tz()
    today = datetime.now(tz).date()
    start_day = datetime.combine(today, datetime.min.time(), tzinfo=tz)
    end_day = start_day + timedelta(days=1)

    events: list[dict[str, Any]] = []
    timeout = httpx.Timeout(12.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for idx, url in enumerate(urls, start=1):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                parsed = parse_ics_events(resp.text, calendar_name=f"ics_{idx}")
                for event in parsed:
                    event_start = event.get("start")
                    event_end = event.get("end") or event_start
                    if event_start and event_end and event_start < end_day and event_end >= start_day:
                        events.append(event)
            except Exception as exc:
                print(f"[calendar] No se pudo leer ICS {idx}: {type(exc).__name__}: {exc}")

    return sorted(events, key=lambda ev: ev.get("start") or datetime.max.replace(tzinfo=tz))


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.casefold()


def event_text(event: dict[str, Any]) -> str:
    return normalize_text(
        " ".join(
            str(event.get(key, ""))
            for key in ("summary", "location", "description", "calendar")
        )
    )


def keyword_list(env_name: str, defaults: list[str]) -> list[str]:
    configured = split_env_list(env_name)
    return [normalize_text(item) for item in (configured or defaults)]


def event_matches(event: dict[str, Any], keywords: list[str]) -> bool:
    text = event_text(event)
    return any(keyword and keyword in text for keyword in keywords)


def classify_segment_from_calendar_events(events: list[dict[str, Any]]) -> str | None:
    if not events:
        return None

    tz = local_tz()
    now = datetime.now(tz)

    forus_keywords = keyword_list("CAL_FORUS_KEYWORDS", ["forus", "gimnasio", "natacion", "natación", "padel", "pádel"])
    cabanillas_keywords = keyword_list("CAL_CABANILLAS_KEYWORDS", ["cabanillas", "dsv", "guadalajara", "azuqueca"])
    alcala_keywords = keyword_list("CAL_ALCALA_KEYWORDS", ["alcala", "alcalá", "alcala de henares", "alcalá de henares"])

    forus_events = [event for event in events if event_matches(event, forus_keywords)]
    cabanillas_events = [event for event in events if event_matches(event, cabanillas_keywords)]
    alcala_events = [event for event in events if event_matches(event, alcala_keywords)]

    # Si hay Forus pendiente hoy, lo normal es ir hacia Forus. Si ya empezó/terminó, toca vuelta.
    upcoming_forus = [event for event in forus_events if (event.get("start") and event["start"] > now)]
    if upcoming_forus:
        upcoming_forus.sort(key=lambda ev: ev["start"])
        return "forus_out"

    past_or_current_forus = [
        event for event in forus_events
        if event.get("start") and event["start"] <= now
    ]
    if past_or_current_forus:
        return "forus_return"

    # Para Cabanillas solo existe segmento de vuelta configurado.
    if cabanillas_events:
        return "cabanillas_return"

    if alcala_events:
        return "alcala"

    return None


def fallback_auto_segment() -> str:
    today = date.today()
    return "forus_return" if today.weekday() < 5 else "alcala"


async def resolve_auto_segment(segment: str) -> str:
    if segment != "auto":
        return segment

    if env_bool("PUBLIC_CALENDAR_ENABLED", True):
        calendar_segment = classify_segment_from_calendar_events(
            await fetch_public_calendar_events_for_today()
        )
        if calendar_segment:
            return calendar_segment

    return fallback_auto_segment()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug-precioil")
async def debug_precioil(segment: str = Query(default="all")) -> dict[str, Any]:
    try:
        payload, rows = await fetch_precioil_relevant_rows(segment)
        items = payload.get("items", []) if isinstance(payload, dict) else precioil_station_items(payload)
        return {
            "ok": True,
            "source": "precioil",
            "segment": segment,
            "regions_used": precioil_regions_for_segment(segment),
            "items_count": len(items),
            "matched_count": len(rows),
            "matched_stations": rows,
            "region_errors": payload.get("region_errors", {}) if isinstance(payload, dict) else {},
            "sample_start": str(items[:5])[:1500],
        }
    except HTTPException as e:
        return {"ok": False, "detail": e.detail}
    except Exception as e:
        return {"ok": False, "detail": f"Error interno en debug_precioil: {type(e).__name__}: {e}"}


@app.get("/prices")
async def prices(
    save: bool = Query(default=True),
    source: str = Query(default="precioil"),
    segment: str = Query(default="all"),
) -> dict[str, Any]:
    # source: precioil | official | auto
    # Por defecto Precioil es la fuente principal práctica; official queda como opción explícita/fallback.
    precioil_error: Any = None

    if source in ("precioil", "auto"):
        try:
            payload, rows = await fetch_precioil_relevant_rows(segment)
            if save:
                save_observations(rows)
            items = payload.get("items", []) if isinstance(payload, dict) else precioil_station_items(payload)
            return {
                "status": "ok",
                "source": "precioil",
                "segment": segment,
                "regions_used": precioil_regions_for_segment(segment),
                "count": len(rows),
                "stations": rows,
                "raw_items_count": len(items),
                "region_errors": payload.get("region_errors", {}) if isinstance(payload, dict) else {},
            }
        except HTTPException as e:
            precioil_error = e.detail
            if source == "precioil":
                raise e

    if source in ("official", "auto"):
        try:
            payload = await fetch_official_data()
            rows = extract_relevant_rows(payload)
            if save:
                save_observations(rows)
            return {
                "status": "degraded" if precioil_error else "ok",
                "source": "official",
                "precioil_error": precioil_error,
                "official_timestamp": payload.get("Fecha", ""),
                "count": len(rows),
                "stations": rows,
            }
        except HTTPException as official_error:
            if source == "official":
                raise official_error
            raise HTTPException(
                status_code=502,
                detail={
                    "precioil_error": precioil_error,
                    "official_error": official_error.detail,
                },
            )

    raise HTTPException(status_code=400, detail="source debe ser precioil, official o auto")

@app.get("/recommend")
async def recommend(
    segment: str = Query(default="auto"),
    source: str = Query(default="precioil"),
) -> dict[str, Any]:
    # source: precioil | official | auto
    # Precioil queda como fuente principal. La oficial solo se usa si se pide explícitamente o como fallback en auto.
    segment = await resolve_auto_segment(segment)
    precioil_error: Any = None

    if source in ("precioil", "auto"):
        try:
            payload, rows = await fetch_precioil_relevant_rows(segment)
            save_observations(rows)
            result = choose_best(rows, segment)
            items = payload.get("items", []) if isinstance(payload, dict) else precioil_station_items(payload)
            return {
                "status": "ok",
                "source": "precioil",
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "segment": segment,
                "regions_used": precioil_regions_for_segment(segment),
                "precioil_matched_count": len(rows),
                "precioil_raw_items_count": len(items),
                "region_errors": payload.get("region_errors", {}) if isinstance(payload, dict) else {},
                **result,
                "map": build_map_payload(segment, result),
            }
        except HTTPException as e:
            precioil_error = e.detail
            if source == "precioil":
                return {
                    "status": "fallback",
                    "source": "manual",
                    "precioil_error": precioil_error,
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "segment": segment,
                    "recommended": manual_fallback(segment),
                    "alternatives": [],
                    "warning": "Precioil falló y la fuente oficial no se usó porque source=precioil.",
                    "map": build_map_payload(segment, {"recommended": manual_fallback(segment), "alternatives": []}),
                }

    if source in ("official", "auto"):
        try:
            payload = await fetch_official_data()
            rows = extract_relevant_rows(payload)
            save_observations(rows)
            result = choose_best(rows, segment)
            return {
                "status": "degraded" if precioil_error else "ok",
                "source": "official",
                "precioil_error": precioil_error,
                "official_timestamp": payload.get("Fecha", ""),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "segment": segment,
                **result,
                "map": build_map_payload(segment, result),
            }
        except HTTPException as official_error:
            if source == "official":
                return {
                    "status": "fallback",
                    "source": "manual",
                    "official_error": official_error.detail,
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "segment": segment,
                    "recommended": manual_fallback(segment),
                    "alternatives": [],
                    "warning": "La fuente oficial falló y Precioil no se usó porque source=official.",
                    "map": build_map_payload(segment, {"recommended": manual_fallback(segment), "alternatives": []}),
                }

            return {
                "status": "fallback",
                "source": "manual",
                "precioil_error": precioil_error,
                "official_error": official_error.detail,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "segment": segment,
                "recommended": manual_fallback(segment),
                "alternatives": [],
                "warning": "Fallaron Precioil y fuente oficial.",
                "map": build_map_payload(segment, {"recommended": manual_fallback(segment), "alternatives": []}),
            }

    raise HTTPException(status_code=400, detail="source debe ser precioil, official o auto")


@app.get("/map", response_class=HTMLResponse)
async def map_view(
    segment: str = Query(default="auto"),
    source: str = Query(default="precioil"),
) -> HTMLResponse:
    segment = await resolve_auto_segment(segment)

    try:
        if source in ("precioil", "auto"):
            payload, rows = await fetch_precioil_relevant_rows(segment)
            result = choose_best(rows, segment)
        elif source == "official":
            payload = await fetch_official_data()
            rows = extract_relevant_rows(payload)
            result = choose_best(rows, segment)
        else:
            raise HTTPException(status_code=400, detail="source debe ser precioil, official o auto")
    except HTTPException:
        result = {"recommended": manual_fallback(segment), "alternatives": []}

    map_payload = build_map_payload(segment, result)
    return HTMLResponse(render_visual_map_html(segment, result, map_payload))

@app.get("/history/{station_key}")
def history(station_key: str, limit: int = 50) -> dict[str, Any]:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT station_key, station_name, address, municipality, price, official_timestamp, fetched_at
            FROM observations
            WHERE station_key = ?
            ORDER BY fetched_at DESC
            LIMIT ?
        """, (station_key, limit)).fetchall()
    return {"station_key": station_key, "observations": [dict(r) for r in rows]}
