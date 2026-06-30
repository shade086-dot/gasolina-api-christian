"""Autoinstala las rutas GPX en FastAPI al arrancar Python.

Python importa usercustomize después de sitecustomize. Lo usamos para cargar main
y registrar /rutas sin tocar main.py.
"""
from __future__ import annotations

import importlib
import os


def _auto_install_rutas() -> None:
    if os.getenv("DISABLE_RUTAS_AUTOINSTALL", "").lower() in {"1", "true", "yes", "on"}:
        return
    try:
        rutas_ext = importlib.import_module("sitecustomize")
        main_module = importlib.import_module("main")
        installer = getattr(rutas_ext, "_install_routes", None)
        if callable(installer):
            installer(main_module)
            print("[rutas] Historial GPX instalado automáticamente")
    except Exception as exc:
        print(f"[rutas] No se pudo instalar automáticamente: {type(exc).__name__}: {exc}")


_auto_install_rutas()
