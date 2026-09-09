#!/usr/bin/env python3
"""
Monitor dedicado Tacerca:
- Fecha fija: 10-06-2026
- Ruta fija: Piedra Roja / Los Montes -> Escuela Militar
- Hora fija: 06:54

Comportamiento:
- Consulta disponibilidad cada 5 minutos.
- Si encuentra cupo, reserva de inmediato pagando con billetera.
- Si no encuentra cupo, notifica por Telegram al menos cada 1 hora.
- Si encuentra cupo, notifica por Telegram de inmediato.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from monitor_tacerca import (
    DEFAULT_DESTINATION_ID,
    DEFAULT_ORIGIN_ID,
    TacercaClient,
    attempt_booking,
    available_seats,
    build_booking_failed_message,
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

TARGET_DATE = "10-06-2026"
TARGET_HOUR = "06:54"
ROUTE_LABEL = "Piedra Roja / Los Montes -> Escuela Militar"
STATE_FILE = Path("tacerca_state_10062026_0654_wallet.json")

DEFAULT_POLL_SECONDS = 300
DEFAULT_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS = 3600
TELEGRAM_RETRY_COUNT = 3
TELEGRAM_RETRY_DELAY_SECONDS = 5
LOCAL_LOG_FILE = Path("monitor_10062026.log")


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


def append_local_log(message: str) -> None:
    line = f"[{now_ts()}] {message}\n"
    try:
        with LOCAL_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        pass


def require_telegram_config() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno del proceso."
        )


def build_available_message(trip: dict[str, Any]) -> str:
    price = int(first_non_empty(trip.get("price"), trip.get("totalPrice"), 0) or 0)
    return "\n".join(
        [
            "Tacerca 10-06-2026 06:54 - cupo detectado",
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
            "Tacerca 10-06-2026 06:54 - sin reserva disponible",
            f"Ruta: {ROUTE_LABEL}",
            f"Fecha objetivo: {TARGET_DATE}",
            f"Horario objetivo: {TARGET_HOUR}",
            f"Detalle: {reason}",
            f"Siguiente aviso en: {next_notify_seconds} segundo(s).",
        ]
    )


def safe_send_telegram(message: str) -> bool:
    last_exc: Exception | None = None
    for attempt in range(1, TELEGRAM_RETRY_COUNT + 1):
        try:
            send_telegram(message)
            return True
        except Exception as exc:
            last_exc = exc
            print(
                f"[{now_ts()}] Telegram falló en intento {attempt}/{TELEGRAM_RETRY_COUNT}: {exc}",
                file=sys.stderr,
            )
            if attempt < TELEGRAM_RETRY_COUNT:
                time.sleep(TELEGRAM_RETRY_DELAY_SECONDS)

    if last_exc is not None:
        print(f"[{now_ts()}] Telegram no pudo enviar el mensaje: {last_exc}", file=sys.stderr)
        append_local_log(f"Telegram no pudo enviar el mensaje: {last_exc}")
    return False


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
    append_local_log(f"Enviando Telegram: no hay reservas disponibles. Motivo: {reason}")
    safe_send_telegram(message)

    state["last_no_reservation_notify_at"] = now
    state["last_no_reservation_notify_at_text"] = now_ts()
    state["last_no_reservation_reason"] = reason
    save_state(STATE_FILE, state)


def run_monitor_once(
    client: TacercaClient,
    notify_every_seconds: int,
) -> bool:
    state = load_state(STATE_FILE)
    if state.get("booking_done"):
        maybe_notify_booking_done_from_state(TARGET_DATE, state, STATE_FILE)
        print(f"[{now_ts()}] La reserva ya figura como completada en el estado local.")
        return True

    trips = client.fetch_trips(TARGET_DATE, DEFAULT_ORIGIN_ID, DEFAULT_DESTINATION_ID)
    target_trip = None
    available_trip = None
    for trip in trips:
        if trip_departure_hour(trip) == TARGET_HOUR:
            target_trip = target_trip or trip
            if available_seats(trip) > 0:
                available_trip = trip
                break

    if not target_trip:
        maybe_notify_no_reservations(
            state,
            reason=f"No existe viaje a las {TARGET_HOUR} para {TARGET_DATE}.",
            notify_every_seconds=notify_every_seconds,
        )
        print(f"[{now_ts()}] No existe viaje para {TARGET_DATE} {TARGET_HOUR}.")
        return False

    if not available_trip:
        try:
            attempt_booking(client, TARGET_DATE, target_trip, state, STATE_FILE)
        except Exception as exc:
            wallet_notified = notify_wallet_no_balance_if_needed(exc)
            if not wallet_notified:
                message = build_booking_failed_message(TARGET_DATE, target_trip, exc)
                print(message, file=sys.stderr)
                safe_send_telegram(message)
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
        safe_send_telegram(available_message)
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
            safe_send_telegram(message)
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
            "Monitor dedicado 10-06-2026 06:54 Piedra Roja / Los Montes -> Escuela Militar "
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
        default=env_int("TACERCA_1006_POLL_SECONDS", DEFAULT_POLL_SECONDS),
        help="Segundos entre consultas en modo continuo (default: 300).",
    )
    parser.add_argument(
        "--no-reservation-notify-every",
        type=int,
        default=env_int(
            "TACERCA_1006_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS",
            DEFAULT_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS,
        ),
        help="Frecuencia mínima de aviso 'no encontrado' en segundos (default: 3600).",
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
    require_telegram_config()
    client.login()

    print("Tacerca monitor 10-06-2026 06:54")
    print(f"Ruta: {ROUTE_LABEL}")
    print(f"Fecha objetivo: {TARGET_DATE}")
    print(f"Hora objetivo: {TARGET_HOUR}")
    print("Pago forzado: wallet")
    print(f"Consulta cada {args.poll} segundo(s)")
    print(
        f"Aviso 'no encontrado': cada {args.no_reservation_notify_every} segundo(s) como mínimo."
    )

    startup_message = "\n".join(
        [
            "Tacerca monitor iniciado",
            f"- Ruta: {ROUTE_LABEL}",
            f"- Fecha: {TARGET_DATE}",
            f"- Hora: {TARGET_HOUR}",
            "- Pago: wallet",
            f"- Consulta cada: {args.poll} segundo(s)",
            (
                "- Aviso sin cupo: cada "
                f"{args.no_reservation_notify_every} segundo(s) como mínimo"
            ),
        ]
    )
    append_local_log("Monitor iniciado")
    safe_send_telegram(startup_message)

    if args.once:
        run_monitor_once(client, args.no_reservation_notify_every)
        return 0

    while True:
        try:
            booked = run_monitor_once(client, args.no_reservation_notify_every)
            if booked and not args.keep_running_after_booking:
                return 0
        except KeyboardInterrupt:
            print(f"[{now_ts()}] Interrumpido por el usuario.")
            return 130
        except Exception as exc:
            print(f"[{now_ts()}] Error en monitor: {exc}", file=sys.stderr)
        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
