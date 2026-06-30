from __future__ import annotations

import html
import json
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


def _preview_svg(main_module: Any) -> str:
    st = _stats(main_module)
    top_groups = st["groups"].most_common(4)
    group_lines = "".join(
        f"<text x='825' y='{360+i*34}' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='17' fill='#334155'>• {_e(g)}: {n}</text>"
        for i, (g, n) in enumerate(top_groups)
    )
    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='720' viewBox='0 0 1200 720'>
  <defs><linearGradient id='bg' x1='0' x2='1' y1='0' y2='1'><stop offset='0' stop-color='#e8f2ff'/><stop offset='1' stop-color='#f7fafc'/></linearGradient></defs>
  <rect width='1200' height='720' fill='url(#bg)'/><rect x='42' y='38' width='1116' height='644' rx='34' fill='white'/>
  <text x='82' y='103' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='38' font-weight='900' fill='#111827'>Historial GPX · {st['count']} rutas</text>
  <text x='82' y='143' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='21' fill='#64748b'>{st['total']:.1f} km registrados · mapa generado desde GPX reales</text>
  <rect x='82' y='180' width='700' height='430' rx='28' fill='#eef7ef' stroke='#d7e5d8'/>
  <path d='M125 510 C230 390, 305 425, 410 300 S610 180, 735 238' fill='none' stroke='#2563eb' stroke-width='12' stroke-linecap='round' opacity='.78'/>
  <path d='M150 455 C260 330, 380 325, 520 405 S655 505, 735 420' fill='none' stroke='#16a34a' stroke-width='10' stroke-linecap='round' opacity='.70'/>
  <path d='M180 230 C285 280, 345 180, 480 225 S620 305, 720 250' fill='none' stroke='#d97706' stroke-width='9' stroke-linecap='round' opacity='.68'/>
  <path d='M245 560 C350 500, 460 565, 580 505 S685 370, 742 360' fill='none' stroke='#9333ea' stroke-width='8' stroke-linecap='round' opacity='.60'/>
  <circle cx='210' cy='515' r='14' fill='#111827'/><text x='232' y='522' font-size='19' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='800' fill='#111827'>Anchuelo</text>
  <circle cx='725' cy='245' r='14' fill='#111827'/><text x='742' y='252' font-size='19' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-weight='800' fill='#111827'>Alcalá</text>
  <rect x='820' y='190' width='285' height='82' rx='20' fill='#f8fafc' stroke='#dbe4ee'/>
  <text x='846' y='225' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='18' font-weight='900' fill='#111827'>Ruta más larga</text>
  <text x='846' y='253' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='16' fill='#475569'>{_e(st['longest'].get('name'))[:28]} · {float(st['longest'].get('distance_km') or 0):.1f} km</text>
  <rect x='820' y='302' width='285' height='190' rx='20' fill='#ecfdf3' stroke='#bbf7d0'/>
  <text x='846' y='334' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='18' font-weight='900' fill='#166534'>Zonas principales</text>
  {group_lines}
  <text x='82' y='650' font-family='-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif' font-size='17' fill='#64748b'>Vista previa generada automáticamente. No necesitas subir ninguna imagen PNG.</text>
</svg>"""


def _page(main_module: Any) -> str:
    st = _stats(main_module)
    groups = st["groups"].most_common(5)
    group_html = "".join(f"<li><b>{_e(g)}</b>: {n} rutas</li>" for g, n in groups)
    last = st["last"]
    longest = st["longest"]
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/><title>Historial GPX</title>
<style>body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f6f8;color:#111827}}.page{{max-width:1120px;margin:0 auto;padding:16px}}.hero,.card{{background:#fff;border:1px solid #dbe4ee;border-radius:20px;padding:18px;box-shadow:0 4px 18px rgba(31,41,51,.06)}}h1{{margin:0 0 6px;font-size:31px}}p{{color:#64748b;line-height:1.45}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}}.stat{{background:#f8fafc;border:1px solid #dbe4ee;border-radius:16px;padding:14px}}.stat b{{display:block;font-size:24px}}.stat span{{color:#64748b;font-size:13px;font-weight:700}}.links{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}}a.btn{{display:block;text-align:center;background:#111827;color:#fff;padding:13px;border-radius:14px;font-weight:850;text-decoration:none}}a.blue{{background:#2563eb}}a.green{{background:#16803c}}.grid{{display:grid;grid-template-columns:1.15fr .85fr;gap:14px;margin-top:14px}}.preview{{width:100%;border-radius:18px;border:1px solid #dbe4ee}}li{{margin:6px 0;color:#475569}}@media(max-width:820px){{.grid,.links,.stats{{grid-template-columns:1fr}}}}</style></head>
<body><main class='page'><section class='hero'><h1>🏍️ Historial GPX</h1><p>Histórico real de rutas hechas, generado desde los GPX guardados en <b>gpx_historial/</b>. Sirve para ver el mapa completo y preparar la futura comparación de rutas nuevas.</p>
<div class='stats'><div class='stat'><b>{st['count']}</b><span>rutas</span></div><div class='stat'><b>{st['total']:.1f}</b><span>km aprox.</span></div><div class='stat'><b>{len(st['groups'])}</b><span>zonas</span></div><div class='stat'><b>{float(longest.get('distance_km') or 0):.1f}</b><span>km ruta más larga</span></div></div>
<div class='links'><a class='btn blue' href='/rutas/full' target='_blank'>🗺️ Abrir mapa completo</a><a class='btn green' href='/rutas/preview' target='_blank'>🖼️ Vista previa generada</a><a class='btn' href='/rutas.json' target='_blank'>📄 Datos JSON</a></div></section>
<section class='grid'><div class='card'><h2>Vista previa automática</h2><img class='preview' src='/rutas/preview' alt='Vista previa GPX'/><p>No hace falta subir una imagen PNG: esta vista se genera automáticamente con los datos actuales.</p></div><div class='card'><h2>Resumen</h2><p><b>Última ruta:</b> {_e(last.get('name'))} · {float(last.get('distance_km') or 0):.1f} km</p><p><b>Ruta más larga:</b> {_e(longest.get('name'))} · {float(longest.get('distance_km') or 0):.1f} km</p><h3>Zonas principales</h3><ul>{group_html}</ul></div></section></main></body></html>"""


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
