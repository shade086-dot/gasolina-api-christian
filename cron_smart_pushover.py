#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import main as api
import cron_notify_pushover as notify

STATE_FILE = Path(os.getenv("SMART_PUSHOVER_STATE_FILE", "/tmp/gasolina_smart_pushover_state.json"))
# El cron suele ir cada 15 min. Con 18 min podía avisar dos veces si el estado no persistía entre runs.
# 9 min mantiene margen suficiente y evita repeticiones continuas en la cadencia de 15 min.
TOLERANCE_MIN = int(os.getenv("SMART_TRIGGER_TOLERANCE_MIN", "9"))
LOOKAHEAD_HOURS = int(os.getenv("SMART_LOOKAHEAD_HOURS", "36"))
# Importante: para avisar de vueltas hay eventos que empezaron horas antes.
# Si solo buscamos desde ahora-18min, el evento de oficina de la mañana desaparece
# y nunca se detecta su hora de fin. Por defecto miramos 14h hacia atrás.
LOOKBACK_HOURS = int(os.getenv("SMART_LOOKBACK_HOURS", "14"))
FORUS_OUT_NOTICE_MIN = int(os.getenv("SMART_FORUS_OUT_NOTICE_MIN", "75"))
FORUS_RETURN_NOTICE_MIN = int(os.getenv("SMART_FORUS_RETURN_NOTICE_MIN", "0"))
FORUS_BLOCK_GAP_MIN = int(os.getenv("SMART_FORUS_BLOCK_GAP_MIN", "45"))
OFFICE_OUT_NOTICE_MIN = int(os.getenv("SMART_OFFICE_OUT_NOTICE_MIN", "60"))
OFFICE_RETURN_NOTICE_MIN = int(os.getenv("SMART_OFFICE_RETURN_NOTICE_MIN", "0"))
TRAVEL_NOTICE_MIN = int(os.getenv("SMART_TRAVEL_NOTICE_MIN", "120"))
TRAVEL_DAY_NOTICE_HOUR = int(os.getenv("SMART_TRAVEL_DAY_NOTICE_HOUR", "9"))
DRY_RUN = os.getenv("SMART_DRY_RUN", "false").lower() in {"1", "true", "yes"}
DEBUG = os.getenv("SMART_DEBUG", "false").lower() in {"1", "true", "yes"}


def _now() -> datetime:
    return datetime.now(api.local_tz())


def _load_state() -> dict[str, str]:
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
    except Exception:
        pass
    return {}


def _save_state(state: dict[str, str]) -> None:
    try:
        cutoff = _now() - timedelta(days=int(os.getenv("SMART_STATE_KEEP_DAYS", "10")))
        clean: dict[str, str] = {}
        for key, value in state.items():
            try:
                ts = datetime.fromisoformat(value)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=api.local_tz())
                if ts >= cutoff:
                    clean[key] = value
            except Exception:
                clean[key] = value
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[smart] No pude guardar estado anti-duplicados: {type(exc).__name__}: {exc}")


def _hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _event_id(event: dict[str, Any]) -> str:
    text = "|".join(str(event.get(k) or "") for k in ("summary", "location", "description"))
    start = event.get("start")
    end = event.get("end")
    return _hash_text(f"{start}|{end}|{text}")


def _inside_window(now: datetime, target: datetime, tolerance_min: int = TOLERANCE_MIN) -> bool:
    return target <= now <= target + timedelta(minutes=tolerance_min)


def _keywords() -> tuple[list[str], list[str]]:
    forus = api.keyword_list("CAL_FORUS_KEYWORDS", ["forus", "gimnasio", "natacion", "natación", "padel", "pádel", "zumba"])
    office = api.keyword_list("CAL_CABANILLAS_KEYWORDS", ["cabanillas", "dsv", "guadalajara", "azuqueca", "oficina"])
    return forus, office


def _event_dt(event: dict[str, Any], key: str) -> datetime | None:
    value = event.get(key)
    return value if isinstance(value, datetime) else None


def _debug_events(events: list[dict[str, Any]], now: datetime) -> None:
    if not DEBUG:
        return
    forus_keywords, office_keywords = _keywords()
    print(f"[smart-debug] Ahora: {now.isoformat()} · eventos={len(events)}")
    for event in events:
        start = _event_dt(event, "start")
        end = _event_dt(event, "end")
        summary = str(event.get("summary") or "")
        location = str(event.get("location") or "")
        is_forus = api.event_matches(event, forus_keywords)
        is_office = api.event_matches(event, office_keywords)
        print(
            "[smart-debug] "
            f"start={start} end={end} forus={is_forus} office={is_office} "
            f"summary={summary!r} location={location!r}"
        )


def _group_forus_events(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    forus_keywords, _ = _keywords()
    items = [e for e in events if api.event_matches(e, forus_keywords) and _event_dt(e, "start")]
    items.sort(key=lambda e: _event_dt(e, "start") or datetime.max.replace(tzinfo=api.local_tz()))
    groups: list[list[dict[str, Any]]] = []
    for event in items:
        start = _event_dt(event, "start")
        if start is None:
            continue
        end = _event_dt(event, "end") or start
        if not groups:
            groups.append([event])
            continue
        last = groups[-1][-1]
        last_end = _event_dt(last, "end") or _event_dt(last, "start") or start
        same_day = start.date() == last_end.date()
        close = start <= last_end + timedelta(minutes=FORUS_BLOCK_GAP_MIN)
        if same_day and close:
            groups[-1].append(event)
        else:
            groups.append([event])
    return groups


def _is_all_day_like(start: datetime, end: datetime) -> bool:
    return start.hour == 0 and start.minute == 0 and (end - start) >= timedelta(hours=20)


def _travel_params_from_event(event: dict[str, Any]) -> dict[str, str] | None:
    text = api._event_text_for_travel_detection(event)
    if not api._contains_travel_keyword(text):
        return None
    route = api.parse_travel_route_from_text(text)
    if route:
        origin, destination = route
        return {"mode": "route", "origin": origin, "destination": destination}
    city = api.parse_travel_city_from_event(event)
    if city:
        return {"mode": "city", "city": city}
    return None


def _build_actions(events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    _forus_keywords, office_keywords = _keywords()

    # Forus: ida antes de la primera clase del bloque; vuelta justo al terminar el último evento seguido.
    for group in _group_forus_events(events):
        starts = [_event_dt(e, "start") for e in group if _event_dt(e, "start")]
        ends = [(_event_dt(e, "end") or _event_dt(e, "start")) for e in group if _event_dt(e, "start")]
        if not starts or not ends:
            continue
        first_start = min(starts)
        last_end = max(e for e in ends if e is not None)
        first_event = group[0]
        last_event = group[-1]

        out_target = first_start - timedelta(minutes=FORUS_OUT_NOTICE_MIN)
        if _inside_window(now, out_target):
            actions.append({
                "key": f"forus_out:{first_start.isoformat()}:{_event_id(first_event)}",
                "kind": "Forus ida",
                "segment": "forus_out",
                "target": out_target,
                "event": api.serialize_calendar_event(first_event),
            })

        return_target = last_end - timedelta(minutes=FORUS_RETURN_NOTICE_MIN)
        if _inside_window(now, return_target):
            actions.append({
                "key": f"forus_return:{last_end.isoformat()}:{_event_id(last_event)}",
                "kind": "Forus vuelta",
                "segment": "forus_return",
                "target": return_target,
                "event": api.serialize_calendar_event(last_event),
            })

    # Oficina: informe completo antes de empezar y al terminar el evento de oficina.
    for event in events:
        if not api.event_matches(event, office_keywords):
            continue
        start = _event_dt(event, "start")
        end = _event_dt(event, "end") or start
        if not start:
            continue
        out_target = start - timedelta(minutes=OFFICE_OUT_NOTICE_MIN)
        if _inside_window(now, out_target):
            actions.append({
                "key": f"office_out:{start.isoformat()}:{_event_id(event)}",
                "kind": "Oficina ida",
                "segment": "cabanillas_out",
                "target": out_target,
                "event": api.serialize_calendar_event(event),
            })
        if end and end > start:
            return_target = end - timedelta(minutes=OFFICE_RETURN_NOTICE_MIN)
            if _inside_window(now, return_target):
                actions.append({
                    "key": f"office_return:{end.isoformat()}:{_event_id(event)}",
                    "kind": "Oficina vuelta",
                    "segment": "cabanillas_return",
                    "target": return_target,
                    "event": api.serialize_calendar_event(event),
                })

    # Viajes/vacaciones: sin lugar no hace nada; con ciudad => mode=city; origen→destino => mode=route.
    for event in events:
        start = _event_dt(event, "start")
        end = _event_dt(event, "end") or start
        if not start:
            continue
        params = _travel_params_from_event(event)
        if not params:
            continue
        if end and _is_all_day_like(start, end):
            target = start.replace(hour=TRAVEL_DAY_NOTICE_HOUR, minute=0, second=0, microsecond=0)
        else:
            target = start - timedelta(minutes=TRAVEL_NOTICE_MIN)
        if _inside_window(now, target):
            label = params.get("city") or f"{params.get('origin')} → {params.get('destination')}"
            actions.append({
                "key": f"travel:{params.get('mode')}:{target.isoformat()}:{_hash_text(json.dumps(params, ensure_ascii=False, sort_keys=True))}",
                "kind": f"Viaje {label}",
                "travel": params,
                "target": target,
                "event": api.serialize_calendar_event(event),
            })

    actions.sort(key=lambda a: a.get("target") or now)
    return actions


def _recommend_url_for_action(action: dict[str, Any]) -> str:
    if action.get("travel"):
        params = dict(action["travel"])
    else:
        params = {"segment": str(action["segment"])}
    return f"{notify.API_BASE_URL}/recommend?{api.urlencode(params)}"


def _send_action(action: dict[str, Any]) -> None:
    data = notify.get_json(_recommend_url_for_action(action), timeout=300)
    if data.get("status") != "ok":
        notify.post_pushover("Gasolina calendario error", json.dumps(data, ensure_ascii=False)[:1000])
        return
    title, message, map_url = notify.build_message(data)
    title = f"🤖 {action.get('kind')} · {title}"
    message = f"Calendario inteligente V11: {action.get('kind')}\n\n{message}"
    if DRY_RUN:
        print(f"[smart] DRY RUN: {title}\n{message[:800]}")
        return
    print(notify.post_pushover(title, message, click_url=map_url))


async def amain() -> int:
    now = _now()
    start = now - timedelta(hours=LOOKBACK_HOURS)
    end = now + timedelta(hours=LOOKAHEAD_HOURS)
    events = await api.fetch_public_calendar_events_for_range(start, end)
    _debug_events(events, now)
    actions = _build_actions(events, now)
    state = _load_state()

    pending = [a for a in actions if a["key"] not in state]
    if not pending:
        print(f"[smart] Sin avisos. Eventos revisados: {len(events)}. Acciones candidatas: {len(actions)}. Lookback: {LOOKBACK_HOURS}h. Tolerancia: {TOLERANCE_MIN}min.")
        _save_state(state)
        return 0

    action = pending[0]
    print(f"[smart] Aviso seleccionado: {action.get('kind')} · {action.get('key')}")
    _send_action(action)
    state[action["key"]] = now.isoformat(timespec="seconds")
    _save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
