#!/usr/bin/env python3
"""
Monitor Tacerca dedicado.

Objetivo:
- Fecha fija: 03-09-2026
- Ruta fija: Piedra Roja / Cond. Los Montes -> Metro Escuela Militar
- Horarios objetivo: 06:35, 06:55, 08:25
- Consulta cada 5 minutos
- Si encuentra al menos 1 asiento disponible en cualquiera de esos horarios,
  avisa por Telegram.
- Si no encuentra asientos, no informa nada.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from monitor_tacerca import (
    DEFAULT_DESTINATION_ID,
    DEFAULT_ORIGIN_ID,
    TacercaClient,
    available_seats,
    env_str,
    load_state,
    normalize_trip_id,
    save_state,
    send_telegram,
    trip_arrival_hour,
    trip_departure_hour,
)

TARGET_DATE = "03-09-2026"
TARGET_HOURS = ("06:35", "06:55", "08:25")
ROUTE_LABEL = "Piedra Roja / Cond. Los Montes -> Metro Escuela Militar"
STATE_FILE = Path("tacerca_state_03092026_0635_0655_0825.json")

DEFAULT_POLL_SECONDS = 300
TELEGRAM_RETRY_COUNT = 3
TELEGRAM_RETRY_DELAY_SECONDS = 5


def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def require_telegram_config() -> None:
    token = env_str("TELEGRAM_BOT_TOKEN")
    chat_id = env_str("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno del proceso."
        )


def append_available_state(state: dict[str, Any], available_trips: list[dict[str, Any]]) -> str:
    compact = [
        {
            "trip_id": normalize_trip_id(trip),
            "hour": trip_departure_hour(trip),
            "available": available_seats(trip),
        }
        for trip in available_trips
    ]
    signature = json.dumps(compact, sort_keys=True, ensure_ascii=False)
    state["last_available_signature"] = signature
    state["last_available_at"] = now_ts()
    state["target_date"] = TARGET_DATE
    state["target_hours"] = list(TARGET_HOURS)
    save_state(STATE_FILE, state)
    return signature


def build_available_message(available_trips: list[dict[str, Any]]) -> str:
    lines = [
        "Tacerca 03-09-2026 - asiento disponible detectado",
        f"Ruta: {ROUTE_LABEL}",
        f"Fecha: {TARGET_DATE}",
        f"Horarios objetivo: {', '.join(TARGET_HOURS)}",
        "Coincidencias:",
    ]

    for trip in available_trips:
        lines.append(
            f"- {trip_departure_hour(trip)} -> {trip_arrival_hour(trip)} | "
            f"disponibles: {available_seats(trip)} | Trip ID: {normalize_trip_id(trip)}"
        )

    lines.append("Accion: revisar y reservar usando la sesion almacenada de Simon Iribarren.")
    return "\n".join(lines)


def safe_send_telegram(message: str) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, TELEGRAM_RETRY_COUNT + 1):
        try:
            send_telegram(message)
            return
        except Exception as exc:
            last_exc = exc
            if attempt < TELEGRAM_RETRY_COUNT:
                time.sleep(TELEGRAM_RETRY_DELAY_SECONDS)

    if last_exc is not None:
        raise last_exc


def run_once(client: TacercaClient) -> bool:
    state = load_state(STATE_FILE)
    trips = client.fetch_trips(TARGET_DATE, DEFAULT_ORIGIN_ID, DEFAULT_DESTINATION_ID)

    available_trips = [
        trip
        for trip in trips
        if trip_departure_hour(trip) in TARGET_HOURS and available_seats(trip) > 0
    ]
    if not available_trips:
        return False

    available_trips.sort(key=lambda trip: (trip_departure_hour(trip), normalize_trip_id(trip)))
    signature = json.dumps(
        [
            {
                "trip_id": normalize_trip_id(trip),
                "hour": trip_departure_hour(trip),
                "available": available_seats(trip),
            }
            for trip in available_trips
        ],
        sort_keys=True,
        ensure_ascii=False,
    )
    if state.get("last_available_signature") == signature:
        return True

    message = build_available_message(available_trips)
    safe_send_telegram(message)
    print(message)
    append_available_state(state, available_trips)
    return True


def main() -> int:
    poll_seconds = int(os.getenv("TACERCA_03092026_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))
    if poll_seconds < 1:
        raise RuntimeError("TACERCA_03092026_POLL_SECONDS debe ser mayor o igual a 1.")

    require_telegram_config()

    client = TacercaClient()
    client.origin_id = DEFAULT_ORIGIN_ID
    client.destination_id = DEFAULT_DESTINATION_ID
    client.type_payment = "wallet"
    client.login()

    print("Tacerca monitor 03-09-2026 06:35/06:55/08:25")
    print(f"Ruta: {ROUTE_LABEL}")
    print(f"Consulta cada {poll_seconds} segundo(s)")
    print(f"Estado local: {STATE_FILE}")

    try:
        while True:
            try:
                run_once(client)
            except requests.HTTPError as exc:
                print(f"HTTP error: {exc}", file=sys.stderr)
                if exc.response is not None:
                    print(exc.response.text[:2000], file=sys.stderr)
            except Exception as exc:
                print(f"[{now_ts()}] Error: {exc}", file=sys.stderr)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("\nMonitoreo detenido por el usuario.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
