"""Extensiones ligeras del panel sin tocar main.py.

Añade la página /rutas y un botón al mapa visual para abrir el historial GPX.
No intercepta ntfy ni modifica notificaciones.
"""
from __future__ import annotations

import html
import importlib.abc
import importlib.machinery
import json
import os
import sys
from pathlib import Path
from typing import Any


ROUTES_COUNT = int(os.getenv("GPX_ROUTES_COUNT", "22"))
ROUTES_TOTAL_KM = float(os.getenv("GPX_ROUTES_TOTAL_KM", "4514.1"))
ROUTES_FULL_MAP_URL = os.getenv("GPX_FULL_MAP_URL", "").strip()
ROUTES_PREVIEW_URL = os.getenv("GPX_PREVIEW_URL", "").strip()

LAST_ROUTE = {
    "id": 22,
    "name": "MapitRoute-3Flwm.gpx",
    "distance_km": 97.8,
    "start": "Alcalá de Henares",
    "finish": "Anchuelo",
    "bbox": "lat 40.446–40.684 · lon -3.455 a -3.267",
    "note": "Ruta ya realizada; no proponerla como novedad salvo que se pida repetir o usar tramo como enlace.",
}


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _static_path(main_module: Any, *parts: str) -> Path:
    app_dir = getattr(main_module, "APP_DIR", Path(__file__).resolve().parent)
    return Path(app_dir).joinpath(*parts)


def _routes_json_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "routes_count": ROUTES_COUNT,
        "total_km": ROUTES_TOTAL_KM,
        "last_route": LAST_ROUTE,
        "full_map_url_env": bool(ROUTES_FULL_MAP_URL),
        "preview_url_env": bool(ROUTES_PREVIEW_URL),
        "message": "Historial GPX preparado para enlazar mapa completo, preview e índice de rutas.",
    }


def _preview_svg() -> str:
    # Vista previa ligera y estable: no pretende sustituir al mapa real reconstruido.
    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='720' viewBox='0 0 1200 720'>
  <defs>
    <linearGradient id='bg' x1='0' x2='1' y1='0' y2='1'>
      <stop offset='0' stop-color='#e8f2ff'/><stop offset='1' stop-color='#f7fafc'/>
    </linearGradient>
    <filter id='shadow' x='-20%' y='-20%' width='140%' height='140%'>
      <feDropShadow dx='0' dy='8' stdDeviation='10' flood-color='#0f172a' flood-opacity='.12'/>
    </filter>
  </defs>
  <rect width='1200' height='720' fill='url(#bg)'/>
  <rect x='42' y='38' width='1116' height='644' rx='34' fill='white' filter='url(#shadow)'/>
  <text x='82' y='100' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='36' font-weight='900' fill='#111827'>Historial GPX · {ROUTES_COUNT} rutas</text>
  <text x='82' y='138' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='20' fill='#64748b'>{ROUTES_TOTAL_KM:.1f} km registrados · base Anchuelo / Alcalá / Cabanillas</text>
  <rect x='82' y='178' width='700' height='430' rx='28' fill='#eef7ef' stroke='#d7e5d8'/>
  <path d='M125 510 C230 390, 305 425, 410 300 S610 180, 735 238' fill='none' stroke='#2563eb' stroke-width='12' stroke-linecap='round' opacity='.78'/>
  <path d='M150 455 C260 330, 380 325, 520 405 S655 505, 735 420' fill='none' stroke='#16a34a' stroke-width='10' stroke-linecap='round' opacity='.70'/>
  <path d='M180 230 C285 280, 345 180, 480 225 S620 305, 720 250' fill='none' stroke='#d97706' stroke-width='9' stroke-linecap='round' opacity='.68'/>
  <path d='M245 560 C350 500, 460 565, 580 505 S685 370, 742 360' fill='none' stroke='#9333ea' stroke-width='8' stroke-linecap='round' opacity='.60'/>
  <circle cx='210' cy='515' r='14' fill='#111827'/><text x='232' y='522' font-size='19' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='800' fill='#111827'>Anchuelo</text>
  <circle cx='725' cy='245' r='14' fill='#111827'/><text x='742' y='252' font-size='19' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='800' fill='#111827'>Alcalá</text>
  <rect x='820' y='190' width='285' height='112' rx='22' fill='#f8fafc' stroke='#dbe4ee'/>
  <text x='846' y='228' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='18' font-weight='900' fill='#111827'>Última ruta añadida</text>
  <text x='846' y='258' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' fill='#475569'>{_escape(LAST_ROUTE['name'])}</text>
  <text x='846' y='282' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' fill='#475569'>≈ {LAST_ROUTE['distance_km']} km</text>
  <rect x='820' y='326' width='285' height='156' rx='22' fill='#ecfdf3' stroke='#bbf7d0'/>
  <text x='846' y='364' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='18' font-weight='900' fill='#166534'>Uso previsto</text>
  <text x='846' y='398' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' fill='#166534'>• Evitar repetir rutas</text>
  <text x='846' y='425' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' fill='#166534'>• Detectar tramos ya hechos</text>
  <text x='846' y='452' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' fill='#166534'>• Preparar rutas nuevas</text>
  <text x='82' y='650' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' fill='#64748b'>Vista previa generada. Si subes static/rutas_mapa.html y static/rutas_preview.png, /rutas usará el mapa real reconstruido.</text>
</svg>"""


def _routes_page_html(main_module: Any) -> str:
    static_full = _static_path(main_module, "static", "rutas_mapa.html")
    static_preview = _static_path(main_module, "static", "rutas_preview.png")
    full_url = ROUTES_FULL_MAP_URL or ("/rutas/full" if static_full.exists() else "/rutas-preview.svg")
    preview_url = ROUTES_PREVIEW_URL or ("/rutas/preview" if static_preview.exists() else "/rutas-preview.svg")
    return f"""<!doctype html>
<html lang='es'>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>Historial GPX · rutas realizadas</title>
  <style>
    :root {{ --ink:#111827; --muted:#64748b; --blue:#2563eb; --green:#16803c; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#f4f6f8; color:var(--ink); }}
    .page {{ max-width:1120px; margin:0 auto; padding:16px; }}
    .hero,.card {{ background:#fff; border:1px solid #dbe4ee; border-radius:20px; padding:18px; box-shadow:0 4px 18px rgba(31,41,51,.06); }}
    .hero h1 {{ margin:0 0 6px; font-size:30px; }}
    .hero p,.note {{ margin:0; color:var(--muted); line-height:1.45; }}
    .grid {{ display:grid; grid-template-columns:1.2fr .8fr; gap:14px; margin-top:14px; }}
    .preview {{ width:100%; border-radius:18px; border:1px solid #dbe4ee; background:#fff; }}
    .stats {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:14px 0; }}
    .stat {{ background:#f8fafc; border:1px solid #dbe4ee; border-radius:16px; padding:14px; }}
    .stat b {{ display:block; font-size:24px; }}
    .stat span {{ color:var(--muted); font-size:13px; font-weight:700; }}
    .links {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:14px; }}
    a.btn {{ display:block; text-align:center; background:#111827; color:white; padding:13px 12px; border-radius:14px; font-weight:850; text-decoration:none; }}
    a.btn.green {{ background:var(--green); }}
    a.btn.blue {{ background:var(--blue); }}
    .route {{ border:1px solid #dbe4ee; background:#f8fafc; border-radius:16px; padding:14px; margin-top:12px; }}
    .route h3 {{ margin:0 0 6px; }}
    .route p {{ margin:4px 0; color:var(--muted); }}
    @media(max-width:820px){{ .grid,.links,.stats {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <main class='page'>
    <section class='hero'>
      <h1>🏍️ Historial GPX</h1>
      <p>Mapa y acceso rápido al histórico de rutas realizadas para evitar repetir tramos y preparar rutas nuevas.</p>
      <div class='stats'>
        <div class='stat'><b>{ROUTES_COUNT}</b><span>rutas guardadas</span></div>
        <div class='stat'><b>{ROUTES_TOTAL_KM:.1f}</b><span>km aproximados</span></div>
        <div class='stat'><b>GPX</b><span>modo historial</span></div>
      </div>
      <div class='links'>
        <a class='btn blue' href='{_escape(full_url)}' target='_blank'>🗺️ Abrir mapa completo</a>
        <a class='btn green' href='{_escape(preview_url)}' target='_blank'>🖼️ Vista previa</a>
        <a class='btn' href='/rutas.json' target='_blank'>📄 Ver resumen JSON</a>
      </div>
    </section>

    <section class='grid'>
      <div class='card'>
        <h2>Vista previa</h2>
        <img class='preview' src='{_escape(preview_url)}' alt='Vista previa de rutas GPX'/>
        <p class='note'>Cuando subas el mapa real reconstruido a <b>static/rutas_mapa.html</b> y la imagen a <b>static/rutas_preview.png</b>, esta página los usará automáticamente.</p>
      </div>
      <div class='card'>
        <h2>Última ruta registrada</h2>
        <div class='route'>
          <h3>{LAST_ROUTE['id']}) {_escape(LAST_ROUTE['name'])}</h3>
          <p><b>Distancia:</b> ≈ {LAST_ROUTE['distance_km']} km</p>
          <p><b>Inicio / fin:</b> {_escape(LAST_ROUTE['start'])} → {_escape(LAST_ROUTE['finish'])}</p>
          <p><b>Bbox:</b> {_escape(LAST_ROUTE['bbox'])}</p>
          <p>{_escape(LAST_ROUTE['note'])}</p>
        </div>
        <p class='note'>Siguiente paso: guardar los GPX reales en el repo para comparar una ruta nueva contra el historial y marcar coincidencias.</p>
      </div>
    </section>
  </main>
</body>
</html>"""


def _install_routes(main_module: Any) -> None:
    if getattr(main_module, "_rutas_routes_installed", False):
        return
    app = getattr(main_module, "app", None)
    if app is None:
        return

    from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse

    @app.get("/rutas", response_class=HTMLResponse)
    async def rutas_page() -> HTMLResponse:  # type: ignore[unused-ignore]
        return HTMLResponse(_routes_page_html(main_module))

    @app.get("/rutas.json")
    async def rutas_json() -> JSONResponse:  # type: ignore[unused-ignore]
        return JSONResponse(_routes_json_payload())

    @app.get("/rutas-preview.svg")
    async def rutas_preview_svg() -> Response:  # type: ignore[unused-ignore]
        return Response(_preview_svg(), media_type="image/svg+xml")

    @app.get("/rutas/preview")
    async def rutas_preview() -> Response:  # type: ignore[unused-ignore]
        path = _static_path(main_module, "static", "rutas_preview.png")
        if path.exists():
            return Response(path.read_bytes(), media_type="image/png")
        return Response(_preview_svg(), media_type="image/svg+xml")

    @app.get("/rutas/full")
    async def rutas_full() -> Response:  # type: ignore[unused-ignore]
        if ROUTES_FULL_MAP_URL:
            return RedirectResponse(ROUTES_FULL_MAP_URL)
        path = _static_path(main_module, "static", "rutas_mapa.html")
        if path.exists():
            return HTMLResponse(path.read_text(encoding="utf-8"))
        return HTMLResponse(_routes_page_html(main_module))

    original_render = getattr(main_module, "render_visual_map_html", None)
    if callable(original_render):
        def wrapped_render_visual_map_html(*args: Any, **kwargs: Any) -> str:
            page = original_render(*args, **kwargs)
            button = '<a class="secondary" href="/rutas" target="_blank">Historial GPX · rutas</a>'
            if 'href="/rutas"' in page:
                return page
            marker = '<a class="apple" href="{apple_link}" target="_blank">Apple Maps · estación</a>'
            if marker in page:
                return page.replace(marker, marker + "\n        " + button, 1)
            return page.replace("</main>", f"<section class='actions'><h2>Historial GPX</h2><div class='links'>{button}</div></section></main>", 1)
        main_module.render_visual_map_html = wrapped_render_visual_map_html

    setattr(main_module, "_rutas_routes_installed", True)


class _MainLoader(importlib.abc.Loader):
    def __init__(self, wrapped: importlib.abc.Loader):
        self.wrapped = wrapped

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        if hasattr(self.wrapped, "create_module"):
            return self.wrapped.create_module(spec)  # type: ignore[attr-defined]
        return None

    def exec_module(self, module):  # type: ignore[no-untyped-def]
        self.wrapped.exec_module(module)  # type: ignore[attr-defined]
        _install_routes(module)


class _MainFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
        if fullname != "main":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.loader and not isinstance(spec.loader, _MainLoader):
            spec.loader = _MainLoader(spec.loader)  # type: ignore[arg-type]
        return spec


if "main" in sys.modules:
    _install_routes(sys.modules["main"])
else:
    if not any(isinstance(finder, _MainFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _MainFinder())
