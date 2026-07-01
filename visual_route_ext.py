from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

_ROUTE_CACHE: dict[str, list[dict[str, Any]]] = {}


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _coord_key(lat: Any, lon: Any) -> str:
    try:
        return f"{float(lat):.5f},{float(lon):.5f}"
    except Exception:
        return ""


def _append_coord(coords: list[tuple[float, float]], lat: Any, lon: Any) -> None:
    try:
        flat = float(lat)
        flon = float(lon)
    except Exception:
        return
    if coords and abs(coords[-1][0] - flat) < 0.00005 and abs(coords[-1][1] - flon) < 0.00005:
        return
    coords.append((flat, flon))


def _sample_points(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if len(points) <= max_points:
        return points
    max_points = max(2, int(max_points or 2))
    idxs = [round(i * (len(points) - 1) / (max_points - 1)) for i in range(max_points)]
    sampled: list[dict[str, Any]] = []
    seen: set[int] = set()
    for idx in idxs:
        if idx in seen:
            continue
        seen.add(idx)
        sampled.append(points[idx])
    return sampled


def _fetch_osrm_points(coords: list[tuple[float, float]], labels: list[str]) -> list[dict[str, Any]]:
    if len(coords) < 2:
        return []
    base = os.getenv("VISUAL_ROUTE_OSRM_BASE_URL", os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org")).rstrip("/")
    timeout = float(os.getenv("VISUAL_ROUTE_OSRM_TIMEOUT_SECONDS", "7"))
    max_points = int(os.getenv("VISUAL_ROUTE_MAX_POINTS", "120"))
    coord_text = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    query = urllib.parse.urlencode({
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "false",
        "steps": "false",
    })
    url = f"{base}/route/v1/driving/{coord_text}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "gasolina-api-christian/visual-route/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    routes = payload.get("routes") if isinstance(payload, dict) else None
    route = routes[0] if isinstance(routes, list) and routes else {}
    geometry = (route.get("geometry") or {}).get("coordinates") or []
    points = [
        {"lat": float(lat), "lon": float(lon), "label": "", "source": "osrm"}
        for lon, lat in geometry
        if lon is not None and lat is not None
    ]
    if len(points) < 2:
        return []
    points = _sample_points(points, max_points)
    # Mantiene etiquetas mínimas para que el payload siga siendo legible.
    if points:
        points[0]["label"] = labels[0] if labels else "Origen"
        points[-1]["label"] = labels[-1] if labels else "Destino"
    return points


def install(main_module: Any) -> None:
    if getattr(main_module, "_visual_route_ext_installed", False):
        return

    original_build_visual_route_points = getattr(main_module, "build_visual_route_points", None)
    original_render_visual_map_html = getattr(main_module, "render_visual_map_html", None)

    def road_visual_route_points(segment: str, endpoint: dict[str, Any], recommended: Any = None) -> list[dict[str, Any]]:
        if not _env_bool("VISUAL_ROUTE_OSRM_ENABLED", True):
            if callable(original_build_visual_route_points):
                return original_build_visual_route_points(segment, endpoint, recommended)
            return []

        coords: list[tuple[float, float]] = []
        labels: list[str] = []
        _append_coord(coords, endpoint.get("origin_lat"), endpoint.get("origin_lon"))
        labels.append("Origen")

        try:
            rec_lat, rec_lon = main_module.station_lat_lon(recommended) if recommended else (None, None)
        except Exception:
            rec_lat, rec_lon = None, None
        if rec_lat is not None and rec_lon is not None:
            _append_coord(coords, rec_lat, rec_lon)
            labels.append("Repostaje")

        _append_coord(coords, endpoint.get("destination_lat"), endpoint.get("destination_lon"))
        labels.append("Destino")

        cache_key = "|".join([segment] + [_coord_key(lat, lon) for lat, lon in coords])
        cached = _ROUTE_CACHE.get(cache_key)
        if cached:
            return [dict(p) for p in cached]

        try:
            points = _fetch_osrm_points(coords, labels)
            if points:
                _ROUTE_CACHE[cache_key] = [dict(p) for p in points]
                return points
        except Exception as exc:
            print(f"[visual-route] OSRM no disponible para {segment}; uso fallback: {type(exc).__name__}: {exc}")

        if callable(original_build_visual_route_points):
            return original_build_visual_route_points(segment, endpoint, recommended)
        return []

    def render_visual_map_html(segment: str, result: dict[str, Any], map_payload: dict[str, Any], weather_panel_html: str | None = None) -> str:
        if not callable(original_render_visual_map_html):
            return ""
        page = original_render_visual_map_html(segment, result, map_payload, weather_panel_html)
        # La geometría ya viene siguiendo carretera; la hacemos sólida y más natural visualmente.
        page = page.replace("color:'#2563eb', weight:5, opacity:.78, dashArray:'10,10'", "color:'#2563eb', weight:6, opacity:.84")
        page = page.replace("color:'#2563eb', weight:4, opacity:.75, dashArray:'8,8'", "color:'#2563eb', weight:5, opacity:.78")
        page = page.replace("Mapa orientativo: para navegación real usa Google Maps o Apple Maps.", "Mapa visual con trazado aproximado por carretera. Para navegación real usa Google Maps o Apple Maps.")
        return page

    main_module.build_visual_route_points = road_visual_route_points
    main_module.render_visual_map_html = render_visual_map_html
    setattr(main_module, "_visual_route_ext_installed", True)
    print("[visual-route] Trazado visual OSRM instalado")
