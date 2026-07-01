from __future__ import annotations

import json
import os
import re
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


def _append_point(points: list[dict[str, Any]], lat: Any, lon: Any, label: str) -> None:
    try:
        flat = float(lat)
        flon = float(lon)
    except Exception:
        return
    if points and abs(points[-1]["lat"] - flat) < 0.00005 and abs(points[-1]["lon"] - flon) < 0.00005:
        return
    points.append({"lat": flat, "lon": flon, "label": label, "source": "essential"})


def _minimal_route_points(main_module: Any, endpoint: dict[str, Any], recommended: Any = None) -> list[dict[str, Any]]:
    """Trazado rápido y limpio: origen → parada recomendada → destino.

    No usa puntos intermedios hardcodeados para evitar que OSRM o Leaflet dibujen
    ida-vuelta-ida-vuelta cuando una pista queda antes/después de la gasolinera.
    """
    points: list[dict[str, Any]] = []
    _append_point(points, endpoint.get("origin_lat"), endpoint.get("origin_lon"), "Origen")
    try:
        rec_lat, rec_lon = main_module.station_lat_lon(recommended) if recommended else (None, None)
    except Exception:
        rec_lat, rec_lon = None, None
    if rec_lat is not None and rec_lon is not None:
        _append_point(points, rec_lat, rec_lon, "Repostaje")
    _append_point(points, endpoint.get("destination_lat"), endpoint.get("destination_lon"), "Destino")
    return points


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
    if (typeof L === 'undefined') return;
    const waypoints = [];
    const seen = new Set();
    function pushWaypoint(lat, lon) {
      lat = Number(lat); lon = Number(lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const key = lat.toFixed(5) + ',' + lon.toFixed(5);
      if (seen.has(key)) return;
      seen.add(key);
      waypoints.push([lat, lon]);
    }

    // La ruta correcta debe ser SOLO: origen → gasolinera recomendada → destino.
    // No usamos puntos visuales intermedios porque pueden provocar zig-zags.
    pushWaypoint(origin && origin.lat, origin && origin.lon);
    if (recommended) pushWaypoint(recommended.lat, recommended.lon);
    pushWaypoint(destination && destination.lat, destination && destination.lon);

    if (waypoints.length < 2) return;
    const coords = waypoints.map(p => p[1].toFixed(6) + ',' + p[0].toFixed(6)).join(';');
    const url = '/visual-route-osrm?coords=' + encodeURIComponent(coords);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5500);
    const response = await fetch(url, { signal: controller.signal, cache: 'no-store' });
    clearTimeout(timer);
    if (!response.ok) return;
    const data = await response.json();
    const geometry = data && data.routes && data.routes[0] && data.routes[0].geometry && data.routes[0].geometry.coordinates;
    if (!Array.isArray(geometry) || geometry.length < 2) return;
    const latlngs = geometry.map(c => [c[1], c[0]]).filter(c => Number.isFinite(c[0]) && Number.isFinite(c[1]));
    if (latlngs.length < 2) return;
    if (window.visualRouteLine && typeof window.visualRouteLine.setLatLngs === 'function') {
      window.visualRouteLine.setLatLngs(latlngs);
      if (typeof window.visualRouteLine.setStyle === 'function') window.visualRouteLine.setStyle({color:'#2563eb', weight:6, opacity:.84, dashArray:null});
    } else {
      window.visualRouteLine = L.polyline(latlngs, {color:'#2563eb', weight:6, opacity:.84}).addTo(map);
    }
    const bounds = L.latLngBounds(latlngs);
    if (bounds && bounds.isValid && bounds.isValid()) map.fitBounds(bounds, { padding: [35, 35] });
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


def _insert_before_last_script_close(page: str, script: str) -> str:
    idx = page.rfind("</script>")
    if idx == -1:
        return page
    return page[:idx] + script + "\n" + page[idx:]


def install(main_module: Any) -> None:
    if getattr(main_module, "_visual_route_ext_installed", False):
        return

    original_render_visual_map_html = getattr(main_module, "render_visual_map_html", None)

    def road_visual_route_points(segment: str, endpoint: dict[str, Any], recommended: Any = None) -> list[dict[str, Any]]:
        # Por defecto no bloqueamos el servidor con OSRM. Se dibuja rápido la línea limpia
        # origen → parada → destino, y el navegador la mejora con carretera real.
        if not _env_bool("VISUAL_ROUTE_SERVER_OSRM_ENABLED", False):
            return _minimal_route_points(main_module, endpoint, recommended)

        points = _minimal_route_points(main_module, endpoint, recommended)
        coords: list[tuple[float, float]] = []
        labels: list[str] = []
        for p in points:
            _append_coord(coords, p.get("lat"), p.get("lon"))
            labels.append(str(p.get("label") or ""))

        cache_key = "|".join([segment] + [_coord_key(lat, lon) for lat, lon in coords])
        cached = _ROUTE_CACHE.get(cache_key)
        if cached:
            return [dict(p) for p in cached]

        try:
            osrm_points = _fetch_osrm_points(coords, labels)
            if osrm_points:
                _ROUTE_CACHE[cache_key] = [dict(p) for p in osrm_points]
                return osrm_points
        except Exception as exc:
            print(f"[visual-route] OSRM servidor no disponible para {segment}; uso fallback: {type(exc).__name__}: {exc}")

        return points

    def render_visual_map_html(segment: str, result: dict[str, Any], map_payload: dict[str, Any], weather_panel_html: str | None = None) -> str:
        if not callable(original_render_visual_map_html):
            return ""
        page = original_render_visual_map_html(segment, result, map_payload, weather_panel_html)
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
            "Mapa visual: origen → parada recomendada → destino. Si OSRM responde, la línea azul se ajusta a carretera en segundo plano.",
        )
        if "improveVisualRouteWithOsrm" not in page:
            page = _insert_before_last_script_close(page, _CLIENT_OSRM_SCRIPT)
        return page

    app = getattr(main_module, "app", None)
    if app is not None:
        from fastapi import Query
        from fastapi.responses import JSONResponse

        @app.get("/visual-route-osrm")
        async def visual_route_osrm(coords: str = Query(default="")) -> JSONResponse:
            if not re.fullmatch(r"[-0-9.,;]+", coords or ""):
                return JSONResponse({"status": "error", "error": "coords invalid"}, status_code=400)
            parts = [p for p in coords.split(";") if p]
            if len(parts) < 2 or len(parts) > 6:
                return JSONResponse({"status": "error", "error": "coords count invalid"}, status_code=400)
            try:
                # Validación básica lon,lat.
                for part in parts:
                    lon_s, lat_s = part.split(",", 1)
                    lon = float(lon_s); lat = float(lat_s)
                    if not (-10.5 <= lon <= 5.0 and 35.0 <= lat <= 45.0):
                        return JSONResponse({"status": "error", "error": "coords out of bounds"}, status_code=400)
                base = os.getenv("VISUAL_ROUTE_OSRM_BASE_URL", os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org")).rstrip("/")
                url = f"{base}/route/v1/driving/{coords}?overview=full&geometries=geojson&alternatives=false&steps=false"
                timeout = main_module.httpx.Timeout(5.0, connect=2.5)
                async with main_module.httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    resp = await client.get(url, headers={"User-Agent": "gasolina-api-christian/visual-route/1.1"})
                return JSONResponse(resp.json(), status_code=resp.status_code)
            except Exception as exc:
                return JSONResponse({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, status_code=502)

    main_module.build_visual_route_points = road_visual_route_points
    main_module.render_visual_map_html = render_visual_map_html
    setattr(main_module, "_visual_route_ext_installed", True)
    print("[visual-route] Trazado visual instalado: origen → parada → destino + proxy OSRM cliente")
