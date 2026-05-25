from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Query, HTTPException

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


def resolve_auto_segment(segment: str) -> str:
    if segment != "auto":
        return segment
    today = date.today()
    return "forus_return" if today.weekday() < 5 else "forus_return"


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
    segment = resolve_auto_segment(segment)
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
            }

    raise HTTPException(status_code=400, detail="source debe ser precioil, official o auto")

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
