
from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Query

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "gasolina_history.sqlite3"

app = FastAPI(title="Gasolina Christian API", version="1.0.0")


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
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def get_field(row: dict[str, Any], *names: str) -> str:
    for n in names:
        if n in row and row[n]:
            return str(row[n])
    return ""


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
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(cfg["official_endpoint"])
        r.raise_for_status()
        return r.json()


def station_matches(row: dict[str, Any], station_cfg: dict[str, Any]) -> bool:
    municipio = norm(get_field(row, "Municipio"))
    rotulo = norm(get_field(row, "Rótulo", "Rotulo"))
    direccion = norm(get_field(row, "Dirección", "Direccion"))
    provincia = norm(get_field(row, "Provincia"))

    wanted_municipality = norm(station_cfg.get("municipality", ""))
    if wanted_municipality and wanted_municipality not in municipio:
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
                "raw": r
            })
    return found


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
                json.dumps(r["raw"], ensure_ascii=False)
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

    # Penalizaciones prácticas: no solo precio.
    def score(r: dict[str, Any]) -> float:
        p = float(r["price"])
        key = r["station_key"]
        penalty = 0.0
        if key == "alcampo_dehesa":
            penalty += 0.025  # por discrepancia presencial previa
        if key == "family_energy_azuqueca" and segment != "cabanillas_return":
            penalty += 0.10
        if key == "ballenoil_varsovia" and segment in ("forus_out", "forus_return", "alcala"):
            penalty -= 0.005  # validación presencial y ruta cómoda
        return p + penalty

    best = sorted(candidates, key=score)[0]
    alternatives = sorted(candidates, key=score)[1:4]
    return {"segment": segment, "recommended": best, "alternatives": alternatives}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/prices")
async def prices(save: bool = Query(default=True)) -> dict[str, Any]:
    payload = await fetch_official_data()
    rows = extract_relevant_rows(payload)
    if save:
        save_observations(rows)
    return {
        "official_timestamp": payload.get("Fecha", ""),
        "count": len(rows),
        "stations": rows
    }


@app.get("/recommend")
async def recommend(segment: str = Query(default="auto")) -> dict[str, Any]:
    # segment: auto | cabanillas_return | forus_return | forus_out | alcala
    payload = await fetch_official_data()
    rows = extract_relevant_rows(payload)
    save_observations(rows)

    if segment == "auto":
        today = date.today()
        # L-V: durante el día damos prioridad a los tramos reales habituales.
        # Esto puede enriquecerse con ICS de calendarios en una versión 2.
        segment = "forus_return" if today.weekday() < 5 else "forus_return"

    result = choose_best(rows, segment)
    return {
        "official_timestamp": payload.get("Fecha", ""),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "segment": segment,
        **result
    }


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
