#!/usr/bin/env python3
"""
Monitor dedicado Tacerca:
- Fecha fija: 29-04-2026
- Ruta fija: Los Montes -> Escuela Militar
- Hora fija: 06:54

Comportamiento:
- Si hay cupo, intenta reservar de inmediato a nombre del titular.
- Fuerza pago con billetera (wallet).
- Si no hay cupos, envía aviso por Telegram cada 30 minutos.
"""

from __future__ import annotations

import argparse
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
    ReservationRule,
    TacercaClient,
    attempt_booking,
    available_seats,
    build_booking_failed_message,
    choose_target_trip,
    first_non_empty,
    load_state,
    normalize_trip_id,
    now_ts,
    maybe_notify_booking_done_from_state,
    notify_wallet_no_balance_if_needed,
    save_state,
    send_telegram,
    trip_arrival_hour,
    trip_departure_hour,
)

TARGET_DATE = "29-04-2026"
TARGET_HOUR = "06:54"
ROUTE_LABEL = "Piedra Roja / Los Montes -> Escuela Militar"
STATE_FILE = Path("tacerca_state_29042026_0654_wallet.json")

DEFAULT_POLL_SECONDS = 60
DEFAULT_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS = 1800


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} debe ser un entero válido.") from exc
    if value < 0:
        raise RuntimeError(f"{name} debe ser mayor o igual a 0.")
    return value


def build_available_message(trip: dict[str, Any]) -> str:
    price = int(first_non_empty(trip.get("price"), trip.get("totalPrice"), 0) or 0)
    return "\n".join(
        [
            "Tacerca 29-04-2026 06:54 - cupo detectado",
            f"Ruta: {ROUTE_LABEL}",
            f"Fecha: {TARGET_DATE}",
            f"Horario: {trip_departure_hour(trip)} -> {trip_arrival_hour(trip)}",
            f"Disponibles: {available_seats(trip)}",
            f"Tarifa: ${price}",
            f"Trip ID: {normalize_trip_id(trip)}",
            "Acción: se intentará reservar de inmediato con pago wallet.",
        ]
    )


def build_no_reservations_message(reason: str, next_notify_seconds: int) -> str:
    return "\n".join(
        [
            "Tacerca 29-04-2026 06:54 - sin reserva disponible",
            f"Ruta: {ROUTE_LABEL}",
            f"Fecha objetivo: {TARGET_DATE}",
            f"Horario objetivo: {TARGET_HOUR}",
            f"Detalle: {reason}",
            f"Siguiente aviso en: {next_notify_seconds} segundo(s).",
        ]
    )


def maybe_notify_no_reservations(
    state: dict[str, Any],
    *,
    reason: str,
    notify_every_seconds: int,
) -> None:
    now = time.time()
    last = float(state.get("last_no_reservation_notify_at") or 0)
    should_notify = (
        last <= 0
        or notify_every_seconds <= 0
        or (now - last) >= notify_every_seconds
    )
    if not should_notify:
        return

    message = build_no_reservations_message(reason, notify_every_seconds)
    print(f"[{now_ts()}] Enviando Telegram: no hay reservas disponibles.")
    send_telegram(message)

    state["last_no_reservation_notify_at"] = now
    state["last_no_reservation_notify_at_text"] = now_ts()
    state["last_no_reservation_reason"] = reason
    save_state(STATE_FILE, state)


def run_monitor_once(
    client: TacercaClient,
    rule: ReservationRule,
    notify_every_seconds: int,
) -> bool:
    state = load_state(STATE_FILE)
    if state.get("booking_done"):
        maybe_notify_booking_done_from_state(TARGET_DATE, state, STATE_FILE)
        print(f"[{now_ts()}] La reserva ya figura como completada en el estado local.")
        return True

    trips = client.fetch_trips(TARGET_DATE)
    target_trip = choose_target_trip(trips, rule, require_available=False)
    available_trip = choose_target_trip(trips, rule, require_available=True)
    if not target_trip:
        maybe_notify_no_reservations(
            state,
            reason=f"No existe viaje a las {TARGET_HOUR} para {TARGET_DATE}.",
            notify_every_seconds=notify_every_seconds,
        )
        print(f"[{now_ts()}] No existe viaje para {TARGET_DATE} {TARGET_HOUR}.")
        return False

    if not available_trip:
        # Aunque no haya cupos, verificamos si Tacerca ya reporta una reserva existente.
        try:
            attempt_booking(client, TARGET_DATE, target_trip, state, STATE_FILE)
        except Exception as exc:
            wallet_notified = notify_wallet_no_balance_if_needed(exc)
            if not wallet_notified:
                message = build_booking_failed_message(TARGET_DATE, target_trip, exc)
                print(message, file=sys.stderr)
                send_telegram(message)
            state["last_booking_error"] = {
                "timestamp": now_ts(),
                "trip_id": normalize_trip_id(target_trip),
                "error": str(exc),
            }
            save_state(STATE_FILE, state)
            raise

        refreshed_state = load_state(STATE_FILE)
        if refreshed_state.get("booking_done"):
            return True

        maybe_notify_no_reservations(
            state,
            reason=f"Existe viaje a las {TARGET_HOUR}, pero no hay cupos disponibles.",
            notify_every_seconds=notify_every_seconds,
        )
        print(f"[{now_ts()}] Viaje encontrado, pero sin cupos.")
        return False

    available_signature = json.dumps(
        {
            "trip_id": normalize_trip_id(available_trip),
            "hour": trip_departure_hour(available_trip),
            "available": available_seats(available_trip),
        },
        sort_keys=True,
    )
    if state.get("last_available_signature") != available_signature:
        available_message = build_available_message(available_trip)
        print(available_message)
        send_telegram(available_message)
        state["last_available_signature"] = available_signature
        state["last_available_at"] = now_ts()
        state.pop("last_no_reservation_notify_at", None)
        state.pop("last_no_reservation_notify_at_text", None)
        state.pop("last_no_reservation_reason", None)
        save_state(STATE_FILE, state)

    try:
        attempt_booking(client, TARGET_DATE, available_trip, state, STATE_FILE)
    except Exception as exc:
        wallet_notified = notify_wallet_no_balance_if_needed(exc)
        if not wallet_notified:
            message = build_booking_failed_message(TARGET_DATE, available_trip, exc)
            print(message, file=sys.stderr)
            send_telegram(message)
        state["last_booking_error"] = {
            "timestamp": now_ts(),
            "trip_id": normalize_trip_id(available_trip),
            "error": str(exc),
        }
        save_state(STATE_FILE, state)
        raise

    refreshed_state = load_state(STATE_FILE)
    return bool(refreshed_state.get("booking_done"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Monitor dedicado 29-04-2026 06:54 Los Montes -> Escuela Militar "
            "con auto-reserva y pago wallet."
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta una sola consulta y termina.",
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=env_int("TACERCA_2904_POLL_SECONDS", DEFAULT_POLL_SECONDS),
        help="Segundos entre consultas en modo continuo (default: 60).",
    )
    parser.add_argument(
        "--no-reservation-notify-every",
        type=int,
        default=env_int(
            "TACERCA_2904_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS",
            DEFAULT_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS,
        ),
        help="Frecuencia mínima de aviso 'no encontrado' en segundos (default: 1800).",
    )
    parser.add_argument(
        "--keep-running-after-booking",
        action="store_true",
        help="No terminar automáticamente después de reservar.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.poll < 1:
        raise RuntimeError("--poll debe ser mayor o igual a 1.")
    if args.no_reservation_notify_every < 0:
        raise RuntimeError("--no-reservation-notify-every debe ser mayor o igual a 0.")

    client = TacercaClient()
    client.origin_id = DEFAULT_ORIGIN_ID
    client.destination_id = DEFAULT_DESTINATION_ID
    client.type_payment = "wallet"
    client.massive = False
    client.login()

    rule = ReservationRule(
        exact_hours=[TARGET_HOUR],
        hour_from=None,
        hour_to=None,
        max_price=None,
        auto_book=True,
        poll_seconds=args.poll,
    )

    print(f"Monitoreando fecha fija: {TARGET_DATE}")
    print(f"Ruta fija: {ROUTE_LABEL}")
    print(f"Horario fijo: {TARGET_HOUR}")
    print("Reserva automática: ON")
    print("Pago forzado: wallet")
    print(f"Archivo estado: {STATE_FILE}")
    print(
        f"Aviso 'no encontrado': cada {args.no_reservation_notify_every} segundo(s) como mínimo."
    )
    print("Presiona Ctrl+C para detener.")

    try:
        while True:
            try:
                booked = run_monitor_once(
                    client,
                    rule,
                    args.no_reservation_notify_every,
                )
                if booked and not args.keep_running_after_booking:
                    print(f"[{now_ts()}] Reserva detectada/completada. Monitor finalizado.")
                    return 0
            except requests.HTTPError as exc:
                print(f"HTTP error: {exc}", file=sys.stderr)
                if exc.response is not None:
                    print(exc.response.text[:2000], file=sys.stderr)
            except Exception as exc:
                print(f"Error: {exc}", file=sys.stderr)

            if args.once:
                return 0
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\nMonitoreo detenido por el usuario.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
