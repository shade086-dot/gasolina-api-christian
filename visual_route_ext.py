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
    timeout = float(os.getenv("VISUAL_ROUTE_OSRM_TIMEOUT_SECONDS", "2.5"))
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
    if points:
        points[0]["label"] = labels[0] if labels else "Origen"
        points[-1]["label"] = labels[-1] if labels else "Destino"
    return points


_CLIENT_OSRM_SCRIPT = r"""
async function improveVisualRouteWithOsrm() {
  try {
    if (!Array.isArray(visualRoutePoints) || visualRoutePoints.length < 2 || typeof L === 'undefined') return;
    const seen = new Set();
    const waypoints = [];
    for (const p of visualRoutePoints) {
      const lat = Number(p.lat), lon = Number(p.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const key = lat.toFixed(5) + ',' + lon.toFixed(5);
      if (seen.has(key)) continue;
      seen.add(key);
      waypoints.push([lat, lon]);
      if (waypoints.length >= 20) break;
    }
    if (waypoints.length < 2) return;
    const coords = waypoints.map(p => p[1].toFixed(6) + ',' + p[0].toFixed(6)).join(';');
    const url = 'https://router.project-osrm.org/route/v1/driving/' + coords + '?overview=full&geometries=geojson&alternatives=false&steps=false';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4500);
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    if (!response.ok) return;
    const data = await response.json();
    const geometry = data && data.routes && data.routes[0] && data.routes[0].geometry && data.routes[0].geometry.coordinates;
    if (!Array.isArray(geometry) || geometry.length < 2) return;
    const latlngs = geometry.map(c => [c[1], c[0]]).filter(c => Number.isFinite(c[0]) && Number.isFinite(c[1]));
    if (latlngs.length < 2) return;
    if (window.visualRouteLine && typeof window.visualRouteLine.setLatLngs === 'function') {
      window.visualRouteLine.setLatLngs(latlngs);
    } else {
      window.visualRouteLine = L.polyline(latlngs, {color:'#2563eb', weight:6, opacity:.84}).addTo(map);
    }
    const mapDistance = document.getElementById('mapDistance');
    if (mapDistance && !mapDistance.textContent.includes('carretera')) {
      mapDistance.textContent = mapDistance.textContent + ' · trazado carretera';
    }
  } catch (error) {
    console.log('OSRM visual fallback', error && error.message ? error.message : error);
  }
}
improveVisualRouteWithOsrm();
"""


def install(main_module: Any) -> None:
    if getattr(main_module, "_visual_route_ext_installed", False):
        return

    original_build_visual_route_points = getattr(main_module, "build_visual_route_points", None)
    original_render_visual_map_html = getattr(main_module, "render_visual_map_html", None)

    def road_visual_route_points(segment: str, endpoint: dict[str, Any], recommended: Any = None) -> list[dict[str, Any]]:
        # Importante: no bloqueamos la respuesta del servidor con OSRM.
        # El mapa carga primero con el trazado rápido y el navegador mejora la línea en segundo plano.
        if not _env_bool("VISUAL_ROUTE_SERVER_OSRM_ENABLED", False):
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
            print(f"[visual-route] OSRM servidor no disponible para {segment}; uso fallback: {type(exc).__name__}: {exc}")

        if callable(original_build_visual_route_points):
            return original_build_visual_route_points(segment, endpoint, recommended)
        return []

    def render_visual_map_html(segment: str, result: dict[str, Any], map_payload: dict[str, Any], weather_panel_html: str | None = None) -> str:
        if not callable(original_render_visual_map_html):
            return ""
        page = original_render_visual_map_html(segment, result, map_payload, weather_panel_html)
        # La página carga rápido con fallback; luego el navegador sustituye la línea por carretera si OSRM responde.
        page = page.replace("color:'#2563eb', weight:5, opacity:.78, dashArray:'10,10'", "color:'#2563eb', weight:6, opacity:.84")
        page = page.replace("color:'#2563eb', weight:4, opacity:.75, dashArray:'8,8'", "color:'#2563eb', weight:5, opacity:.78")
        page = page.replace(
            "L.polyline(visualLatLngs, {color:'#2563eb', weight:6, opacity:.84}).addTo(map);",
            "window.visualRouteLine = L.polyline(visualLatLngs, {color:'#2563eb', weight:6, opacity:.84}).addTo(map);",
        )
        page = page.replace(
            "L.polyline(routePoints.slice(0,3), {color:'#2563eb', weight:5, opacity:.78}).addTo(map);",
            "window.visualRouteLine = L.polyline(routePoints.slice(0,3), {color:'#2563eb', weight:5, opacity:.78}).addTo(map);",
        )
        page = page.replace(
            "Mapa orientativo: para navegación real usa Google Maps o Apple Maps.",
            "Mapa visual: carga rápido y mejora el trazado por carretera en segundo plano. Para navegación real usa Google Maps o Apple Maps.",
        )
        if "improveVisualRouteWithOsrm" not in page:
            page = page.replace("</script>", _CLIENT_OSRM_SCRIPT + "\n</script>", 1)
        return page

    main_module.build_visual_route_points = road_visual_route_points
    main_module.render_visual_map_html = render_visual_map_html
    setattr(main_module, "_visual_route_ext_installed", True)
    print("[visual-route] Trazado visual cliente instalado: carga rápida + OSRM en navegador")
