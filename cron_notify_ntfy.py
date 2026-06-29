#!/usr/bin/env python3
"""Compatibilidad temporal.

Render todavía puede estar ejecutando `python cron_notify_ntfy.py`.
Para no tocar el comando del Cron Job, este archivo delega directamente en
`cron_notify_pushover.py` y ya no publica nada en ntfy.
"""
from __future__ import annotations

from cron_notify_pushover import main


if __name__ == "__main__":
    raise SystemExit(main())
