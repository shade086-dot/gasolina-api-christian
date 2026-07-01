from __future__ import annotations

from typing import Any

import gpx_routes_ext


def _remove_routes(app: Any, paths: set[str]) -> None:
    try:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) not in paths]
    except Exception as exc:
        print(f"[gpx-preview] No pude reemplazar rutas: {type(exc).__name__}: {exc}")


def _e(value: Any) -> str:
    return gpx_routes_ext._e(value)


def _stats(main_module: Any) -> dict[str, Any]:
    return gpx_routes_ext._stats(main_module)


def _page(main_module: Any) -> str:
    st = _stats(main_module)
    groups = st["groups"].most_common(5)
    group_html = "".join(f"<li><b>{_e(g)}</b>: {n} rutas</li>" for g, n in groups)
    last = st["last"]
    longest = st["longest"]
    version = f"{st['count']}-{str(st['total']).replace('.', '-')}-map"
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/><title>Historial GPX</title>
<style>
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f6f8;color:#111827}}.page{{max-width:1120px;margin:0 auto;padding:16px}}.hero,.card{{background:#fff;border:1px solid #dbe4ee;border-radius:20px;padding:18px;box-shadow:0 4px 18px rgba(31,41,51,.06)}}h1{{margin:0 0 6px;font-size:31px}}p{{color:#64748b;line-height:1.45}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}}.stat{{background:#f8fafc;border:1px solid #dbe4ee;border-radius:16px;padding:14px}}.stat b{{display:block;font-size:24px}}.stat span{{color:#64748b;font-size:13px;font-weight:700}}.links{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}}a.btn{{display:block;text-align:center;background:#111827;color:#fff;padding:13px;border-radius:14px;font-weight:850;text-decoration:none}}a.blue{{background:#2563eb}}a.green{{background:#16803c}}.grid{{display:grid;grid-template-columns:1.18fr .82fr;gap:14px;margin-top:14px}}.mapwrap{{position:relative;height:430px;border-radius:18px;overflow:hidden;border:1px solid #dbe4ee;background:#e5eef8}}.mapwrap iframe{{position:absolute;inset:0;width:100%;height:100%;border:0}}.badge{{position:absolute;left:12px;top:12px;z-index:5;background:rgba(255,255,255,.94);border:1px solid #dbe4ee;border-radius:999px;padding:7px 11px;font-size:13px;font-weight:850;color:#334155;box-shadow:0 4px 14px rgba(15,23,42,.10)}}li{{margin:6px 0;color:#475569}}.mini-note{{font-size:13px;color:#64748b;margin:9px 0 0}}@media(max-width:820px){{.grid,.links,.stats{{grid-template-columns:1fr}}.mapwrap{{height:360px}}}}
</style></head>
<body><main class='page'><section class='hero'><h1>🏍️ Historial GPX</h1><p>Histórico real de rutas hechas, generado desde los GPX guardados en <b>gpx_historial/</b>. Sirve para ver el mapa completo y preparar la futura comparación de rutas nuevas.</p>
<div class='stats'><div class='stat'><b>{st['count']}</b><span>rutas</span></div><div class='stat'><b>{st['total']:.1f}</b><span>km aprox.</span></div><div class='stat'><b>{len(st['groups'])}</b><span>zonas</span></div><div class='stat'><b>{float(longest.get('distance_km') or 0):.1f}</b><span>km ruta más larga</span></div></div>
<div class='links'><a class='btn blue' href='/rutas/full' target='_blank'>🗺️ Abrir mapa completo</a><a class='btn green' href='/rutas/preview?v={version}' target='_blank'>🖼️ Vista aproximada</a><a class='btn' href='/rutas.json' target='_blank'>📄 Datos JSON</a></div></section>
<section class='grid'><div class='card'><div class='mapwrap'><div class='badge'>Mapa real aproximado · GPX</div><iframe src='/rutas/full?v={version}' loading='lazy'></iframe></div><p class='mini-note'>Vista real con fondo OpenStreetMap y las rutas GPX; para manejarlo cómodo, abre el mapa completo.</p></div><div class='card'><h2>Resumen</h2><p><b>Última ruta:</b> {_e(last.get('name'))} · {float(last.get('distance_km') or 0):.1f} km</p><p><b>Ruta más larga:</b> {_e(longest.get('name'))} · {float(longest.get('distance_km') or 0):.1f} km</p><h3>Zonas principales</h3><ul>{group_html}</ul></div></section></main></body></html>"""


def _preview_page(main_module: Any) -> str:
    st = _stats(main_module)
    version = f"{st['count']}-{str(st['total']).replace('.', '-')}-map"
    return f"""<!doctype html><html lang='es'><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/><title>Vista aproximada GPX</title>
<style>html,body{{margin:0;height:100%;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}iframe{{position:fixed;inset:0;width:100%;height:100%;border:0}}.top{{position:fixed;z-index:10;left:14px;top:14px;background:rgba(255,255,255,.94);border:1px solid #dbe4ee;border-radius:16px;padding:10px 13px;color:#111827;font-weight:850;box-shadow:0 8px 24px rgba(15,23,42,.18)}}.top span{{display:block;color:#64748b;font-size:13px;font-weight:700;margin-top:2px}}</style></head>
<body><div class='top'>🏍️ Historial GPX · {st['count']} rutas<span>{st['total']:.1f} km · mapa real aproximado</span></div><iframe src='/rutas/full?v={version}'></iframe></body></html>"""


def install(main_module: Any) -> None:
    if getattr(main_module, "_gpx_preview_map_ext_installed", False):
        return
    app = main_module.app
    from fastapi.responses import HTMLResponse

    _remove_routes(app, {"/rutas", "/rutas/preview"})

    @app.get("/rutas", response_class=HTMLResponse)
    async def rutas_page_map() -> HTMLResponse:
        return HTMLResponse(_page(main_module))

    @app.get("/rutas/preview", response_class=HTMLResponse)
    async def rutas_preview_map() -> HTMLResponse:
        return HTMLResponse(_preview_page(main_module))

    setattr(main_module, "_gpx_preview_map_ext_installed", True)
    print("[gpx-preview] Vista GPX mejorada con mapa real embebido")
