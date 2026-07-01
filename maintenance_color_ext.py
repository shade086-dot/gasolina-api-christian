from __future__ import annotations

import html
import os
from typing import Any


def _threshold(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _moto_percent_from_values(km: float, interval: float) -> int:
    interval = max(1.0, float(interval or 1.0))
    km = max(0.0, float(km or 0.0))
    return max(0, min(100, round((km / interval) * 100)))


def _color_level_for_percent(pct: int, due: bool = False) -> str:
    yellow_at = _threshold("MAPIT_YELLOW_THRESHOLD_PERCENT", 50)
    orange_at = _threshold("MAPIT_ORANGE_THRESHOLD_PERCENT", 75)
    red_at = _threshold("MAPIT_RED_THRESHOLD_PERCENT", 85)
    if due or pct >= red_at:
        return "red"
    if pct >= orange_at:
        return "orange"
    if pct >= yellow_at:
        return "yellow"
    return "green"


def _alert_level_for_color(color_level: str, due: bool = False) -> str:
    if due:
        return "due"
    if color_level in {"yellow", "orange", "red"}:
        return "soon"
    return "ok"


def _palette(color_level: str) -> dict[str, str]:
    return {
        "green": {"bar": "#16a34a", "border": "#bbf7d0", "bg": "#f0fdf4"},
        "yellow": {"bar": "#eab308", "border": "#fde68a", "bg": "#fffbeb"},
        "orange": {"bar": "#f97316", "border": "#fed7aa", "bg": "#fff7ed"},
        "red": {"bar": "#dc2626", "border": "#fecaca", "bg": "#fef2f2"},
    }.get(color_level, {"bar": "#16a34a", "border": "#bbf7d0", "bg": "#f0fdf4"})


def install(main_module: Any) -> None:
    if getattr(main_module, "_maintenance_color_ext_installed", False):
        return

    def moto_counter(km_done: float, interval: float) -> dict[str, Any]:
        remaining = max(0.0, float(interval or 0.0) - float(km_done or 0.0))
        due = remaining <= 0
        pct = _moto_percent_from_values(float(km_done or 0.0), float(interval or 1.0))
        color_level = _color_level_for_percent(pct, due=due)
        return {
            "km": km_done,
            "interval": interval,
            "remaining": remaining,
            "percent": pct,
            "color_level": color_level,
            "level": _alert_level_for_color(color_level, due=due),
            "due": due,
            "soon": (not due) and color_level in {"yellow", "orange", "red"},
        }

    original_revision_counter = getattr(main_module, "_mapit_revision_counter", None)

    def moto_revision_counter(con: Any, current_odometer_km: float) -> dict[str, Any]:
        if not callable(original_revision_counter):
            return moto_counter(0, 1)
        counter = dict(original_revision_counter(con, current_odometer_km))
        interval = max(1.0, float(counter.get("interval") or 1.0))
        km = max(0.0, float(counter.get("km") or 0.0))
        pct = _moto_percent_from_values(km, interval)
        due = bool(counter.get("due"))
        color_level = _color_level_for_percent(pct, due=due)
        counter["percent"] = pct
        counter["color_level"] = color_level
        counter["level"] = _alert_level_for_color(color_level, due=due)
        counter["soon"] = (not due) and color_level in {"yellow", "orange", "red"}
        return counter

    def moto_bar(counter: dict[str, Any]) -> str:
        pct = int(counter.get("percent") if counter.get("percent") is not None else main_module._moto_percent(counter))
        color_level = str(counter.get("color_level") or _color_level_for_percent(pct, bool(counter.get("due"))))
        pal = _palette(color_level)
        return (
            '<div class="moto-progress">'
            f'<div class="moto-progress-fill" style="width:{pct}%;background:{pal["bar"]}"></div>'
            '</div>'
        )

    def moto_row(icon: str, label: str, counter: dict[str, Any]) -> str:
        km = float(counter.get("km") or 0.0)
        interval = float(counter.get("interval") or 0.0)
        remaining = float(counter.get("remaining") or 0.0)
        pct = int(counter.get("percent") if counter.get("percent") is not None else main_module._moto_percent(counter))
        due = bool(counter.get("due")) or str(counter.get("level") or "") == "due"
        color_level = str(counter.get("color_level") or _color_level_for_percent(pct, due=due))
        pal = _palette(color_level)
        klass = "due" if due else "soon" if color_level in {"yellow", "orange", "red"} else "ok"
        if due:
            state = "TOCA"
            remaining_text = "pendiente"
        else:
            state = f"{remaining:.0f} km"
            remaining_text = "restantes"
        if counter.get("target_odometer_km") is not None:
            target = float(counter.get("target_odometer_km") or 0.0)
            last = float(counter.get("last_odometer_km") or 0.0)
            meta_left = f"Próx. {target:.0f} km · {pct}%"
            meta_right = f"desde {last:.0f} km" if last else "por odómetro"
        else:
            meta_left = f"{km:.0f}/{interval:.0f} km · {pct}%"
            meta_right = remaining_text
        return f"""
          <div class=\"moto-row {klass}\" style=\"border-color:{pal['border']};background:{pal['bg']}\">
            <div class=\"moto-line\"><b>{html.escape(icon)} {html.escape(label)}</b><span>{html.escape(state)}</span></div>
            {moto_bar(counter)}
            <div class=\"moto-meta\"><span>{html.escape(meta_left)}</span><span>{html.escape(meta_right)}</span></div>
          </div>
        """

    main_module._mapit_counter = moto_counter
    main_module._mapit_revision_counter = moto_revision_counter
    main_module._moto_bar = moto_bar
    main_module._moto_row = moto_row
    setattr(main_module, "_maintenance_color_ext_installed", True)
    print("[moto] Colores de mantenimiento instalados: 50 amarillo · 75 naranja · 85 rojo")
