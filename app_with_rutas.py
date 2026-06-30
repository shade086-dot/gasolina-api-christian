"""ASGI entrypoint que registra el historial GPX antes de exponer app.

Usar en Render como Start Command:
    uvicorn app_with_rutas:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import main
import sitecustomize as rutas_ext

rutas_ext._install_routes(main)

app = main.app
