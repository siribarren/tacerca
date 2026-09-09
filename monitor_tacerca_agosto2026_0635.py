#!/usr/bin/env python3
"""
Monitor dedicado Tacerca:
- Fechas fijas: 03-08-2026 y 17-08-2026
- Ruta fija: Piedra Roja / Los Montes -> Escuela Militar (solo ida)
- Hora fija: 06:35
- Pasajeros: titular (asiento 7) + pasajero frecuente Teodoro Iribarren (asiento 8)

Comportamiento:
- Revisa disponibilidad cada 10 minutos (default).
- Si hay cupo para ambos asientos, reserva de inmediato vía trips/create-trip-massive/.
- Fuerza pago con billetera (wallet).
- Si no hay cupos, avisa por Telegram al menos cada 1 hora (default).
- Si encuentra y reserva, avisa por Telegram.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

from monitor_tacerca import (
    DEFAULT_DESTINATION_ID,
    DEFAULT_ORIGIN_ID,
    MayBookingBlock,
    May2026PlanConfig,
    TacercaClient,
    env_int,
    now_ts,
    run_may2026_plan_check,
)

TARGET_DATES = ["03-08-2026", "17-08-2026"]
TARGET_HOUR = "06:35"
ROUTE_LABEL = "IDA Piedra Roja / Los Montes -> Escuela Militar"
FREQUENT_PASSENGER_NAME = "Teodoro Iribarren"
STATE_FILE = Path("tacerca_state_agosto2026_0635_wallet.json")

DEFAULT_POLL_SECONDS = 600
DEFAULT_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS = 3600


def build_config(client: TacercaClient) -> May2026PlanConfig:
    poll_seconds = (
        env_int("TACERCA_AGO_POLL_SECONDS", DEFAULT_POLL_SECONDS) or DEFAULT_POLL_SECONDS
    )
    notify_every_seconds = (
        env_int(
            "TACERCA_AGO_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS",
            DEFAULT_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS,
        )
        or DEFAULT_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS
    )
    # run_may2026_plan_check reads this shared env var to pace "sin reservas" avisos.
    os.environ["TACERCA_NO_RESERVATIONS_NOTIFY_EVERY_SECONDS"] = str(notify_every_seconds)

    block = MayBookingBlock(
        key="ida_0635_agosto2026",
        route_key="ida",
        route_label=ROUTE_LABEL,
        origin_id=client.origin_id,
        destination_id=client.destination_id,
        hour=TARGET_HOUR,
        dates=list(TARGET_DATES),
        seats=(7, 8),
        passenger_names=(FREQUENT_PASSENGER_NAME,),
    )

    return May2026PlanConfig(
        seat=7,
        poll_seconds=poll_seconds,
        block_retry_seconds=poll_seconds,
        type_payment="wallet",
        blocks=[block],
    )


def main() -> int:
    client = TacercaClient()
    client.origin_id = DEFAULT_ORIGIN_ID
    client.destination_id = DEFAULT_DESTINATION_ID
    client.type_payment = "wallet"
    client.massive = True
    client.login()

    config = build_config(client)

    print(f"Monitoreando fechas fijas: {', '.join(TARGET_DATES)}")
    print(f"Ruta fija: {ROUTE_LABEL}")
    print(f"Horario fijo: {TARGET_HOUR}")
    print(f"Pasajeros: titular (asiento 7) + {FREQUENT_PASSENGER_NAME} (asiento 8)")
    print("Pago forzado: wallet")
    print(f"Archivo estado: {STATE_FILE}")
    print(f"Frecuencia de consulta: cada {config.poll_seconds} segundo(s).")
    print("Presiona Ctrl+C para detener.")
    print()

    force_startup_no_reservations_notification = True
    try:
        while True:
            try:
                run_may2026_plan_check(
                    client,
                    config,
                    STATE_FILE,
                    force_no_reservations_notification=force_startup_no_reservations_notification,
                )
                force_startup_no_reservations_notification = False
            except requests.HTTPError as exc:
                print(f"HTTP error: {exc}", file=sys.stderr)
                if exc.response is not None:
                    print(exc.response.text[:2000], file=sys.stderr)
                print()
            except Exception as exc:
                print(f"[{now_ts()}] Error: {exc}", file=sys.stderr)
                print()

            time.sleep(config.poll_seconds)
    except KeyboardInterrupt:
        print("\nMonitoreo detenido por el usuario.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
