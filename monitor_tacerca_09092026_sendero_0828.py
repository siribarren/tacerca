#!/usr/bin/env python3
"""Monitor dedicado para Cond. El Sendero -> Metro Escuela Militar."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

from monitor_tacerca import (
    TacercaClient,
    attempt_booking,
    available_seats,
    env_str,
    load_state,
    normalize_trip_id,
    save_state,
    trip_departure_hour,
)


TARGET_DATE = "09-09-2026"
TARGET_HOUR = "08:28"
ORIGIN_ID = "64f526d7c82f4c1fbbbccbb6"
DESTINATION_ID = "654285ab8ef632cd5d0632dc"
ROUTE_LABEL = "Cond. El Sendero -> Metro Escuela Militar"
STATE_FILE = Path("tacerca_state_09092026_sendero_0828.json")
DEFAULT_POLL_SECONDS = 300


def run_once(client: TacercaClient) -> bool:
    state = load_state(STATE_FILE)
    trips = client.fetch_trips(TARGET_DATE, ORIGIN_ID, DESTINATION_ID)
    candidates = [
        trip for trip in trips
        if trip_departure_hour(trip) == TARGET_HOUR
    ]
    candidates.sort(key=lambda trip: (available_seats(trip) <= 0, normalize_trip_id(trip)))

    if not candidates:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] No existe viaje a las {TARGET_HOUR}.")
        return False

    trip = candidates[0]
    available = available_seats(trip)
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {TARGET_DATE} {TARGET_HOUR}: "
        f"{available} asiento(s) disponible(s)."
    )

    if available <= 0:
        return False

    try:
        attempt_booking(client, TARGET_DATE, trip, state, STATE_FILE)
    except Exception as exc:
        # No se envía Telegram por un fallo de intento; solo se registra para reintentar.
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Reserva fallida: {exc}", file=sys.stderr)
        state["last_booking_error"] = {
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "trip_id": normalize_trip_id(trip),
            "error": str(exc),
        }
        save_state(STATE_FILE, state)
        return False

    return True


def main() -> int:
    poll_seconds = int(
        os.getenv("TACERCA_09092026_0828_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))
    )
    if poll_seconds < 1:
        raise RuntimeError("TACERCA_09092026_0828_POLL_SECONDS debe ser mayor o igual a 1.")
    if not env_str("TELEGRAM_BOT_TOKEN") or not env_str("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env.")

    client = TacercaClient()
    client.origin_id = ORIGIN_ID
    client.destination_id = DESTINATION_ID
    client.type_payment = "wallet"
    client.type_trip = "justReturn"
    client.login()

    print(f"Monitor Tacerca: {ROUTE_LABEL}")
    print(f"Fecha: {TARGET_DATE} | Hora: {TARGET_HOUR}")
    print(f"Consulta cada {poll_seconds} segundo(s)")
    print(f"Estado local: {STATE_FILE}")

    while True:
        try:
            run_once(client)
        except requests.HTTPError as exc:
            print(f"HTTP error: {exc}", file=sys.stderr)
            if exc.response is not None:
                print(exc.response.text[:2000], file=sys.stderr)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
