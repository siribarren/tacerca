#!/usr/bin/env python3
"""
Consulta puntual Tacerca.

Pregunta por disponibilidad para:
- Fecha: 04-05-2026
- Zona/ruta: Piedra Roja, Los Montes -> Escuela Militar
- Hora salida: 06:54

No reserva. Solo notifica por Telegram si encuentra un viaje con cupos.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()


def env_or_default(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


BASE_URL = "https://backend.tacerca.cl/api"
LOGIN_URL = f"{BASE_URL}/auth/login"
BOOKING_URL = f"{BASE_URL}/planning-trip/get-booking"

TARGET_DATE = env_or_default("TACERCA_QUERY_DATE", "04-05-2026")
TARGET_HOUR = env_or_default("TACERCA_QUERY_HOUR", "06:54")

ORIGIN_ID = env_or_default("TACERCA_ORIGIN_ID", "64f5268fc82f4c1fbbbccba2")
DESTINATION_ID = env_or_default("TACERCA_DESTINATION_ID", "654285ab8ef632cd5d0632dc")

ROUTE_LABEL = "Piedra Roja / Los Montes -> Escuela Militar"
STATE_FILE = Path("tacerca_query_04052026_0654_state.json")
NO_RESERVATIONS_MESSAGE = "No se activan las reservas"
REQUEST_TIMEOUT = 30

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://tacerca.cl",
    "Referer": "https://tacerca.cl/",
    "User-Agent": "Mozilla/5.0",
}


def env_str(name: str) -> str:
    return os.getenv(name, "").strip()


def normalize_hhmm(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.strptime(raw[:5], "%H:%M")
    except ValueError:
        return raw
    return parsed.strftime("%H:%M")


def validate_date(date_str: str) -> None:
    datetime.strptime(date_str, "%d-%m-%Y")


def date_to_unix_ms(date_str: str) -> int:
    validate_date(date_str)
    dt = datetime.strptime(date_str, "%d-%m-%Y")
    return int(dt.timestamp() * 1000)


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def trip_id(trip: dict[str, Any]) -> str:
    active_trip = trip.get("activeTrip")
    if isinstance(active_trip, dict):
        return str(first_non_empty(active_trip.get("_id"), active_trip.get("id"), trip.get("_id"), ""))
    return str(first_non_empty(active_trip, trip.get("_id"), ""))


def trip_departure_hour(trip: dict[str, Any]) -> str:
    return normalize_hhmm(first_non_empty(trip.get("hourInit"), trip.get("hourDeparture")))


def trip_arrival_hour(trip: dict[str, Any]) -> str:
    return normalize_hhmm(first_non_empty(trip.get("hourFinish"), trip.get("hourArrival")))


def int_or_zero(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if isinstance(value, list):
        return len(value)
    return 0


def total_seats(trip: dict[str, Any]) -> int:
    vehicle = trip.get("vehicle")
    if isinstance(vehicle, dict):
        count = first_non_empty(vehicle.get("seatings"), vehicle.get("totalSeats"), vehicle.get("capacity"))
        parsed = int_or_zero(count)
        if parsed:
            return parsed

    return int_or_zero(first_non_empty(trip.get("totalSeats"), trip.get("capacity")))


def occupied_seats(trip: dict[str, Any]) -> int:
    seatings = trip.get("seatings", [])
    if isinstance(seatings, list):
        return len(seatings)
    return int_or_zero(seatings)


def available_seats(trip: dict[str, Any]) -> int:
    total = total_seats(trip)
    if total <= 0:
        return 0
    return max(total - occupied_seats(trip), 0)


def trip_price(trip: dict[str, Any]) -> int:
    return int_or_zero(first_non_empty(trip.get("price"), trip.get("totalPrice")))


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def login(session: requests.Session) -> None:
    username = env_str("TACERCA_USERNAME")
    password = env_str("TACERCA_PASSWORD")
    if not username or not password:
        raise RuntimeError("Faltan TACERCA_USERNAME o TACERCA_PASSWORD en .env.")

    response = session.post(
        LOGIN_URL,
        json={"username": username, "password": password},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    payload = response.json()
    token = payload.get("data", {}).get("accessToken")
    if not token:
        raise RuntimeError(f"No se encontró accessToken en la respuesta: {payload}")

    session.headers["Authorization"] = f"Bearer {token}"


def fetch_trips(session: requests.Session, trip_date: str) -> list[dict[str, Any]]:
    response = session.get(
        BOOKING_URL,
        params={
            "origin": ORIGIN_ID,
            "destination": DESTINATION_ID,
            "date": str(date_to_unix_ms(trip_date)),
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    payload = response.json()
    trips = payload.get("data")
    if not isinstance(trips, list):
        raise RuntimeError(f"Respuesta inesperada del backend en get-booking: {payload}")
    return trips


def target_trips(trips: list[dict[str, Any]], target_hour: str) -> list[dict[str, Any]]:
    normalized_target_hour = normalize_hhmm(target_hour)
    return [trip for trip in trips if trip_departure_hour(trip) == normalized_target_hour]


def available_target_trips(trips: list[dict[str, Any]], target_hour: str) -> list[dict[str, Any]]:
    matches = [trip for trip in target_trips(trips, target_hour) if available_seats(trip) > 0]
    matches.sort(key=lambda trip: (trip_departure_hour(trip), trip_id(trip)))
    return matches


def build_notification_message(trip_date: str, target_hour: str, trip: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Tacerca: cupos disponibles",
            f"Ruta: {ROUTE_LABEL}",
            f"Fecha: {trip_date}",
            f"Horario: {trip_departure_hour(trip)} -> {trip_arrival_hour(trip)}",
            f"Disponibles: {available_seats(trip)}",
            f"Tarifa: ${trip_price(trip)}",
            f"Trip ID: {trip_id(trip)}",
            f"Consulta objetivo: {target_hour}",
        ]
    )


def send_telegram(message: str) -> None:
    token = env_str("TELEGRAM_BOT_TOKEN")
    chat_id = env_str("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env.")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=20,
    )
    response.raise_for_status()


def notify_no_reservations(timestamp: str, reason: str) -> None:
    print(f"[{timestamp}] {reason} Enviando Telegram: {NO_RESERVATIONS_MESSAGE}")
    send_telegram(NO_RESERVATIONS_MESSAGE)


def availability_signature(trips: list[dict[str, Any]]) -> str:
    compact = [
        {
            "trip_id": trip_id(trip),
            "hour": trip_departure_hour(trip),
            "available": available_seats(trip),
        }
        for trip in trips
    ]
    return json.dumps(compact, sort_keys=True, ensure_ascii=False)


def run_once(notify_once: bool = True) -> bool:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    login(session)
    trips = fetch_trips(session, TARGET_DATE)
    matches = target_trips(trips, TARGET_HOUR)
    available = available_target_trips(trips, TARGET_HOUR)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not matches:
        notify_no_reservations(
            timestamp,
            f"No existe viaje a las {TARGET_HOUR} para {TARGET_DATE}.",
        )
        return False

    if not available:
        notify_no_reservations(
            timestamp,
            f"Existe viaje a las {TARGET_HOUR}, pero no tiene cupos disponibles.",
        )
        return False

    message = build_notification_message(TARGET_DATE, TARGET_HOUR, available[0])
    state: dict[str, Any] = {}
    signature = ""
    if notify_once:
        state = load_state()
        signature = availability_signature(available)
        if state.get("last_notified_signature") == signature:
            print(f"[{timestamp}] Cupos disponibles ya notificados por Telegram.")
            return True

    print(f"[{timestamp}] Cupos disponibles. Enviando Telegram...")
    send_telegram(message)

    if notify_once:
        state["last_notified_signature"] = signature
        state["last_notified_at"] = timestamp
        state["target_date"] = TARGET_DATE
        state["target_hour"] = TARGET_HOUR
        save_state(state)

    print(message)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consulta Tacerca 04-05-2026 06:54 Los Montes -> Escuela Militar."
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Repetir la consulta hasta detener con Ctrl+C.",
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=int(os.getenv("TACERCA_QUERY_POLL_SECONDS", "60")),
        help="Segundos entre consultas cuando se usa --watch.",
    )
    parser.add_argument(
        "--notify-every-run",
        action="store_true",
        help="Enviar Telegram cada vez que encuentre cupos, sin deduplicar por estado local.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    notify_once = not args.notify_every_run

    if args.poll < 1:
        raise RuntimeError("--poll debe ser mayor o igual a 1.")

    try:
        if not args.watch:
            run_once(notify_once=notify_once)
            return 0

        print(f"Consultando {ROUTE_LABEL}, {TARGET_DATE} {TARGET_HOUR}, cada {args.poll}s.")
        print("No reserva; notifica por Telegram si encuentra cupos o si no se activan.")
        print("Presiona Ctrl+C para detener.")
        while True:
            run_once(notify_once=notify_once)
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\nConsulta detenida por el usuario.")
        return 0
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        if exc.response is not None:
            print(exc.response.text[:2000], file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
