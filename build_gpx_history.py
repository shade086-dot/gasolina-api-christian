#!/usr/bin/env python3
"""Genera el histórico visual de rutas desde GPX.

Uso:
    python build_gpx_history.py

Entrada soportada:
    gpx_historial/*.gpx
    gpx_historial/*.zip
    gpx_historial/*.zip.b64

Salida:
    static/rutas_indice.json
    static/rutas_resumen.txt
    static/rutas_mapa.html
"""
from __future__ import annotations

import base64
import json
import math
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
GPX_DIR = ROOT / "gpx_historial"
STATIC_DIR = ROOT / "static"


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def _lat_lon(elem: ET.Element) -> tuple[float, float] | None:
    try:
        return float(elem.attrib["lat"]), float(elem.attrib["lon"])
    except Exception:
        return None


def _tag(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def parse_gpx_segments(text: str) -> list[list[tuple[float, float]]]:
    """Lee el GPX respetando segmentos reales.

    No mezclamos <wpt> con <trkpt>. Eso evita unir inicio/fin o cortes de señal
    con líneas rectas raras.
    """
    root = ET.fromstring(text.encode("utf-8"))
    segments: list[list[tuple[float, float]]] = []

    for trkseg in [e for e in root.iter() if _tag(e) == "trkseg"]:
        pts = []
        for child in trkseg:
            if _tag(child) != "trkpt":
                continue
            point = _lat_lon(child)
            if point is not None:
                pts.append(point)
        if len(pts) >= 2:
            segments.append(pts)
    if segments:
        return segments

    for rte in [e for e in root.iter() if _tag(e) == "rte"]:
        pts = []
        for child in rte:
            if _tag(child) != "rtept":
                continue
            point = _lat_lon(child)
            if point is not None:
                pts.append(point)
        if len(pts) >= 2:
            segments.append(pts)
    if segments:
        return segments

    pts = []
    for elem in root.iter():
        if _tag(elem) != "wpt":
            continue
        point = _lat_lon(elem)
        if point is not None:
            pts.append(point)
    return [pts] if len(pts) >= 2 else []


def read_gpx_sources() -> list[tuple[str, str]]:
    GPX_DIR.mkdir(exist_ok=True)
    sources: list[tuple[str, str]] = []

    for path in sorted(GPX_DIR.glob("*.gpx")):
        sources.append((path.name, path.read_text(encoding="utf-8", errors="replace")))

    for zip_path in sorted(GPX_DIR.glob("*.zip")):
        with zipfile.ZipFile(zip_path) as zf:
            for name in sorted(zf.namelist()):
                if name.lower().endswith(".gpx"):
                    sources.append((Path(name).name, zf.read(name).decode("utf-8", errors="replace")))

    for b64_path in sorted(GPX_DIR.glob("*.zip.b64")):
        data = base64.b64decode(b64_path.read_text(encoding="utf-8"))
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            tmp.write(data)
            tmp.flush()
            with zipfile.ZipFile(tmp.name) as zf:
                for name in sorted(zf.namelist()):
                    if name.lower().endswith(".gpx"):
                        sources.append((Path(name).name, zf.read(name).decode("utf-8", errors="replace")))

    dedup: dict[str, str] = {}
    for name, text in sources:
        dedup[name] = text
    return sorted(dedup.items(), key=lambda item: item[0].lower())


def route_group(name: str, bbox: list[float]) -> str:
    min_lat, min_lon, max_lat, max_lon = bbox
    low = name.lower()
    if "3flwm" in low:
        return "Ruta 22 nueva"
    if "estremera" in low:
        return "Alcarria / Estremera"
    if max_lat >= 42.0 or max_lon <= -3.6:
        return "Corredor norte / Burgos / Cantabria / Bizkaia"
    if max_lon <= -2.6 and min_lon >= -3.4:
        return "Alcarria / Cifuentes-Trillo"
    if max_lat >= 40.85 and min_lon >= -3.5:
        return "Sierra Norte / Pueblos Negros"
    if min_lat <= 40.38:
        return "Sur/suroeste de Anchuelo"
    if max_lon <= -3.12 and min_lon >= -3.5:
        return "Local Anchuelo / Alcalá"
    return "Rutas realizadas"


def simplify_points(points: list[tuple[float, float]], limit: int = 850) -> list[list[float]]:
    if len(points) <= limit:
        selected = points
    else:
        step = max(1, math.ceil(len(points) / limit))
        selected = points[::step]
        if selected[-1] != points[-1]:
            selected.append(points[-1])
    return [[round(lat, 6), round(lon, 6)] for lat, lon in selected]


def simplify_segments(segments: list[list[tuple[float, float]]], total_limit: int = 850) -> list[list[list[float]]]:
    total_points = sum(len(s) for s in segments)
    simplified: list[list[list[float]]] = []
    for segment in segments:
        if len(segment) < 2:
            continue
        share = max(20, round(total_limit * (len(segment) / max(total_points, 1))))
        simplified.append(simplify_points(segment, share))
    return simplified


def build_index() -> tuple[list[dict], dict[str, list[list[list[float]]]]]:
    routes: list[dict] = []
    polylines: dict[str, list[list[list[float]]]] = {}
    for name, text in read_gpx_sources():
        try:
            segments = parse_gpx_segments(text)
        except Exception as exc:
            print(f"[warn] No se pudo leer {name}: {type(exc).__name__}: {exc}")
            continue
        points = [p for segment in segments for p in segment]
        if len(points) < 2:
            continue
        distance = sum(haversine_km(a, b) for segment in segments for a, b in zip(segment, segment[1:]))
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        bbox = [min(lats), min(lons), max(lats), max(lons)]
        route = {
            "name": name,
            "distance_km": round(distance, 1),
            "bbox": [round(x, 6) for x in bbox],
            "start": [round(segments[0][0][0], 6), round(segments[0][0][1], 6)],
            "end": [round(segments[-1][-1][0], 6), round(segments[-1][-1][1], 6)],
            "points": len(points),
            "segments": len(segments),
            "group": route_group(name, bbox),
            "road_correct": "estremera" in name.lower(),
        }
        routes.append(route)
        polylines[name] = simplify_segments(segments)
    return routes, polylines


def write_summary(routes: list[dict]) -> None:
    total = sum(float(r["distance_km"]) for r in routes)
    lines = [
        f"Historial GPX completo cargado: {len(routes)} rutas/tramos",
        f"Distancia acumulada aproximada: {total:.1f} km",
        "",
    ]
    for idx, route in enumerate(routes, 1):
        segments = int(route.get("segments") or 1)
        extra = " — trazado por carretera en mapa" if route.get("road_correct") else ""
        lines.append(f"{idx}. {route['name']} — {route['distance_km']:.1f} km — {route['group']} — {route['points']} puntos — {segments} segmentos{extra}")
    (STATIC_DIR / "rutas_resumen.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_map(routes: list[dict], polylines: dict[str, list[list[list[float]]]]) -> None:
    total = sum(float(r["distance_km"]) for r in routes)
    routes_json = json.dumps(routes, ensure_ascii=False, separators=(",", ":"))
    polylines_json = json.dumps(polylines, ensure_ascii=False, separators=(",", ":"))
    page = f"""<!doctype html>
<html lang='es'>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>Mapa historial GPX · {len(routes)} rutas</title>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script>
<style>
html,body,#map{{height:100%;margin:0}} body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
.panel{{position:absolute;top:12px;left:54px;z-index:9999;background:#fff;padding:12px 14px;border-radius:12px;border:1px solid #dbe4ee;box-shadow:0 4px 18px rgba(0,0,0,.18);max-width:460px}}
.panel h1{{font-size:18px;margin:0 0 4px}} .panel p{{margin:2px 0;color:#475569;font-size:13px}}
.legend{{position:absolute;right:12px;top:12px;z-index:9999;background:#fff;border:1px solid #dbe4ee;border-radius:12px;padding:10px;box-shadow:0 4px 18px rgba(0,0,0,.14);font-size:12px;max-height:72vh;overflow:auto}}
.legend div{{margin:4px 0;white-space:nowrap}} .sw{{display:inline-block;width:18px;height:4px;border-radius:6px;margin-right:7px;vertical-align:middle}}
</style>
</head>
<body>
<div id='map'></div>
<div class='panel'><h1>🏍️ Historial de rutas moteras GPX</h1><p><b>{len(routes)} rutas/tramos</b> · {total:.1f} km aprox.</p><p>Mapa desde GPX. En rutas reconstruidas, como Estremera, se corrige la línea con OSRM para seguir carretera.</p></div>
<div id='legend' class='legend'><b>Rutas</b></div>
<script>
const routes={routes_json};
const polylines={polylines_json};
const colors=['#2563eb','#f97316','#16a34a','#dc2626','#9333ea','#0f766e','#be123c','#64748b','#ca8a04','#0891b2','#7c3aed','#b45309'];
const groupColors={{}};
const map=L.map('map').setView([40.7,-3.25],8);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}}).addTo(map);
const bounds=[];
const legend=document.getElementById('legend');
function colorFor(group){{ if(!groupColors[group]) groupColors[group]=colors[Object.keys(groupColors).length%colors.length]; return groupColors[group]; }}
function normalizedSegments(raw){{
  if(!Array.isArray(raw)) return [];
  if(raw.length && Array.isArray(raw[0]) && typeof raw[0][0]==='number') return [raw];
  return raw.filter(s=>Array.isArray(s) && s.length>=2);
}}
function flattenSegments(segments){{ return segments.flat().filter(p=>Array.isArray(p) && p.length>=2); }}
function sampleWaypoints(points, maxPoints){{
  if(points.length <= maxPoints) return points;
  const out=[];
  for(let i=0;i<maxPoints;i++){{
    out.push(points[Math.round(i*(points.length-1)/(maxPoints-1))]);
  }}
  return out;
}}
async function fetchRoadChunk(chunk){{
  const coords=chunk.map(p=>Number(p[1]).toFixed(6)+','+Number(p[0]).toFixed(6)).join(';');
  const url='/visual-route-osrm?coords='+encodeURIComponent(coords);
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),6500);
  const response=await fetch(url,{{signal:controller.signal,cache:'no-store'}});
  clearTimeout(timer);
  if(!response.ok) throw new Error('OSRM '+response.status);
  const data=await response.json();
  const geometry=data && data.routes && data.routes[0] && data.routes[0].geometry && data.routes[0].geometry.coordinates;
  if(!Array.isArray(geometry) || geometry.length<2) throw new Error('sin geometria');
  return geometry.map(c=>[c[1],c[0]]).filter(p=>Number.isFinite(p[0]) && Number.isFinite(p[1]));
}}
async function drawRoadCorrected(r, c, segments){{
  const raw=flattenSegments(segments);
  raw.forEach(p=>bounds.push(p));
  const waypoints=sampleWaypoints(raw, 16);
  const all=[];
  try{{
    for(let i=0;i<waypoints.length-1;i+=5){{
      const chunk=waypoints.slice(i, Math.min(i+6, waypoints.length));
      if(chunk.length<2) continue;
      const pts=await fetchRoadChunk(chunk);
      if(all.length && pts.length) pts.shift();
      all.push(...pts);
    }}
    if(all.length>=2){{
      const line=L.polyline(all,{{color:c,weight:5,opacity:.9}}).addTo(map);
      line.bindPopup(`<b>${{r.name}}</b><br>Grupo: ${{r.group}}<br>Distancia aprox.: ${{r.distance_km}} km<br>Trazado ajustado a carretera`);
      all.forEach(p=>bounds.push(p));
      if(bounds.length) map.fitBounds(bounds,{{padding:[30,30]}});
      return;
    }}
  }}catch(err){{ console.log('No pude corregir por carretera', r.name, err && err.message ? err.message : err); }}
  // Fallback: no vuelve a pintar el mallado entero; solo una aproximación muy discreta para no ensuciar el mapa.
  const fallback=sampleWaypoints(raw, 12);
  if(fallback.length>=2){{
    const line=L.polyline(fallback,{{color:c,weight:3,opacity:.45,dashArray:'6,8'}}).addTo(map);
    line.bindPopup(`<b>${{r.name}}</b><br>Vista aproximada. Falta GPX real para trazado perfecto.`);
  }}
}}
routes.forEach((r)=>{{
  const c=colorFor(r.group||'Rutas');
  const segments=normalizedSegments(polylines[r.name]||[]);
  if(r.road_correct){{ drawRoadCorrected(r,c,segments); return; }}
  segments.forEach((pts)=>{{
    if(pts.length>=2){{
      const line=L.polyline(pts,{{color:c,weight:4,opacity:.76}}).addTo(map);
      line.bindPopup(`<b>${{r.name}}</b><br>Grupo: ${{r.group}}<br>Distancia aprox.: ${{r.distance_km}} km<br>Puntos GPX: ${{r.points}}<br>Segmentos: ${{r.segments||1}}`);
      pts.forEach(p=>bounds.push(p));
    }}
  }});
}});
Object.entries(groupColors).forEach(([g,c])=>{{
  const div=document.createElement('div');
  div.innerHTML=`<span class='sw' style='background:${{c}}'></span>${{g}}`;
  legend.appendChild(div);
}});
if(bounds.length) map.fitBounds(bounds,{{padding:[30,30]}});
</script>
</body>
</html>"""
    (STATIC_DIR / "rutas_mapa.html").write_text(page, encoding="utf-8")


def main() -> int:
    STATIC_DIR.mkdir(exist_ok=True)
    routes, polylines = build_index()
    if not routes:
        print("No se encontraron GPX en gpx_historial/. Sube .gpx, .zip o .zip.b64 y vuelve a ejecutar.")
        return 1
    (STATIC_DIR / "rutas_indice.json").write_text(json.dumps(routes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_summary(routes)
    write_map(routes, polylines)
    print(f"OK: {len(routes)} rutas · {sum(float(r['distance_km']) for r in routes):.1f} km")
    print("Actualizados: static/rutas_indice.json, static/rutas_resumen.txt, static/rutas_mapa.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
