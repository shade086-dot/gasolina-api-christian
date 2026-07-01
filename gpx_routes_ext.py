from __future__ import annotations

import html
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def _static_path(main_module: Any, *parts: str) -> Path:
    app_dir = getattr(main_module, "APP_DIR", Path(__file__).resolve().parent)
    return Path(app_dir).joinpath(*parts)


def _load_routes(main_module: Any) -> list[dict[str, Any]]:
    path = _static_path(main_module, "static", "rutas_indice.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _stats(main_module: Any) -> dict[str, Any]:
    routes = _load_routes(main_module)
    total = round(sum(float(r.get("distance_km") or 0) for r in routes), 1)
    groups = Counter(str(r.get("group") or "Sin grupo") for r in routes)
    longest = max(routes, key=lambda r: float(r.get("distance_km") or 0), default={})
    last = routes[-1] if routes else {}
    return {"routes": routes, "count": len(routes), "total": total, "groups": groups, "longest": longest, "last": last}


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _global_bbox(routes: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    lats: list[float] = []
    lons: list[float] = []
    for r in routes:
        bbox = r.get("bbox") or []
        if isinstance(bbox, list) and len(bbox) == 4:
            try:
                min_lat, min_lon, max_lat, max_lon = [float(x) for x in bbox]
                lats.extend([min_lat, max_lat])
                lons.extend([min_lon, max_lon])
            except Exception:
                pass
        for key in ("start", "end"):
            pt = r.get(key) or []
            if isinstance(pt, list) and len(pt) == 2:
                try:
                    lats.append(float(pt[0]))
                    lons.append(float(pt[1]))
                except Exception:
                    pass
    if not lats or not lons:
        return 40.35, -3.65, 41.05, -2.65
    pad_lat = max((max(lats) - min(lats)) * 0.08, 0.04)
    pad_lon = max((max(lons) - min(lons)) * 0.08, 0.04)
    return min(lats) - pad_lat, min(lons) - pad_lon, max(lats) + pad_lat, max(lons) + pad_lon


def _peninsula_bbox() -> tuple[float, float, float, float]:
    # Bbox fija para que la vista se entienda como mapa peninsular y no como una nube de cajas.
    return 35.35, -10.2, 44.55, 4.25


def _project(
    lat: float,
    lon: float,
    bbox: tuple[float, float, float, float],
    x0: int = 85,
    y0: int = 145,
    w: int = 730,
    h: int = 455,
) -> tuple[float, float]:
    min_lat, min_lon, max_lat, max_lon = bbox
    lon_span = max(max_lon - min_lon, 0.000001)
    lat_span = max(max_lat - min_lat, 0.000001)
    x = x0 + ((lon - min_lon) / lon_span) * w
    y = y0 + (1 - ((lat - min_lat) / lat_span)) * h
    return x, y


def _geo_path(points: list[tuple[float, float]], bbox: tuple[float, float, float, float], *, x0: int, y0: int, w: int, h: int) -> str:
    parts: list[str] = []
    for idx, (lat, lon) in enumerate(points):
        x, y = _project(lat, lon, bbox, x0=x0, y0=y0, w=w, h=h)
        parts.append(("M" if idx == 0 else "L") + f"{x:.1f} {y:.1f}")
    return " ".join(parts)


def _peninsula_background(bbox: tuple[float, float, float, float], *, x0: int, y0: int, w: int, h: int) -> str:
    iberia = [
        (43.55, -9.25), (42.65, -8.95), (41.45, -9.25), (40.00, -8.85), (38.65, -8.95),
        (37.15, -7.45), (36.15, -5.65), (36.00, -3.20), (36.35, -1.75), (37.50, -0.65),
        (38.80, 0.20), (40.20, 0.75), (41.25, 0.95), (42.00, 2.10), (42.70, 3.15),
        (43.35, 1.60), (43.75, -0.60), (43.75, -3.00), (43.58, -5.30), (43.55, -7.20),
        (43.55, -9.25),
    ]
    portugal_line = [(42.1, -8.15), (40.8, -7.1), (39.5, -7.4), (38.4, -7.1), (37.2, -7.4)]
    pyrenees = [(43.0, -1.8), (42.8, -0.5), (42.75, 0.7), (42.65, 2.2), (42.45, 3.0)]
    ebro = [(42.2, -2.0), (41.8, -1.0), (41.65, 0.0), (41.3, 0.8)]
    tajo = [(40.7, -5.5), (40.3, -4.0), (40.0, -3.0), (39.6, -1.8)]
    guadiana = [(38.8, -7.2), (38.8, -6.2), (38.9, -5.0), (38.6, -3.6)]
    balear = [(39.7, 2.8), (39.4, 3.1), (39.0, 2.6), (38.85, 1.4)]
    canary_note_x, canary_note_y = x0 + 35, y0 + h - 30
    return f"""
      <rect x='{x0}' y='{y0}' width='{w}' height='{h}' rx='24' fill='#dff3ff' stroke='#bfdbfe'/>
      <path d='{_geo_path([(44.5,-10.2),(44.5,4.25),(43.15,4.25),(43.5,1.5),(43.9,-1.5),(44.1,-5.5),(43.9,-9.8)], bbox, x0=x0, y0=y0, w=w, h=h)} Z' fill='#e8f3df' opacity='.86'/>
      <path d='{_geo_path([(36.0,-10.2),(36.0,4.25),(35.35,4.25),(35.35,-10.2)], bbox, x0=x0, y0=y0, w=w, h=h)} Z' fill='#efe3c7' opacity='.55'/>
      <path d='{_geo_path(iberia, bbox, x0=x0, y0=y0, w=w, h=h)} Z' fill='#f5f1df' stroke='#94a3b8' stroke-width='2.2'/>
      <path d='{_geo_path(portugal_line, bbox, x0=x0, y0=y0, w=w, h=h)}' fill='none' stroke='#cbd5e1' stroke-width='1.6' stroke-dasharray='6 6'/>
      <path d='{_geo_path(pyrenees, bbox, x0=x0, y0=y0, w=w, h=h)}' fill='none' stroke='#94a3b8' stroke-width='2.5' opacity='.65'/>
      <path d='{_geo_path(ebro, bbox, x0=x0, y0=y0, w=w, h=h)}' fill='none' stroke='#60a5fa' stroke-width='2' opacity='.55'/>
      <path d='{_geo_path(tajo, bbox, x0=x0, y0=y0, w=w, h=h)}' fill='none' stroke='#60a5fa' stroke-width='2' opacity='.48'/>
      <path d='{_geo_path(guadiana, bbox, x0=x0, y0=y0, w=w, h=h)}' fill='none' stroke='#60a5fa' stroke-width='1.8' opacity='.38'/>
      <path d='{_geo_path(balear, bbox, x0=x0, y0=y0, w=w, h=h)}' fill='none' stroke='#94a3b8' stroke-width='4' stroke-linecap='round' opacity='.65'/>
      <text x='{x0+430}' y='{y0+255}' font-size='26' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='900' fill='#cbd5e1' opacity='.8'>ESPAÑA</text>
      <text x='{x0+115}' y='{y0+285}' font-size='18' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='800' fill='#cbd5e1'>Portugal</text>
      <text x='{x0+495}' y='{y0+40}' font-size='16' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='800' fill='#94a3b8'>Francia</text>
      <text x='{x0+535}' y='{y0+h-58}' font-size='15' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='800' fill='#60a5fa'>Mediterráneo</text>
      <text x='{canary_note_x}' y='{canary_note_y}' font-size='12' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' fill='#64748b'>Vista esquemática peninsular · rutas colocadas por lat/lon</text>
    """


def _route_shapes(
    routes: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
    *,
    x0: int = 85,
    y0: int = 145,
    w: int = 730,
    h: int = 455,
    compact: bool = False,
) -> str:
    colors = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#4f46e5"]
    pieces: list[str] = []
    for i, r in enumerate(routes):
        color = colors[i % len(colors)]
        opacity = 0.30 if i < len(routes) - 5 else 0.68
        bbox_r = r.get("bbox") or []
        if isinstance(bbox_r, list) and len(bbox_r) == 4:
            try:
                min_lat, min_lon, max_lat, max_lon = [float(x) for x in bbox_r]
                # Ignora puntos muy fuera de la península en la vista general.
                if max_lat < bbox[0] or min_lat > bbox[2] or max_lon < bbox[1] or min_lon > bbox[3]:
                    continue
                x1, y1 = _project(max_lat, min_lon, bbox, x0=x0, y0=y0, w=w, h=h)
                x2, y2 = _project(min_lat, max_lon, bbox, x0=x0, y0=y0, w=w, h=h)
                width = max(x2 - x1, 2 if compact else 5)
                height = max(y2 - y1, 2 if compact else 5)
                pieces.append(f"<rect x='{x1:.1f}' y='{y1:.1f}' width='{width:.1f}' height='{height:.1f}' rx='{3 if compact else 6}' fill='none' stroke='{color}' stroke-width='{1.1 if compact else 2.0}' opacity='{opacity:.2f}'/>")
            except Exception:
                pass
        start = r.get("start") or []
        end = r.get("end") or []
        if isinstance(start, list) and isinstance(end, list) and len(start) == 2 and len(end) == 2:
            try:
                x1, y1 = _project(float(start[0]), float(start[1]), bbox, x0=x0, y0=y0, w=w, h=h)
                x2, y2 = _project(float(end[0]), float(end[1]), bbox, x0=x0, y0=y0, w=w, h=h)
                dx, dy = x2 - x1, y2 - y1
                bend = min(34 if compact else 48, max(8, math.hypot(dx, dy) * 0.13))
                length = max(math.hypot(dx, dy), 1)
                cx = (x1 + x2) / 2 - dy / length * bend
                cy = (y1 + y2) / 2 + dx / length * bend
                pieces.append(f"<path d='M{x1:.1f} {y1:.1f} Q{cx:.1f} {cy:.1f} {x2:.1f} {y2:.1f}' fill='none' stroke='{color}' stroke-width='{2.1 if compact else 3.1}' stroke-linecap='round' opacity='0.82'/>")
            except Exception:
                pass
    return "\n  ".join(pieces)


def _anchor_svg(label: str, lat: float, lon: float, bbox: tuple[float, float, float, float], *, x0: int, y0: int, w: int, h: int, small: bool = False) -> str:
    x, y = _project(lat, lon, bbox, x0=x0, y0=y0, w=w, h=h)
    r = 5 if small else 7
    fs = 11 if small else 14
    return f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{r}' fill='#111827' stroke='white' stroke-width='2'/><text x='{x+9:.1f}' y='{y+4:.1f}' font-size='{fs}' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='900' fill='#111827' paint-order='stroke' stroke='white' stroke-width='3'>{_e(label)}</text>"


def _preview_svg(main_module: Any) -> str:
    st = _stats(main_module)
    routes = st["routes"]
    peninsula_bbox = _peninsula_bbox()
    local_bbox = _global_bbox(routes)
    route_shapes = _route_shapes(routes, peninsula_bbox, x0=82, y0=160, w=750, h=470)
    inset_shapes = _route_shapes(routes, local_bbox, x0=610, y0=435, w=205, h=160, compact=True)
    top_groups = st["groups"].most_common(4)
    group_lines = "".join(
        f"<text x='872' y='{352+i*32}' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' fill='#334155'>• {_e(g)[:33]}: {n}</text>"
        for i, (g, n) in enumerate(top_groups)
    )
    anchors = [
        ("Madrid", 40.4168, -3.7038),
        ("Anchuelo", 40.4667, -3.2687),
        ("Alcalá", 40.4923, -3.3615),
        ("Cabanillas", 40.6302, -3.2357),
        ("Burgos", 42.3439, -3.6969),
        ("Cantabria", 43.35, -4.05),
    ]
    anchor_svg = "".join(_anchor_svg(label, lat, lon, peninsula_bbox, x0=82, y0=160, w=750, h=470) for label, lat, lon in anchors)
    inset_anchor_svg = "".join(
        _anchor_svg(label, lat, lon, local_bbox, x0=610, y0=435, w=205, h=160, small=True)
        for label, lat, lon in anchors[:4]
    )

    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='720' viewBox='0 0 1200 720'>
  <defs><linearGradient id='bg' x1='0' x2='1' y1='0' y2='1'><stop offset='0' stop-color='#e8f2ff'/><stop offset='1' stop-color='#f7fafc'/></linearGradient></defs>
  <rect width='1200' height='720' fill='url(#bg)'/>
  <rect x='42' y='38' width='1116' height='644' rx='34' fill='white'/>
  <text x='82' y='100' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='36' font-weight='900' fill='#111827'>Historial GPX · {st['count']} rutas</text>
  <text x='82' y='138' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='20' fill='#64748b'>{st['total']:.1f} km · vista general sobre mapa peninsular aproximado</text>
  {_peninsula_background(peninsula_bbox, x0=82, y0=160, w=750, h=470)}
  {route_shapes}
  {anchor_svg}
  <rect x='598' y='410' width='235' height='206' rx='18' fill='white' stroke='#bfdbfe' opacity='.94'/>
  <text x='616' y='432' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='15' font-weight='900' fill='#1e3a8a'>Detalle zona Madrid</text>
  <rect x='610' y='435' width='205' height='160' rx='12' fill='#eef7ef' stroke='#dbe4ee'/>
  {inset_shapes}
  {inset_anchor_svg}
  <rect x='858' y='164' width='270' height='86' rx='20' fill='#f8fafc' stroke='#dbe4ee'/>
  <text x='882' y='198' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='17' font-weight='900' fill='#111827'>Ruta más larga</text>
  <text x='882' y='226' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='15' fill='#475569'>{_e(st['longest'].get('name'))[:25]} · {float(st['longest'].get('distance_km') or 0):.1f} km</text>
  <rect x='858' y='286' width='270' height='180' rx='20' fill='#ecfdf3' stroke='#bbf7d0'/>
  <text x='882' y='318' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='17' font-weight='900' fill='#166534'>Zonas principales</text>
  {group_lines}
  <rect x='858' y='492' width='270' height='90' rx='20' fill='#eff6ff' stroke='#bfdbfe'/>
  <text x='882' y='526' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' font-weight='900' fill='#1e3a8a'>Lectura rápida</text>
  <text x='882' y='554' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='14' fill='#475569'>Mapa esquemático: no sustituye al</text>
  <text x='882' y='574' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='14' fill='#475569'>interactivo, pero ubica las zonas.</text>
  <text x='82' y='662' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='15' fill='#64748b'>Para detalle real, abre el mapa completo interactivo.</text>
</svg>"""


def _page(main_module: Any) -> str:
    st = _stats(main_module)
    groups = st["groups"].most_common(5)
    group_html = "".join(f"<li><b>{_e(g)}</b>: {n} rutas</li>" for g, n in groups)
    last = st["last"]
    longest = st["longest"]
    version = f"{st['count']}-{str(st['total']).replace('.', '-') }"
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/><title>Historial GPX</title>
<style>body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f6f8;color:#111827}}.page{{max-width:1120px;margin:0 auto;padding:16px}}.hero,.card{{background:#fff;border:1px solid #dbe4ee;border-radius:20px;padding:18px;box-shadow:0 4px 18px rgba(31,41,51,.06)}}h1{{margin:0 0 6px;font-size:31px}}p{{color:#64748b;line-height:1.45}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}}.stat{{background:#f8fafc;border:1px solid #dbe4ee;border-radius:16px;padding:14px}}.stat b{{display:block;font-size:24px}}.stat span{{color:#64748b;font-size:13px;font-weight:700}}.links{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}}a.btn{{display:block;text-align:center;background:#111827;color:#fff;padding:13px;border-radius:14px;font-weight:850;text-decoration:none}}a.blue{{background:#2563eb}}a.green{{background:#16803c}}.grid{{display:grid;grid-template-columns:1.15fr .85fr;gap:14px;margin-top:14px}}.preview{{width:100%;border-radius:18px;border:1px solid #dbe4ee;background:#fff}}li{{margin:6px 0;color:#475569}}@media(max-width:820px){{.grid,.links,.stats{{grid-template-columns:1fr}}}}</style></head>
<body><main class='page'><section class='hero'><h1>🏍️ Historial GPX</h1><p>Histórico real de rutas hechas, generado desde los GPX guardados en <b>gpx_historial/</b>. Sirve para ver el mapa completo y preparar la futura comparación de rutas nuevas.</p>
<div class='stats'><div class='stat'><b>{st['count']}</b><span>rutas</span></div><div class='stat'><b>{st['total']:.1f}</b><span>km aprox.</span></div><div class='stat'><b>{len(st['groups'])}</b><span>zonas</span></div><div class='stat'><b>{float(longest.get('distance_km') or 0):.1f}</b><span>km ruta más larga</span></div></div>
<div class='links'><a class='btn blue' href='/rutas/full' target='_blank'>🗺️ Abrir mapa completo</a><a class='btn green' href='/rutas/preview?v={version}' target='_blank'>🖼️ Vista aproximada</a><a class='btn' href='/rutas.json' target='_blank'>📄 Datos JSON</a></div></section>
<section class='grid'><div class='card'><img class='preview' src='/rutas/preview?v={version}' alt='Vista aproximada GPX'/></div><div class='card'><h2>Resumen</h2><p><b>Última ruta:</b> {_e(last.get('name'))} · {float(last.get('distance_km') or 0):.1f} km</p><p><b>Ruta más larga:</b> {_e(longest.get('name'))} · {float(longest.get('distance_km') or 0):.1f} km</p><h3>Zonas principales</h3><ul>{group_html}</ul></div></section></main></body></html>"""


def install_routes(main_module: Any) -> None:
    if getattr(main_module, "_gpx_routes_ext_installed", False):
        return
    app = main_module.app
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    @app.get("/rutas", response_class=HTMLResponse)
    async def rutas_page() -> HTMLResponse:
        return HTMLResponse(_page(main_module))

    @app.get("/rutas.json")
    async def rutas_json() -> JSONResponse:
        st = _stats(main_module)
        return JSONResponse({"status": "ok", "routes_count": st["count"], "total_km": st["total"], "groups": dict(st["groups"]), "routes": st["routes"]})

    @app.get("/rutas/preview")
    async def rutas_preview() -> Response:
        return Response(_preview_svg(main_module), media_type="image/svg+xml")

    @app.get("/rutas-preview.svg")
    async def rutas_preview_svg() -> Response:
        return Response(_preview_svg(main_module), media_type="image/svg+xml")

    @app.get("/rutas/full", response_class=HTMLResponse)
    async def rutas_full() -> HTMLResponse:
        path = _static_path(main_module, "static", "rutas_mapa.html")
        if path.exists():
            return HTMLResponse(path.read_text(encoding="utf-8"))
        return HTMLResponse(_page(main_module))

    original_render = getattr(main_module, "render_visual_map_html", None)
    if callable(original_render):
        def wrapped_render_visual_map_html(*args: Any, **kwargs: Any) -> str:
            page = original_render(*args, **kwargs)
            if 'href="/rutas"' in page:
                return page
            st = _stats(main_module)
            button = f'<a class="secondary" href="/rutas" target="_blank">Historial GPX · {st["count"]} rutas · {st["total"]:.0f} km</a>'
            marker = '<a class="apple" href="{apple_link}" target="_blank">Apple Maps · estación</a>'
            if marker in page:
                return page.replace(marker, marker + "\n        " + button, 1)
            return page.replace("</main>", f"<section class='actions'><h2>Historial GPX</h2><p>{st['count']} rutas · {st['total']:.1f} km guardados</p><div class='links'>{button}</div></section></main>", 1)
        main_module.render_visual_map_html = wrapped_render_visual_map_html

    setattr(main_module, "_gpx_routes_ext_installed", True)
