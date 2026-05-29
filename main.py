from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urlencode, urlparse, parse_qs, unquote
import html
import math

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, Response

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "gasolina_history.sqlite3"

PRECIOIL_BASE_URL = "https://api.precioil.es"

# Calendarios publicados usados para resolver segment=auto.
# Acepta URLs ICS directas, webcal:// de iCloud y enlaces Google Calendar embed.
# Se pueden sobreescribir en Render con:
#   PUBLIC_CALENDAR_ICS_URLS
#   FORUS_CALENDAR_ICS_URL
#   PERSONAL_CALENDAR_ICS_URL
DEFAULT_PERSONAL_CALENDAR_ICS_URL = (
    "webcal://p118-caldav.icloud.com/published/2/"
    "Mjg2NzY1MzA2Mjg2NzY1M94Qkr3iSV-Qf6LzFWxwtxWv823O3SNidOtaXorjQAgVCp4YD6inMHiLP07VtHQkWEyd5rLTtoIsaNsyJcvrrxA"
)
DEFAULT_FORUS_CALENDAR_ICS_URL = (
    "webcal://p118-caldav.icloud.com/published/2/"
    "Mjg2NzY1MzA2Mjg2NzY1M94Qkr3iSV-Qf6LzFWxwtxXu7Jad9zu2kVc5XhgHE-fbckUoVxbrvBEPq0imCC7TWQl9R0T9cylIx3mPYrWOmmI"
)
DEFAULT_PUBLIC_CALENDAR_ICS_URLS = [
    DEFAULT_PERSONAL_CALENDAR_ICS_URL,
    DEFAULT_FORUS_CALENDAR_ICS_URL,
]

# Zonas de búsqueda Precioil.
# Alcalá/Daganzo: útil para Forus -> Anchuelo y Anchuelo -> Forus.
PRECIOIL_ALCALA_LAT = 40.48198
PRECIOIL_ALCALA_LON = -3.36354

# Centros adicionales para que el radio ampliado de Precioil cubra mejor el corredor real.
# Precioil suele devolver un número limitado de resultados por consulta, así que conviene
# consultar varios centros y deduplicar después.
PRECIOIL_ALCALA_FORJAS_LAT = 40.4923
PRECIOIL_ALCALA_FORJAS_LON = -3.36153
PRECIOIL_ANCHUELO_LAT = 40.4667
PRECIOIL_ANCHUELO_LON = -3.2687
PRECIOIL_ALCALA_ESTE_LAT = 40.4800
PRECIOIL_ALCALA_ESTE_LON = -3.3200

# Punto intermedio Cabanillas/Azuqueca: útil para DSV/Cabanillas -> Anchuelo.
PRECIOIL_CABANILLAS_AZUQUECA_LAT = 40.6000
PRECIOIL_CABANILLAS_AZUQUECA_LON = -3.2500

# Precioil usa radio en km, no en metros.
PRECIOIL_SEARCH_RADIUS_KM = int(os.environ.get("PRECIOIL_SEARCH_RADIUS_KM", "20"))

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://gasolina-api-christian.onrender.com").rstrip("/")

# Coordenadas aproximadas para pintar mapa visual orientativo.
# Las coordenadas exactas de las gasolineras vienen de Precioil.
ROUTE_ENDPOINTS = {
    "cabanillas_out": {
        "origin_name": "Casa · C/ Almendros 6, Anchuelo",
        "origin_address": "Calle Almendros 6, 28818 Anchuelo, Madrid",
        "origin_lat": 40.4667,
        "origin_lon": -3.2687,
        "destination_name": "DSV / Cabanillas del Campo · Av. de la Veguilla 7",
        "destination_address": "Avenida de la Veguilla 7, 19171 Cabanillas del Campo, Guadalajara",
        "destination_lat": 40.6302,
        "destination_lon": -3.2357,
    },
    "cabanillas_return": {
        "origin_name": "DSV / Cabanillas del Campo · Av. de la Veguilla 7",
        "origin_address": "Avenida de la Veguilla 7, 19171 Cabanillas del Campo, Guadalajara",
        "origin_lat": 40.6302,
        "origin_lon": -3.2357,
        "destination_name": "Casa · C/ Almendros 6, Anchuelo",
        "destination_address": "Calle Almendros 6, 28818 Anchuelo, Madrid",
        "destination_lat": 40.4667,
        "destination_lon": -3.2687,
    },
    "forus_return": {
        "origin_name": "Forus Alcalá Forjas · C/ Belvís del Jarama 8",
        "origin_address": "Calle Belvís del Jarama 8, 28806 Alcalá de Henares, Madrid",
        "origin_lat": 40.4923,
        "origin_lon": -3.36153,
        "destination_name": "Casa · C/ Almendros 6, Anchuelo",
        "destination_address": "Calle Almendros 6, 28818 Anchuelo, Madrid",
        "destination_lat": 40.4667,
        "destination_lon": -3.2687,
    },
    "forus_out": {
        "origin_name": "Casa · C/ Almendros 6, Anchuelo",
        "origin_address": "Calle Almendros 6, 28818 Anchuelo, Madrid",
        "origin_lat": 40.4667,
        "origin_lon": -3.2687,
        "destination_name": "Forus Alcalá Forjas · C/ Belvís del Jarama 8",
        "destination_address": "Calle Belvís del Jarama 8, 28806 Alcalá de Henares, Madrid",
        "destination_lat": 40.4923,
        "destination_lon": -3.36153,
    },
    "alcala": {
        "origin_name": "Forus Alcalá Forjas · C/ Belvís del Jarama 8",
        "origin_address": "Calle Belvís del Jarama 8, 28806 Alcalá de Henares, Madrid",
        "origin_lat": 40.4923,
        "origin_lon": -3.36153,
        "destination_name": "Casa · C/ Almendros 6, Anchuelo",
        "destination_address": "Calle Almendros 6, 28818 Anchuelo, Madrid",
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
    "alcala_forjas": {
        "latitud": PRECIOIL_ALCALA_FORJAS_LAT,
        "longitud": PRECIOIL_ALCALA_FORJAS_LON,
        "radio": PRECIOIL_SEARCH_RADIUS_KM,
    },
    "anchuelo": {
        "latitud": PRECIOIL_ANCHUELO_LAT,
        "longitud": PRECIOIL_ANCHUELO_LON,
        "radio": PRECIOIL_SEARCH_RADIUS_KM,
    },
    "alcala_este": {
        "latitud": PRECIOIL_ALCALA_ESTE_LAT,
        "longitud": PRECIOIL_ALCALA_ESTE_LON,
        "radio": PRECIOIL_SEARCH_RADIUS_KM,
    },
    "cabanillas_azuqueca": {
        "latitud": PRECIOIL_CABANILLAS_AZUQUECA_LAT,
        "longitud": PRECIOIL_CABANILLAS_AZUQUECA_LON,
        "radio": PRECIOIL_SEARCH_RADIUS_KM,
    },
}

app = FastAPI(title="Gasolina Christian API", version="1.3.0")


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



def row_lat_lon(row: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    lat = first_present(row, ["latitud", "lat", "latitude"])
    lon = first_present(row, ["longitud", "lon", "lng", "longitude"])
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def _equirect_xy(lat: float, lon: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    # Coordenadas locales aproximadas en km, suficiente para filtros de pocos kilómetros.
    x = math.radians(lon - ref_lon) * math.cos(math.radians(ref_lat)) * 6371.0088
    y = math.radians(lat - ref_lat) * 6371.0088
    return x, y


def point_segment_distance_km(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    lat, lon = point
    ref_lat = (lat + a[0] + b[0]) / 3
    ref_lon = (lon + a[1] + b[1]) / 3
    px, py = _equirect_xy(lat, lon, ref_lat, ref_lon)
    ax, ay = _equirect_xy(a[0], a[1], ref_lat, ref_lon)
    bx, by = _equirect_xy(b[0], b[1], ref_lat, ref_lon)
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def point_polyline_distance_km(point: tuple[float, float], points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return float("inf")
    return min(point_segment_distance_km(point, points[i], points[i + 1]) for i in range(len(points) - 1))


def route_corridor_points(segment: str) -> list[tuple[float, float]]:
    endpoint = ROUTE_ENDPOINTS.get(segment, ROUTE_ENDPOINTS["forus_return"])
    origin = (float(endpoint["origin_lat"]), float(endpoint["origin_lon"]))
    destination = (float(endpoint["destination_lat"]), float(endpoint["destination_lon"]))

    # Trazadas visuales aproximadas del corredor de carretera. No son navegación giro a giro:
    # sirven para filtrar estaciones cercanas al trayecto y pintar el mapa sin líneas rectas raras.
    if segment == "forus_out":
        return [origin, (40.4687, -3.2820), (40.4768, -3.3140), (40.4865, -3.3440), destination]
    if segment in ("forus_return", "alcala"):
        return [origin, (40.4865, -3.3440), (40.4768, -3.3140), (40.4687, -3.2820), destination]
    if segment == "cabanillas_return":
        return [origin, (40.6040, -3.2500), (40.5600, -3.2550), (40.5150, -3.2700), destination]
    return [origin, destination]


def extract_precioil_rows_dynamic(items: list[dict[str, Any]], segment: str) -> list[dict[str, Any]]:
    endpoint = ROUTE_ENDPOINTS.get(segment, ROUTE_ENDPOINTS["forus_return"])
    route_points = route_corridor_points(segment)
    destination = (float(endpoint["destination_lat"]), float(endpoint["destination_lon"]))
    route_max_km = float(os.environ.get("ROUTE_CORRIDOR_MAX_KM", "8.0"))
    destination_max_km = float(os.environ.get("DESTINATION_NEAR_MAX_KM", "8.0"))

    rows: list[dict[str, Any]] = []
    fetched_timestamp = datetime.now().isoformat(timespec="seconds")

    for r in items:
        price = extract_price_95_from_precioil(r)
        lat, lon = row_lat_lon(r)
        if price is None or lat is None or lon is None:
            continue

        route_distance = point_polyline_distance_km((lat, lon), route_points)
        destination_distance = haversine_km(lat, lon, destination[0], destination[1])
        if route_distance > route_max_km and destination_distance > destination_max_km:
            continue

        station_name = str(first_present(r, ["Rótulo", "Rotulo", "rotulo", "nombre", "Nombre", "marca", "Marca", "brand", "nombreEstacion"]) or "")
        address = str(first_present(r, ["Dirección", "Direccion", "direccion", "address", "Address"]) or "")
        municipality = str(first_present(r, ["Municipio", "municipio", "localidad", "Localidad", "city", "poblacion", "nombreMunicipio"]) or "")
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
        station_id = first_present(r, ["idEstacion", "id", "station_id", "codigo"])
        station_key = f"precioil_{station_id}" if station_id is not None else norm(f"{station_name}_{address}")[:80]

        row = {
            "station_key": station_key,
            "station_name": station_name,
            "address": address,
            "municipality": municipality,
            "price": price,
            "official_timestamp": str(updated_at or fetched_timestamp),
            "route_tags": [segment, "near_route"],
            "trust_note": f"Cerca del trayecto: {route_distance:.1f} km · cerca destino: {destination_distance:.1f} km",
            "source": "precioil",
            "distance_to_route_km": round(route_distance, 3),
            "distance_to_destination_km": round(destination_distance, 3),
            "raw": r,
        }
        rows.append(row)

    return rows


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
    # Se consultan varias áreas amplias y luego se filtra por cercanía real al trayecto/destino.
    # Importante: el radio ampliado solo ayuda si consultamos más de un centro, porque Precioil
    # puede limitar el número de resultados por petición. Por eso dividimos el corredor en zonas.
    forus_corridor = ["alcala_daganzo", "alcala_forjas", "alcala_este", "anchuelo"]
    cabanillas_corridor = ["cabanillas_azuqueca", "alcala_este", "anchuelo", "alcala_daganzo"]

    if segment in ("forus_out", "forus_return", "alcala"):
        return forus_corridor
    if segment in ("cabanillas_out", "cabanillas_return"):
        return cabanillas_corridor

    # Modo diagnóstico o desconocido: cubrir todos los corredores conocidos.
    return list(dict.fromkeys(forus_corridor + cabanillas_corridor))


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

    rows = extract_precioil_rows_dynamic(all_items, segment)
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
    candidates = [r for r in rows if r.get("price") is not None]

    if not candidates:
        return {"error": "No hay candidatos con precio cerca del trayecto o destino.", "segment": segment}

    def score(r: dict[str, Any]) -> tuple[float, float, float]:
        # Orden principal: precio. Desempate: más cerca del trayecto y después del destino.
        price = float(r["price"])
        route_distance = float(r.get("distance_to_route_km", 999.0) or 999.0)
        destination_distance = float(r.get("distance_to_destination_km", 999.0) or 999.0)
        return (price, route_distance, destination_distance)

    ordered = sorted(candidates, key=score)
    top5 = ordered[:5]
    best = top5[0]
    alternatives = top5[1:]
    return {
        "segment": segment,
        "selection_rule": "Estaciones cercanas al trayecto/destino; top 5 ordenadas por Gasolina 95 más barata.",
        "nearby_candidates_count": len(candidates),
        "recommended": best,
        "alternatives": alternatives,
        "top_5_cheapest": top5,
    }



async def fetch_public_calendar_events_for_range(
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    """Lee calendarios publicados y devuelve eventos que solapan con [start_at, end_at].

    Se usa para calcular el siguiente trayecto real a partir de eventos futuros, no por mapeo fijo.
    """
    urls = public_calendar_ics_urls()
    if not urls:
        return []

    events: list[dict[str, Any]] = []
    timeout = httpx.Timeout(12.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for idx, url in enumerate(urls, start=1):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                parsed = parse_ics_events(resp.text, calendar_name=calendar_name_from_url(url, idx))
                for event in parsed:
                    event_start = event.get("start")
                    event_end = event.get("end") or event_start
                    if event_start and event_end and event_start < end_at and event_end >= start_at:
                        events.append(event)
            except Exception as exc:
                print(f"[calendar] No se pudo leer próximos eventos ICS {idx}: {type(exc).__name__}: {exc}")

    return sorted(events, key=lambda ev: ev.get("start") or datetime.max.replace(tzinfo=local_tz()))


def route_occurrences_from_calendar_events(events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Convierte eventos de calendario en trayectos fechados.

    Ejemplos:
    - Evento Forus futuro: start => forus_out, end => forus_return.
    - Evento DSV/Cabanillas futuro: start => cabanillas_out; end => cabanillas_return si hay hora de fin real.
    - Evento Alcalá genérico: start/end => alcala.
    """
    forus_keywords = keyword_list("CAL_FORUS_KEYWORDS", ["forus", "gimnasio", "natacion", "natación", "padel", "pádel", "zumba"])
    cabanillas_keywords = keyword_list("CAL_CABANILLAS_KEYWORDS", ["cabanillas", "dsv", "guadalajara", "azuqueca", "oficina"])
    alcala_keywords = keyword_list("CAL_ALCALA_KEYWORDS", ["alcala", "alcalá", "alcala de henares", "alcalá de henares"])

    occurrences: list[dict[str, Any]] = []

    for event in events:
        start = event.get("start")
        end = event.get("end") or start
        if not isinstance(start, datetime):
            continue
        if not isinstance(end, datetime):
            end = start

        if event_matches(event, forus_keywords):
            if start >= now:
                occurrences.append(
                    {
                        "segment": "forus_out",
                        "at": start,
                        "event": serialize_calendar_event(event),
                        "reason": "Próximo evento Forus: ida",
                    }
                )
            if end >= now:
                occurrences.append(
                    {
                        "segment": "forus_return",
                        "at": end,
                        "event": serialize_calendar_event(event),
                        "reason": "Fin de evento Forus: vuelta",
                    }
                )
            continue

        if event_matches(event, cabanillas_keywords):
            # Oficina/DSV/Cabanillas:
            # - antes del inicio, el trayecto real es Casa/Anchuelo → Cabanillas;
            # - para la vuelta solo se añade si el evento tiene una hora de fin real posterior al inicio.
            if start >= now:
                occurrences.append(
                    {
                        "segment": "cabanillas_out",
                        "at": start,
                        "event": serialize_calendar_event(event),
                        "reason": "Próximo evento DSV/Cabanillas: ida",
                    }
                )

            if end > start and end >= now:
                occurrences.append(
                    {
                        "segment": "cabanillas_return",
                        "at": end,
                        "event": serialize_calendar_event(event),
                        "reason": "Fin de evento DSV/Cabanillas: vuelta",
                    }
                )
            continue

        if event_matches(event, alcala_keywords):
            when = start if start >= now else end
            if when >= now:
                occurrences.append(
                    {
                        "segment": "alcala",
                        "at": when,
                        "event": serialize_calendar_event(event),
                        "reason": "Evento en Alcalá",
                    }
                )

    occurrences.sort(key=lambda item: item["at"])
    return occurrences


async def resolve_current_and_next_segments_from_calendar() -> dict[str, Any]:
    """Resuelve trayecto actual y siguiente mirando próximos eventos reales del calendario."""
    tz = local_tz()
    now = datetime.now(tz)
    horizon_days = int(os.environ.get("NEXT_CALENDAR_LOOKAHEAD_DAYS", "3"))
    events = await fetch_public_calendar_events_for_range(now - timedelta(minutes=30), now + timedelta(days=horizon_days))
    occurrences = route_occurrences_from_calendar_events(events, now)

    current = occurrences[0] if occurrences else None
    next_occurrence = None
    if occurrences:
        # El siguiente debe ser el siguiente hito temporal real, no una tabla fija.
        # Si solo hay uno, no inventamos: se usará fallback.
        next_occurrence = occurrences[1] if len(occurrences) > 1 else None

    return {
        "now": now.isoformat(timespec="minutes"),
        "lookahead_days": horizon_days,
        "events_count": len(events),
        "occurrences": [
            {
                "segment": item["segment"],
                "at": item["at"].isoformat(timespec="minutes"),
                "reason": item.get("reason"),
                "event": item.get("event"),
            }
            for item in occurrences[:10]
        ],
        "current_segment": current["segment"] if current else None,
        "next_segment": next_occurrence["segment"] if next_occurrence else None,
        "current_occurrence": {
            "segment": current["segment"],
            "at": current["at"].isoformat(timespec="minutes"),
            "reason": current.get("reason"),
            "event": current.get("event"),
        } if current else None,
        "next_occurrence": {
            "segment": next_occurrence["segment"],
            "at": next_occurrence["at"].isoformat(timespec="minutes"),
            "reason": next_occurrence.get("reason"),
            "event": next_occurrence.get("event"),
        } if next_occurrence else None,
    }


async def next_segment_from_calendar_or_fallback(current_segment: str) -> tuple[str, dict[str, Any]]:
    plan = await resolve_current_and_next_segments_from_calendar()
    next_segment = plan.get("next_segment")
    if isinstance(next_segment, str) and next_segment in ROUTE_ENDPOINTS:
        plan["decision_source"] = "calendar_future_events"
        return next_segment, plan

    fallback = next_segment_for(current_segment)
    plan["decision_source"] = "fallback_no_future_calendar_occurrence"
    plan["fallback_next_segment"] = fallback
    return fallback, plan


def next_segment_for(current_segment: str) -> str:
    """Fallback si el calendario no ofrece un siguiente evento real.

    La ruta siguiente normal se calcula con next_segment_from_calendar_or_fallback(),
    mirando eventos futuros. Esta tabla solo se usa como respaldo.
    """
    default_map = {
        "forus_out": "forus_return",
        "forus_return": "forus_out",
        "cabanillas_out": "cabanillas_return",
        "cabanillas_return": "forus_out",
        "alcala": "forus_return",
    }
    raw = os.getenv("NEXT_SEGMENT_MAP", "").strip()
    if raw:
        try:
            configured = json.loads(raw)
            if isinstance(configured, dict):
                value = configured.get(current_segment)
                if isinstance(value, str) and value in ROUTE_ENDPOINTS:
                    return value
        except Exception:
            pass
    return default_map.get(current_segment, "forus_return")


def wait_analysis(current_result: dict[str, Any], next_result: dict[str, Any]) -> dict[str, Any]:
    current_best = current_result.get("recommended") if isinstance(current_result.get("recommended"), dict) else None
    next_best = next_result.get("recommended") if isinstance(next_result.get("recommended"), dict) else None
    threshold = float(os.environ.get("WAIT_MIN_SAVINGS_EUR_L", "0.02"))

    if not current_best or not next_best:
        return {
            "should_wait": False,
            "reason": "No hay datos suficientes para comparar el trayecto actual con el siguiente.",
            "min_saving_threshold_eur_l": threshold,
        }

    current_price = current_best.get("price")
    next_price = next_best.get("price")
    if not isinstance(current_price, (int, float)) or not isinstance(next_price, (int, float)):
        return {
            "should_wait": False,
            "reason": "No hay precio comparable en alguno de los dos trayectos.",
            "min_saving_threshold_eur_l": threshold,
        }

    saving = round(float(current_price) - float(next_price), 3)
    should_wait = saving >= threshold
    if should_wait:
        reason = f"Compensa esperar: el siguiente trayecto tiene una opción {saving:.3f} €/l más barata."
    elif saving > 0:
        reason = f"El siguiente trayecto es {saving:.3f} €/l más barato, pero no supera el umbral de {threshold:.3f} €/l."
    elif saving == 0:
        reason = "El mejor precio es igual en ambos trayectos; no hay ventaja clara por esperar."
    else:
        reason = f"No compensa esperar: el siguiente trayecto es {abs(saving):.3f} €/l más caro."

    return {
        "should_wait": should_wait,
        "price_difference_current_minus_next_eur_l": saving,
        "min_saving_threshold_eur_l": threshold,
        "current_best": {
            "station_name": current_best.get("station_name"),
            "price": current_price,
            "updated_at": current_best.get("official_timestamp"),
            "address": current_best.get("address"),
        },
        "next_best": {
            "station_name": next_best.get("station_name"),
            "price": next_price,
            "updated_at": next_best.get("official_timestamp"),
            "address": next_best.get("address"),
        },
        "reason": reason,
    }


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


def endpoint_origin_text(endpoint: dict[str, Any]) -> str:
    return str(endpoint.get("origin_address") or endpoint.get("origin_name") or coord_text(endpoint["origin_lat"], endpoint["origin_lon"]))


def endpoint_destination_text(endpoint: dict[str, Any]) -> str:
    return str(endpoint.get("destination_address") or endpoint.get("destination_name") or coord_text(endpoint["destination_lat"], endpoint["destination_lon"]))


def station_destination_text(station: dict[str, Any]) -> str:
    lat, lon = station_lat_lon(station)
    if lat is not None and lon is not None:
        return coord_text(lat, lon)
    name = str(station.get("station_name") or "")
    address = str(station.get("address") or "")
    municipality = str(station.get("municipality") or "")
    return " ".join(x for x in [name, address, municipality] if x).strip()


def build_google_directions(origin: dict[str, Any], destination: dict[str, Any], waypoints: list[dict[str, Any]]) -> str:
    # Para navegación real usamos direcciones postales, no solo coordenadas aproximadas.
    origin_text = endpoint_origin_text(origin)
    destination_text = endpoint_destination_text(destination)
    waypoint_coords = []
    for station in waypoints:
        waypoint = station_destination_text(station)
        if waypoint:
            waypoint_coords.append(waypoint)
    params = {
        "api": "1",
        "origin": origin_text,
        "destination": destination_text,
        "travelmode": "driving",
    }
    if waypoint_coords:
        params["waypoints"] = "|".join(waypoint_coords)
    return "https://www.google.com/maps/dir/?" + urlencode(params)


def build_apple_directions(origin: dict[str, Any], station: Optional[dict[str, Any]] = None) -> str:
    # Apple Maps no soporta waypoints de forma fiable en URL.
    # Este enlace debe representar siempre el trayecto completo (origen → destino final).
    saddr = endpoint_origin_text(origin)
    daddr = endpoint_destination_text(origin)
    return f"https://maps.apple.com/?saddr={quote_plus(saddr)}&daddr={quote_plus(daddr)}&dirflg=d"


def build_apple_station_link(station: Optional[dict[str, Any]]) -> str:
    if not station:
        return ""
    lat, lon = station_lat_lon(station)
    label = f"{station.get('station_name') or 'Gasolinera'} {station.get('address') or ''} {format_price_label(station.get('price'))}".strip()
    if lat is not None and lon is not None:
        return f"https://maps.apple.com/?ll={lat},{lon}&q={quote_plus(label)}"
    destination = station_destination_text(station)
    return f"https://maps.apple.com/?q={quote_plus(destination)}"

ROUTE_ROAD_HINTS = {
    "forus_out": [
        {"lat": 40.4687, "lon": -3.2820},
        {"lat": 40.4768, "lon": -3.3140},
        {"lat": 40.4865, "lon": -3.3440},
    ],
    "forus_return": [
        {"lat": 40.4865, "lon": -3.3440},
        {"lat": 40.4768, "lon": -3.3140},
        {"lat": 40.4687, "lon": -3.2820},
    ],
    "alcala": [
        {"lat": 40.4865, "lon": -3.3440},
        {"lat": 40.4768, "lon": -3.3140},
        {"lat": 40.4687, "lon": -3.2820},
    ],
    "cabanillas_return": [
        {"lat": 40.6100, "lon": -3.2450},
        {"lat": 40.5700, "lon": -3.2400},
        {"lat": 40.5200, "lon": -3.2500},
        {"lat": 40.4850, "lon": -3.2600},
    ],
}


def _append_point(points: list[dict[str, Any]], lat: Any, lon: Any, label: str = "") -> None:
    try:
        flat = float(lat)
        flon = float(lon)
    except (TypeError, ValueError):
        return
    if points and abs(points[-1]["lat"] - flat) < 0.0002 and abs(points[-1]["lon"] - flon) < 0.0002:
        return
    points.append({"lat": flat, "lon": flon, "label": label})


def build_visual_route_points(segment: str, endpoint: dict[str, Any], recommended: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    _append_point(points, endpoint.get("origin_lat"), endpoint.get("origin_lon"), "Origen")
    hints = ROUTE_ROAD_HINTS.get(segment, [])
    rec_lat, rec_lon = station_lat_lon(recommended) if recommended else (None, None)
    if rec_lat is not None and rec_lon is not None:
        for p in hints:
            _append_point(points, p.get("lat"), p.get("lon"), "")
        _append_point(points, rec_lat, rec_lon, "Repostaje")
    else:
        for p in hints:
            _append_point(points, p.get("lat"), p.get("lon"), "")
    _append_point(points, endpoint.get("destination_lat"), endpoint.get("destination_lon"), "Destino")
    return points


def _svg_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_map_svg(segment: str, result: dict[str, Any], map_payload: dict[str, Any]) -> str:
    width, height = 1200, 760
    map_x, map_y, map_w, map_h = 40, 150, 720, 560
    panel_x, panel_y, panel_w, panel_h = 790, 150, 370, 560
    origin = map_payload.get("origin", {})
    destination = map_payload.get("destination", {})
    markers = map_payload.get("markers", [])
    route_points = map_payload.get("route_points", [])
    geo_points: list[tuple[float, float]] = []
    for p in route_points:
        try:
            geo_points.append((float(p["lat"]), float(p["lon"])))
        except Exception:
            pass
    for m in markers:
        try:
            geo_points.append((float(m["lat"]), float(m["lon"])))
        except Exception:
            pass
    if not geo_points:
        geo_points = [(40.48, -3.34), (40.50, -3.26)]
    lats = [p[0] for p in geo_points]
    lons = [p[1] for p in geo_points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    pad_lat = max((max_lat - min_lat) * 0.18, 0.01)
    pad_lon = max((max_lon - min_lon) * 0.18, 0.01)
    min_lat -= pad_lat; max_lat += pad_lat; min_lon -= pad_lon; max_lon += pad_lon

    def xy(lat: float, lon: float) -> tuple[float, float]:
        x = map_x + (lon - min_lon) / (max_lon - min_lon) * map_w
        y = map_y + (max_lat - lat) / (max_lat - min_lat) * map_h
        return x, y

    route_poly_parts = []
    for p in route_points:
        try:
            x, y = xy(float(p["lat"]), float(p["lon"]))
            route_poly_parts.append(f"{x:.1f},{y:.1f}")
        except Exception:
            pass
    route_poly = " ".join(route_poly_parts)
    recommended = result.get("recommended", {}) if isinstance(result.get("recommended"), dict) else {}
    alternatives = [x for x in result.get("alternatives", []) if isinstance(x, dict)]
    rec_name = recommended.get("station_name") or recommended.get("station_key") or "N/D"
    rec_price = format_price_label(recommended.get("price"))
    rec_addr = recommended.get("address") or ""
    rec_time = recommended.get("official_timestamp") or "N/D"
    alt = alternatives[0] if alternatives else {}
    alt_text = f"{alt.get('station_name') or 'Alternativa'} · {format_price_label(alt.get('price'))}" if alt else ""
    marker_svg: list[str] = []

    def add_marker(lat: Any, lon: Any, color: str, label: str, r: int = 10) -> None:
        try:
            x, y = xy(float(lat), float(lon))
        except Exception:
            return
        marker_svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}" stroke="white" stroke-width="4"/>')
        marker_svg.append(f'<text x="{x+14:.1f}" y="{y-12:.1f}" class="map-label">{_svg_escape(label)}</text>')

    add_marker(origin.get("lat"), origin.get("lon"), "#111827", "Origen", 9)
    for m in markers:
        add_marker(m.get("lat"), m.get("lon"), "#15803d" if m.get("role") == "recommended" else "#d97706", m.get("name") or "", 12 if m.get("role") == "recommended" else 9)
    add_marker(destination.get("lat"), destination.get("lon"), "#111827", "Destino", 9)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <style>
    .title {{ font: 800 34px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#111827; }}
    .subtitle {{ font: 500 18px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#4b5563; }}
    .small {{ font: 500 15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#6b7280; }}
    .label {{ font: 700 17px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#111827; }}
    .map-label {{ font: 800 15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#111827; paint-order:stroke; stroke:#fff; stroke-width:4px; }}
    .price {{ font: 900 46px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#15803d; }}
    .card-title {{ font: 900 22px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; fill:#111827; }}
  </style>
  <rect width="100%" height="100%" fill="#f3f6f8"/>
  <rect x="28" y="24" width="1144" height="96" rx="24" fill="white" stroke="#dbe4ee"/>
  <text x="54" y="66" class="title">Gasolina · {_svg_escape(segment)}</text>
  <text x="54" y="98" class="subtitle">{_svg_escape(origin.get("name"))} → {_svg_escape(destination.get("name"))}</text>
  <rect x="{map_x}" y="{map_y}" width="{map_w}" height="{map_h}" rx="24" fill="#e8f1ec" stroke="#cbd5e1"/>
  <path d="M {map_x+40} {map_y+map_h-95} C {map_x+210} {map_y+map_h-165}, {map_x+390} {map_y+420}, {map_x+map_w-70} {map_y+360}" fill="none" stroke="#d7c9a8" stroke-width="36" stroke-linecap="round" opacity=".75"/>
  <path d="M {map_x+80} {map_y+90} C {map_x+235} {map_y+155}, {map_x+365} {map_y+170}, {map_x+map_w-90} {map_y+70}" fill="none" stroke="#ffffff" stroke-width="18" stroke-linecap="round" opacity=".85"/>
  <path d="M {map_x+110} {map_y+map_h-70} C {map_x+275} {map_y+map_h-235}, {map_x+495} {map_y+map_h-170}, {map_x+map_w-110} {map_y+map_h-250}" fill="none" stroke="#ffffff" stroke-width="15" stroke-linecap="round" opacity=".75"/>
  <polyline points="{route_poly}" fill="none" stroke="#2563eb" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="16 12"/>
  {''.join(marker_svg)}
  <rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="24" fill="white" stroke="#dbe4ee"/>
  <text x="{panel_x+28}" y="{panel_y+45}" class="card-title">Mejor parada</text>
  <text x="{panel_x+28}" y="{panel_y+105}" class="price">{_svg_escape(rec_price)}</text>
  <text x="{panel_x+28}" y="{panel_y+150}" class="card-title">{_svg_escape(rec_name)}</text>
  <text x="{panel_x+28}" y="{panel_y+182}" class="small">{_svg_escape(rec_addr)}</text>
  <text x="{panel_x+28}" y="{panel_y+212}" class="small">Actualizado: {_svg_escape(rec_time)}</text>
  <rect x="{panel_x+24}" y="{panel_y+248}" width="{panel_w-48}" height="96" rx="18" fill="#ecfdf3" stroke="#15803d"/>
  <text x="{panel_x+46}" y="{panel_y+286}" class="label">Recomendación</text>
  <text x="{panel_x+46}" y="{panel_y+318}" class="small">Repostar en {_svg_escape(rec_name)}.</text>
  <rect x="{panel_x+24}" y="{panel_y+372}" width="{panel_w-48}" height="96" rx="18" fill="#fff7ed" stroke="#d97706"/>
  <text x="{panel_x+46}" y="{panel_y+410}" class="label">Alternativa</text>
  <text x="{panel_x+46}" y="{panel_y+442}" class="small">{_svg_escape(alt_text or "Sin alternativa cercana")}</text>
  <text x="{panel_x+28}" y="{panel_y+520}" class="small">Ruta dibujada de forma orientativa por carretera.</text>
  <text x="{panel_x+28}" y="{panel_y+548}" class="small">Para navegación real: Google Maps / Apple Maps.</text>
</svg>"""


def build_map_payload(segment: str, result: dict[str, Any]) -> dict[str, Any]:
    endpoint = ROUTE_ENDPOINTS.get(segment, ROUTE_ENDPOINTS["forus_return"])
    recommended = result.get("recommended") if isinstance(result.get("recommended"), dict) else None
    alternatives = [x for x in result.get("alternatives", []) if isinstance(x, dict)]
    stations = ([recommended] if recommended else []) + alternatives
    markers = [map_marker_from_station(station, "recommended" if idx == 0 else "alternative") for idx, station in enumerate(stations)]
    valid_stations = [station for station in stations if station_lat_lon(station)[0] is not None]
    apple_route = build_apple_directions(endpoint)
    apple_station = build_apple_station_link(recommended)
    recommended_name = recommended.get("station_name") if recommended else "N/D"
    recommended_price = recommended.get("price") if recommended else None
    return {
        "type": "visual_map_links",
        "note": "Informe breve con mapa interactivo, ruta directa a Apple Maps y resumen meteorológico. La ruta del mapa es visual; Google/Apple calculan la navegación real con las direcciones exactas.",
        "summary": f"{endpoint['origin_name']} → {endpoint['destination_name']} · Recomendada: {recommended_name}" + (f" ({recommended_price:.3f} €/l)" if isinstance(recommended_price, (int, float)) else ""),
        "visual_map_url": f"{PUBLIC_BASE_URL}/map?segment={quote_plus(segment)}",
        "apple_maps_route": apple_route,
        "apple_maps_recommended_route": apple_route,
        "apple_maps_recommended_station": apple_station,
        "google_maps_recommended_route": build_google_directions(endpoint, endpoint, valid_stations[:1]),
        "google_maps_all_candidates_route": build_google_directions(endpoint, endpoint, valid_stations),
        "origin": {
            "name": endpoint["origin_name"],
            "address": endpoint.get("origin_address"),
            "lat": endpoint["origin_lat"],
            "lon": endpoint["origin_lon"],
        },
        "destination": {
            "name": endpoint["destination_name"],
            "address": endpoint.get("destination_address"),
            "lat": endpoint["destination_lat"],
            "lon": endpoint["destination_lon"],
        },
        "markers": markers,
        "route_points": build_visual_route_points(segment, endpoint, recommended),
    }



def weather_code_label(code: int | None) -> str:
    labels = {
        0: "cielo despejado",
        1: "poco nuboso",
        2: "nuboso parcial",
        3: "cubierto",
        45: "niebla",
        48: "niebla con escarcha",
        51: "llovizna débil",
        53: "llovizna",
        55: "llovizna intensa",
        61: "lluvia débil",
        63: "lluvia",
        65: "lluvia fuerte",
        71: "nieve débil",
        73: "nieve",
        75: "nieve fuerte",
        80: "chubascos débiles",
        81: "chubascos",
        82: "chubascos fuertes",
        95: "tormenta",
        96: "tormenta con granizo",
        99: "tormenta fuerte con granizo",
    }
    return labels.get(code, "tiempo variable")


def segment_weather_title(segment: str) -> str:
    if segment == "forus_out":
        return "☀️ Ida a Forus"
    if segment == "forus_return":
        return "🌙 Regreso Forus → Anchuelo"
    if segment == "cabanillas_out":
        return "🏢 Ida Anchuelo → DSV/Cabanillas"
    if segment == "cabanillas_return":
        return "🏢 Vuelta DSV/Cabanillas → Anchuelo"
    if segment == "alcala":
        return "🏍️ Trayecto Alcalá"
    return f"🏍️ Trayecto {segment}"


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _weather_emoji(code: int | None, precip_prob: float | None) -> str:
    if code in (95, 96, 99):
        return "⛈️"
    if code in (61, 63, 65, 80, 81, 82) or (precip_prob is not None and precip_prob >= 45):
        return "🌧️"
    if code in (45, 48):
        return "🌫️"
    if code in (2, 3):
        return "🌥️"
    return "🌤️"


async def fetch_route_weather(segment: str, at: datetime | None = None) -> dict[str, Any]:
    """Resumen meteorológico ligero usando Open-Meteo, sin API key.

    Se consulta el punto medio aproximado del trayecto para representar el corredor Henares/Anchuelo.
    """
    endpoint = ROUTE_ENDPOINTS.get(segment, ROUTE_ENDPOINTS["forus_out"])
    lat = (float(endpoint["origin_lat"]) + float(endpoint["destination_lat"])) / 2
    lon = (float(endpoint["origin_lon"]) + float(endpoint["destination_lon"])) / 2
    tz_name = os.environ.get("LOCAL_TZ", "Europe/Madrid")
    target = at or datetime.now(ZoneInfo(tz_name))
    if target.tzinfo is None:
        target = target.replace(tzinfo=ZoneInfo(tz_name))

    params = {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m,relative_humidity_2m",
        "timezone": tz_name,
        "forecast_days": "3",
    }
    url = "https://api.open-meteo.com/v1/forecast"
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=True) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    hourly = data.get("hourly", {}) if isinstance(data, dict) else {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    pops = hourly.get("precipitation_probability") or []
    precs = hourly.get("precipitation") or []
    codes = hourly.get("weather_code") or []
    winds = hourly.get("wind_speed_10m") or []
    hums = hourly.get("relative_humidity_2m") or []

    best_idx = 0
    best_delta = None
    for idx, raw_time in enumerate(times):
        dt = _parse_iso_datetime(raw_time)
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        delta = abs((dt - target).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_idx = idx

    def _get(seq: list[Any], idx: int) -> Any:
        return seq[idx] if isinstance(seq, list) and 0 <= idx < len(seq) else None

    temp = _get(temps, best_idx)
    pop = _get(pops, best_idx)
    precip = _get(precs, best_idx)
    code = _get(codes, best_idx)
    wind = _get(winds, best_idx)
    humidity = _get(hums, best_idx)
    forecast_time = _parse_iso_datetime(_get(times, best_idx))

    return {
        "segment": segment,
        "at": target.isoformat(timespec="minutes"),
        "forecast_time": forecast_time.isoformat(timespec="minutes") if forecast_time else None,
        "temperature": temp,
        "precipitation_probability": pop,
        "precipitation": precip,
        "weather_code": code,
        "weather_label": weather_code_label(int(code)) if code is not None else "tiempo variable",
        "wind_speed": wind,
        "humidity": humidity,
        "emoji": _weather_emoji(int(code) if code is not None else None, float(pop) if pop is not None else None),
    }


def _weather_bullets(weather: dict[str, Any]) -> list[str]:
    temp = weather.get("temperature")
    pop = weather.get("precipitation_probability")
    precip = weather.get("precipitation")
    wind = weather.get("wind_speed")
    humidity = weather.get("humidity")
    label = weather.get("weather_label") or "tiempo variable"

    bullets: list[str] = []
    bullets.append(f"{weather.get('emoji', '🌤️')} {str(label).capitalize()}.")
    if isinstance(temp, (int, float)):
        if temp >= 30:
            bullets.append(f"🌡️ Temperatura alta: {temp:.0f} °C; sensación calurosa en ciudad y semáforos.")
        elif temp >= 24:
            bullets.append(f"🌡️ Ambiente templado-cálido: {temp:.0f} °C.")
        elif temp >= 16:
            bullets.append(f"🌡️ Temperatura agradable: {temp:.0f} °C.")
        else:
            bullets.append(f"🌡️ Fresco para moto: {temp:.0f} °C; conviene algo de abrigo.")
    if isinstance(pop, (int, float)):
        if pop >= 50 or (isinstance(precip, (int, float)) and precip > 0):
            bullets.append(f"🌧️ Riesgo de lluvia relevante: {pop:.0f}%; revisa impermeable.")
        elif pop >= 20:
            bullets.append(f"🌦️ Probabilidad de lluvia baja-moderada: {pop:.0f}%.")
        else:
            bullets.append(f"🌧️ Probabilidad de lluvia muy baja: {pop:.0f}%.")
    if isinstance(wind, (int, float)):
        if wind >= 30:
            bullets.append(f"💨 Viento notable: {wind:.0f} km/h; puede notarse en zonas abiertas.")
        elif wind >= 18:
            bullets.append(f"💨 Viento moderado: {wind:.0f} km/h.")
        else:
            bullets.append(f"💨 Viento flojo: {wind:.0f} km/h.")
    if isinstance(humidity, (int, float)) and humidity >= 85:
        bullets.append(f"🌫️ Humedad alta: {humidity:.0f}%; atención a posible sensación húmeda.")
    return bullets[:5]


def weather_panel_html_from_reports(
    current_segment: str,
    current_weather: dict[str, Any] | None,
    next_segment: str | None,
    next_weather: dict[str, Any] | None,
    decision: dict[str, Any] | None = None,
) -> str:
    def block(title: str, weather: dict[str, Any] | None) -> str:
        if not weather:
            return f"""
            <div class=\"weather-block\">
              <h3>{html.escape(title)}</h3>
              <p class=\"note\">No se pudo obtener la previsión ahora mismo.</p>
            </div>
            """
        at_label = html.escape(str(weather.get("at") or ""))
        bullets = "".join(f"<li>{html.escape(item)}</li>" for item in _weather_bullets(weather))
        result = "✅ Buenas condiciones para moto."
        pop = weather.get("precipitation_probability")
        wind = weather.get("wind_speed")
        temp = weather.get("temperature")
        code = weather.get("weather_code")
        if (isinstance(pop, (int, float)) and pop >= 45) or code in (61, 63, 65, 80, 81, 82, 95, 96, 99):
            result = "⚠️ Llevar impermeable o revisar antes de salir."
        elif isinstance(wind, (int, float)) and wind >= 30:
            result = "⚠️ Ojo con viento en zonas abiertas."
        elif isinstance(temp, (int, float)) and temp >= 30:
            result = "🥵 Buenas condiciones, pero con calor."
        return f"""
        <div class=\"weather-block\">
          <h3>{html.escape(title)}</h3>
          <small>Referencia: {at_label}</small>
          <ul>{bullets}</ul>
          <p class=\"weather-result\">{html.escape(result)}</p>
        </div>
        """

    current_title = segment_weather_title(current_segment)
    next_title = segment_weather_title(next_segment) if next_segment else "Siguiente trayecto"

    decision_text = ""
    if isinstance(decision, dict) and decision.get("reason"):
        decision_text = f"<p class=\"note\"><b>Gasolina:</b> {html.escape(str(decision.get('reason')))}</p>"

    return f"""
      <div class=\"croquis-title\"><h2>Tiempo para los trayectos</h2><span class=\"pill\">Moto</span></div>
      <div class=\"weather-panel\">
        {block(current_title, current_weather)}
        {block(next_title, next_weather) if next_segment else ""}
      </div>
      {decision_text}
    """


async def build_weather_panel_html(
    current_segment: str,
    next_segment: str | None = None,
    calendar_route_plan: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
) -> str:
    current_at = None
    next_at = None
    if isinstance(calendar_route_plan, dict):
        current_at = _parse_iso_datetime((calendar_route_plan.get("current_occurrence") or {}).get("at"))
        next_at = _parse_iso_datetime((calendar_route_plan.get("next_occurrence") or {}).get("at"))
    try:
        current_weather = await fetch_route_weather(current_segment, current_at)
    except Exception as exc:
        print(f"[weather] No se pudo obtener tiempo actual: {type(exc).__name__}: {exc}")
        current_weather = None

    next_weather = None
    if next_segment:
        try:
            next_weather = await fetch_route_weather(next_segment, next_at)
        except Exception as exc:
            print(f"[weather] No se pudo obtener tiempo siguiente: {type(exc).__name__}: {exc}")

    return weather_panel_html_from_reports(current_segment, current_weather, next_segment, next_weather, decision)


def _weather_practical_result(weather: dict[str, Any] | None) -> str:
    if not weather:
        return "Previsión no disponible ahora mismo."
    pop = weather.get("precipitation_probability")
    wind = weather.get("wind_speed")
    temp = weather.get("temperature")
    code = weather.get("weather_code")
    if (isinstance(pop, (int, float)) and pop >= 45) or code in (61, 63, 65, 80, 81, 82, 95, 96, 99):
        return "⚠️ Revisa impermeable antes de salir."
    if isinstance(wind, (int, float)) and wind >= 30:
        return "⚠️ Buenas condiciones, pero ojo con viento en zonas abiertas."
    if isinstance(temp, (int, float)) and temp >= 30:
        return "🥵 Buenas condiciones, pero con calor."
    return "✅ Buenas condiciones para ir en moto."


def weather_text_block(segment: str, weather: dict[str, Any] | None) -> dict[str, Any]:
    title = segment_weather_title(segment)
    if not weather:
        return {
            "segment": segment,
            "title": title,
            "at": None,
            "bullets": ["Previsión no disponible ahora mismo."],
            "result": "Previsión no disponible ahora mismo.",
        }
    return {
        "segment": segment,
        "title": title,
        "at": weather.get("at"),
        "forecast_time": weather.get("forecast_time"),
        "temperature": weather.get("temperature"),
        "precipitation_probability": weather.get("precipitation_probability"),
        "wind_speed": weather.get("wind_speed"),
        "humidity": weather.get("humidity"),
        "weather_label": weather.get("weather_label"),
        "bullets": _weather_bullets(weather),
        "result": _weather_practical_result(weather),
    }


async def build_weather_summary_payload(
    current_segment: str,
    next_segment: str | None = None,
    calendar_route_plan: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_at = None
    next_at = None
    if isinstance(calendar_route_plan, dict):
        current_at = _parse_iso_datetime((calendar_route_plan.get("current_occurrence") or {}).get("at"))
        next_at = _parse_iso_datetime((calendar_route_plan.get("next_occurrence") or {}).get("at"))

    current_weather = None
    next_weather = None
    try:
        current_weather = await fetch_route_weather(current_segment, current_at)
    except Exception as exc:
        print(f"[weather] No se pudo obtener resumen actual: {type(exc).__name__}: {exc}")

    if next_segment:
        try:
            next_weather = await fetch_route_weather(next_segment, next_at)
        except Exception as exc:
            print(f"[weather] No se pudo obtener resumen siguiente: {type(exc).__name__}: {exc}")

    current_block = weather_text_block(current_segment, current_weather)
    next_block = weather_text_block(next_segment, next_weather) if next_segment else None

    lines: list[str] = []
    lines.append("Trayectos previstos:")
    current_endpoint = ROUTE_ENDPOINTS.get(current_segment, ROUTE_ENDPOINTS["forus_out"])
    lines.append(f"🏍️ {current_endpoint['origin_name']} → {current_endpoint['destination_name']}")
    if next_segment:
        next_endpoint = ROUTE_ENDPOINTS.get(next_segment, ROUTE_ENDPOINTS["forus_return"])
        lines.append(f"🏍️ {next_endpoint['origin_name']} → {next_endpoint['destination_name']}")

    for block in [current_block, next_block]:
        if not block:
            continue
        lines.append("")
        lines.append(str(block["title"]))
        for item in block.get("bullets", []):
            lines.append(f"* {item}")
        lines.append("")
        lines.append("Resultado práctico")
        lines.append(str(block.get("result") or ""))

    if isinstance(decision, dict) and decision.get("reason"):
        lines.append("")
        lines.append("Gasolina")
        lines.append(str(decision.get("reason")))

    return {
        "current": current_block,
        "next": next_block,
        "summary_text": "\n".join(lines),
    }

def render_visual_map_html(segment: str, result: dict[str, Any], map_payload: dict[str, Any], weather_panel_html: str | None = None) -> str:
    markers_json = json.dumps(map_payload.get("markers", []), ensure_ascii=False)
    origin_json = json.dumps(map_payload.get("origin", {}), ensure_ascii=False)
    destination_json = json.dumps(map_payload.get("destination", {}), ensure_ascii=False)
    route_points_json = json.dumps(map_payload.get("route_points", []), ensure_ascii=False)
    recommended = result.get("recommended", {}) if isinstance(result.get("recommended"), dict) else {}
    alternatives = [x for x in result.get("alternatives", []) if isinstance(x, dict)]
    title = f"Gasolina — {segment}"
    subtitle = f"{map_payload.get('origin', {}).get('name', 'Origen')} → {map_payload.get('destination', {}).get('name', 'Destino')}"
    google_recommended_link = html.escape(map_payload.get("google_maps_recommended_route", ""))
    google_all_link = html.escape(map_payload.get("google_maps_all_candidates_route", ""))
    apple_link = html.escape(map_payload.get("apple_maps_route") or map_payload.get("apple_maps_recommended_station", ""))
    if weather_panel_html is None:
        weather_panel_html = """
        <div class=\"croquis-title\"><h2>Tiempo para los trayectos</h2><span class=\"pill\">Moto</span></div>
        <p class=\"note\">Previsión no disponible en este momento.</p>
        """

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
    .weather-panel {{ display:grid; grid-template-columns:1fr; gap:10px; }}
    .weather-block {{ border:1px solid #dbe4ee; border-radius:15px; padding:12px; background:#f9fafb; }}
    .weather-block h3 {{ margin:0 0 4px; font-size:16px; }}
    .weather-block small {{ color:var(--muted); }}
    .weather-block ul {{ margin:8px 0 0; padding-left:18px; color:var(--ink); font-size:13px; line-height:1.35; }}
    .weather-block li {{ margin:4px 0; }}
    .weather-result {{ margin:10px 0 0; font-weight:800; }}
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
        {weather_panel_html}
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
const visualRoutePoints = {route_points_json};
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
if (visualRoutePoints.length >= 2) {{
  const visualLatLngs = visualRoutePoints.map(p => [p.lat, p.lon]).filter(p => p[0] != null && p[1] != null);
  L.polyline(visualLatLngs, {{color:'#2563eb', weight:5, opacity:.78, dashArray:'10,10'}}).addTo(map);
}} else if (routePoints.length >= 2) {{
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


def normalize_calendar_feed_url(url: str) -> str:
    """Convierte webcal:// y Google embed en una URL descargable por httpx."""
    value = (url or "").strip()
    if not value:
        return ""

    lowered = value.lower()
    if lowered.startswith("webcal://"):
        return "https://" + value[len("webcal://"):]

    # Google Calendar embed: https://calendar.google.com/calendar/embed?src=...&ctz=...
    # Solo funcionará si el calendario tiene ICS público. Los iCloud webcal son preferibles.
    parsed = urlparse(value)
    if "calendar.google.com" in parsed.netloc and parsed.path.rstrip("/") == "/calendar/embed":
        src = parse_qs(parsed.query).get("src", [""])[0]
        if src:
            return "https://calendar.google.com/calendar/ical/" + quote_plus(unquote(src)) + "/public/basic.ics"

    return value


def public_calendar_ics_urls() -> list[str]:
    urls: list[str] = []

    # 1) Variables de entorno, si existen.
    urls.extend(split_env_list("PUBLIC_CALENDAR_ICS_URLS"))
    for key in (
        "FORUS_CALENDAR_ICS_URL",
        "PERSONAL_CALENDAR_ICS_URL",
        "FORUS_CALENDAR_WEBCAL_URL",
        "PERSONAL_CALENDAR_WEBCAL_URL",
        "FORUS_CALENDAR_EMBED_URL",
        "PERSONAL_CALENDAR_EMBED_URL",
        "GOOGLE_FORUS_ICS_URL",
        "GOOGLE_PERSONAL_ICS_URL",
    ):
        value = os.getenv(key, "").strip()
        if value:
            urls.append(value)

    # 2) Valores por defecto: calendarios iCloud publicados indicados por Christian.
    if not urls:
        urls.extend(DEFAULT_PUBLIC_CALENDAR_ICS_URLS)

    # Normaliza y deduplica manteniendo orden.
    seen: set[str] = set()
    result: list[str] = []
    for raw_url in urls:
        url = normalize_calendar_feed_url(raw_url)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def calendar_name_from_url(url: str, index: int) -> str:
    lowered = url.lower()

    if "jgp0bfn3vu2iacqjb08rec5t4pd9508i" in lowered or "fbckuovxbrvbepq0imcc7twql9r0t9cylix3mpyrwommi" in lowered:
        return "forus"
    if "3nb1ihtbco4lla5s1s5o5mao24kj947e" in lowered or "v823o3snidotaxorjqagvcp4yd6inmhilp07vthqkweyd5rlttoisansyjcvrrxa" in lowered:
        return "personal_trabajo"

    # Fallback por nombre de variable/URL cuando se sobreescriba en Render.
    if "forus" in lowered:
        return "forus"
    if "personal" in lowered or "trabajo" in lowered or "work" in lowered:
        return "personal_trabajo"

    return f"ics_{index}"


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


async def fetch_public_calendar_events_for_today_with_sources() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    urls = public_calendar_ics_urls()
    if not urls:
        return [], []

    tz = local_tz()
    today = datetime.now(tz).date()
    start_day = datetime.combine(today, datetime.min.time(), tzinfo=tz)
    end_day = start_day + timedelta(days=1)

    events: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    timeout = httpx.Timeout(12.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for idx, url in enumerate(urls, start=1):
            parsed_url = urlparse(url)
            source_debug: dict[str, Any] = {
                "index": idx,
                "calendar": calendar_name_from_url(url, idx),
                "url_scheme": parsed_url.scheme,
                "host": parsed_url.netloc,
                "ok": False,
                "status_code": None,
                "raw_vevent_count": 0,
                "parsed_events_count": 0,
                "events_today_count": 0,
                "error": None,
            }
            try:
                resp = await client.get(url)
                source_debug["status_code"] = resp.status_code
                resp.raise_for_status()

                raw_text = resp.text
                source_debug["raw_vevent_count"] = raw_text.upper().count("BEGIN:VEVENT")
                parsed = parse_ics_events(raw_text, calendar_name=calendar_name_from_url(url, idx))
                source_debug["parsed_events_count"] = len(parsed)

                source_today = 0
                for event in parsed:
                    event_start = event.get("start")
                    event_end = event.get("end") or event_start
                    if event_start and event_end and event_start < end_day and event_end >= start_day:
                        events.append(event)
                        source_today += 1

                source_debug["events_today_count"] = source_today
                source_debug["ok"] = True
            except Exception as exc:
                source_debug["error"] = f"{type(exc).__name__}: {exc}"
                print(f"[calendar] No se pudo leer ICS {idx}: {type(exc).__name__}: {exc}")
            finally:
                sources.append(source_debug)

    return sorted(events, key=lambda ev: ev.get("start") or datetime.max.replace(tzinfo=tz)), sources


async def fetch_public_calendar_events_for_today() -> list[dict[str, Any]]:
    events, _sources = await fetch_public_calendar_events_for_today_with_sources()
    return events


def serialize_calendar_event(event: dict[str, Any]) -> dict[str, Any]:
    start = event.get("start")
    end = event.get("end")
    return {
        "calendar": event.get("calendar", ""),
        "summary": event.get("summary", ""),
        "location": event.get("location", ""),
        "start": start.isoformat(timespec="minutes") if isinstance(start, datetime) else None,
        "end": end.isoformat(timespec="minutes") if isinstance(end, datetime) else None,
    }


async def calendar_auto_debug() -> dict[str, Any]:
    events, sources = await fetch_public_calendar_events_for_today_with_sources()
    calendar_segment = classify_segment_from_calendar_events(events)
    return {
        "enabled": env_bool("PUBLIC_CALENDAR_ENABLED", True),
        "urls_count": len(public_calendar_ics_urls()),
        "events_today_count": len(events),
        "calendar_segment": calendar_segment,
        "fallback_segment": fallback_auto_segment(),
        "sources": sources,
        "events_today": [serialize_calendar_event(event) for event in events[:20]],
    }


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

    upcoming_cabanillas = [event for event in cabanillas_events if (event.get("start") and event["start"] > now)]
    if upcoming_cabanillas:
        upcoming_cabanillas.sort(key=lambda ev: ev["start"])
        return "cabanillas_out"

    finished_cabanillas = [
        event for event in cabanillas_events
        if event.get("end") and event.get("start") and event["end"] > event["start"] and event["end"] <= now
    ]
    if finished_cabanillas:
        return "cabanillas_return"

    if alcala_events:
        return "alcala"

    return None


def fallback_auto_segment() -> str:
    today = date.today()
    return "forus_return" if today.weekday() < 5 else "alcala"


async def resolve_auto_segment_info(segment: str) -> tuple[str, dict[str, Any] | None]:
    if segment != "auto":
        return segment, None

    debug: dict[str, Any] = {
        "enabled": env_bool("PUBLIC_CALENDAR_ENABLED", True),
        "urls_count": len(public_calendar_ics_urls()),
        "events_today_count": 0,
        "calendar_segment": None,
        "fallback_segment": fallback_auto_segment(),
        "decision_source": "no_route",
    }

    if env_bool("PUBLIC_CALENDAR_ENABLED", True):
        events = await fetch_public_calendar_events_for_today()
        calendar_segment = classify_segment_from_calendar_events(events)
        debug.update(
            {
                "events_today_count": len(events),
                "calendar_segment": calendar_segment,
                "events_today": [serialize_calendar_event(event) for event in events[:10]],
            }
        )

        # Para el informe automático, priorizamos el próximo trayecto real con fecha/hora
        # frente a la clasificación genérica del día. Así Cabanillas a las 09:00 se
        # publica como cabanillas_out antes de ir, no como vuelta ni como Forus posterior.
        plan = await resolve_current_and_next_segments_from_calendar()
        debug["calendar_route_plan"] = plan
        current_segment = plan.get("current_segment")
        if isinstance(current_segment, str) and current_segment in ROUTE_ENDPOINTS:
            debug["decision_source"] = "calendar_next_occurrence"
            return current_segment, debug

        # Si no hay próximos eventos relevantes (oficina/Cabanillas, casa/Anchuelo,
        # Forus o Alcalá, según keywords), no inventamos ruta por fallback.
        if env_bool("NO_ROUTE_WHEN_NO_CALENDAR_OCCURRENCE", True):
            debug["decision_source"] = "no_calendar_occurrence"
            return "no_route", debug

        if calendar_segment:
            debug["decision_source"] = "calendar_today"
            return calendar_segment, debug

    if env_bool("NO_ROUTE_WHEN_NO_CALENDAR_OCCURRENCE", True):
        return "no_route", debug

    debug["decision_source"] = "fallback"
    return fallback_auto_segment(), debug

async def resolve_auto_segment(segment: str) -> str:
    resolved_segment, _debug = await resolve_auto_segment_info(segment)
    return resolved_segment


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug-calendar")
async def debug_calendar() -> dict[str, Any]:
    return {
        "status": "ok",
        **(await calendar_auto_debug()),
    }


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
                **({"auto_debug": auto_debug} if requested_segment == "auto" else {}),
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
    requested_segment = segment
    segment, auto_debug = await resolve_auto_segment_info(segment)
    if segment == "no_route":
        return {
            "status": "no_route",
            "source": source,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "segment": segment,
            "message": "No hay trayectos próximos relevantes en el calendario.",
            **({"auto_debug": auto_debug} if requested_segment == "auto" else {}),
        }
    precioil_error: Any = None

    if source in ("precioil", "auto"):
        try:
            payload, rows = await fetch_precioil_relevant_rows(segment)
            save_observations(rows)
            result = choose_best(rows, segment)
            items = payload.get("items", []) if isinstance(payload, dict) else precioil_station_items(payload)

            # Si el usuario pide segment=auto, hacemos una doble evaluación:
            # 1) trayecto inmediato, que se publica como informe principal;
            # 2) trayecto posterior, que solo se usa para decidir si merece esperar.
            if requested_segment == "auto" and env_bool("DUAL_ROUTE_EVALUATION_ENABLED", True):
                next_segment, calendar_route_plan = await next_segment_from_calendar_or_fallback(segment)
                next_payload, next_rows = await fetch_precioil_relevant_rows(next_segment)
                save_observations(next_rows)
                next_result = choose_best(next_rows, next_segment)
                next_items = next_payload.get("items", []) if isinstance(next_payload, dict) else precioil_station_items(next_payload)
                comparison = wait_analysis(result, next_result)
                current_map = build_map_payload(segment, result)
                next_map = build_map_payload(next_segment, next_result)
                weather_summary = await build_weather_summary_payload(segment, next_segment, calendar_route_plan, comparison)
                current_map["weather_summary"] = weather_summary
                return {
                    "status": "ok",
                    "source": "precioil",
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "segment": segment,
                    "current_segment": segment,
                    "next_segment": next_segment,
                    "analysis_mode": "dual_current_and_next_calendar_based",
                    "published_route": "current_segment",
                    "calendar_route_plan": calendar_route_plan,
                    "decision": comparison,
                    "weather_summary": weather_summary,
                    "current_route": {
                        "segment": segment,
                        "regions_used": precioil_regions_for_segment(segment),
                        "precioil_matched_count": len(rows),
                        "precioil_raw_items_count": len(items),
                        "region_errors": payload.get("region_errors", {}) if isinstance(payload, dict) else {},
                        "result": result,
                        "map": current_map,
                    },
                    "next_route": {
                        "segment": next_segment,
                        "regions_used": precioil_regions_for_segment(next_segment),
                        "precioil_matched_count": len(next_rows),
                        "precioil_raw_items_count": len(next_items),
                        "region_errors": next_payload.get("region_errors", {}) if isinstance(next_payload, dict) else {},
                        "result": next_result,
                        "map": next_map,
                    },
                    **result,
                    "map": current_map,
                    **({"auto_debug": auto_debug} if requested_segment == "auto" else {}),
                }

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
                "weather_summary": await build_weather_summary_payload(segment),
                **({"auto_debug": auto_debug} if requested_segment == "auto" else {}),
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
                    **({"auto_debug": auto_debug} if requested_segment == "auto" else {}),
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
                **({"auto_debug": auto_debug} if requested_segment == "auto" else {}),
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
                    **({"auto_debug": auto_debug} if requested_segment == "auto" else {}),
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
                **({"auto_debug": auto_debug} if requested_segment == "auto" else {}),
                "recommended": manual_fallback(segment),
                "alternatives": [],
                "warning": "Fallaron Precioil y fuente oficial.",
                "map": build_map_payload(segment, {"recommended": manual_fallback(segment), "alternatives": []}),
            }

    raise HTTPException(status_code=400, detail="source debe ser precioil, official o auto")


@app.get("/map-image")
async def map_image(
    segment: str = Query(default="auto"),
    source: str = Query(default="precioil"),
) -> Response:
    requested_segment = segment
    segment = await resolve_auto_segment(segment)
    if segment == "no_route":
        raise HTTPException(status_code=404, detail="No hay trayectos próximos relevantes en el calendario.")

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
    svg = render_map_svg(segment, result, map_payload)
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/map", response_class=HTMLResponse)
async def map_view(
    segment: str = Query(default="auto"),
    source: str = Query(default="precioil"),
) -> HTMLResponse:
    requested_segment = segment
    segment = await resolve_auto_segment(segment)
    if segment == "no_route":
        return HTMLResponse(
            "<html><body><h1>Sin trayecto próximo</h1><p>No hay trayectos próximos relevantes en el calendario.</p></body></html>",
            status_code=404,
        )

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
    calendar_route_plan = await resolve_current_and_next_segments_from_calendar() if env_bool("PUBLIC_CALENDAR_ENABLED", True) else {}
    next_segment = calendar_route_plan.get("next_segment") if isinstance(calendar_route_plan, dict) else None
    if not isinstance(next_segment, str) or next_segment not in ROUTE_ENDPOINTS or next_segment == segment:
        next_segment = next_segment_for(segment)
    weather_panel = await build_weather_panel_html(segment, next_segment, calendar_route_plan)
    return HTMLResponse(render_visual_map_html(segment, result, map_payload, weather_panel))

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
