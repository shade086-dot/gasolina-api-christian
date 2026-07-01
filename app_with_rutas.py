"""ASGI entrypoint que registra extensiones antes de exponer app.

Usar en Render como Start Command:
    uvicorn app_with_rutas:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import main
import gpx_routes_ext
import maintenance_color_ext
import visual_route_ext

maintenance_color_ext.install(main)
visual_route_ext.install(main)
gpx_routes_ext.install_routes(main)

app = main.app
