#!/usr/bin/env python3
"""Web UI and persistent worker for configurable Tacerca reservations."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from monitor_tacerca import (
    DEFAULT_DESTINATION_ID,
    DEFAULT_ORIGIN_ID,
    TacercaClient,
    available_seats,
    booking_data,
    build_favorite_passenger_payload,
    extract_booking_code_from_response,
    extract_reserved_seat_numbers,
    extract_total_seat_count,
    normalize_trip_id,
    send_telegram,
    trip_departure_hour,
    validate_booking_matches_trip,
)

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("TACERCA_WEB_DB", ROOT / "data" / "monitor.db"))
ZONE = ZoneInfo("America/Santiago")
POLL_SECONDS = max(30, int(os.getenv("TACERCA_WEB_POLL_SECONDS", "60")))
MAX_PRICE_CLP = max(1, int(os.getenv("TACERCA_WEB_MAX_PRICE_CLP", "50000")))
LOG = logging.getLogger("tacerca.web")

ROUTES = {
    "outbound": ("Ida: Los Montes → Escuela Militar", DEFAULT_ORIGIN_ID, DEFAULT_DESTINATION_ID),
    "return": ("Vuelta: Escuela Militar → Los Montes", DEFAULT_DESTINATION_ID, DEFAULT_ORIGIN_ID),
}

app = Flask(__name__)
app.secret_key = os.getenv("TACERCA_WEB_SECRET_KEY") or secrets.token_hex(32)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS monitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                dates_json TEXT NOT NULL,
                schedule_type TEXT NOT NULL,
                exact_hours_json TEXT NOT NULL DEFAULT '[]',
                hour_from TEXT,
                hour_to TEXT,
                quantity INTEGER NOT NULL,
                outbound INTEGER NOT NULL DEFAULT 0,
                return_trip INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_check_at TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
                trip_date TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                trip_id TEXT,
                departure_hour TEXT,
                booking_code TEXT,
                detail TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(monitor_id, trip_date, direction)
            );
            """
        )


def now_text() -> str:
    return datetime.now(ZONE).isoformat(timespec="seconds")


def require_auth(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        expected_user = os.getenv("TACERCA_WEB_USERNAME", "admin")
        expected_password = os.getenv("TACERCA_WEB_PASSWORD", "")
        if not expected_password:
            abort(503, "Configura TACERCA_WEB_PASSWORD para habilitar la interfaz.")
        auth = request.authorization
        valid = bool(
            auth
            and hmac.compare_digest(auth.username or "", expected_user)
            and hmac.compare_digest(auth.password or "", expected_password)
        )
        if not valid:
            return ("Autenticación requerida", 401, {"WWW-Authenticate": 'Basic realm="Tacerca"'})
        return view(*args, **kwargs)

    return wrapped


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return str(session["csrf"])


app.jinja_env.globals["csrf_token"] = csrf_token


def check_csrf() -> None:
    if not hmac.compare_digest(str(session.get("csrf", "")), request.form.get("csrf", "")):
        abort(400, "Solicitud inválida.")


def parse_dates(raw: str) -> list[str]:
    values = [part.strip() for part in re.split(r"[,;\n]+", raw) if part.strip()]
    if not values:
        raise ValueError("Agrega al menos un día.")
    result: list[str] = []
    for value in values:
        parsed = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                parsed = datetime.strptime(value, fmt).date()
                break
            except ValueError:
                pass
        if parsed is None:
            raise ValueError(f"Fecha inválida: {value}.")
        normalized = parsed.strftime("%d-%m-%Y")
        if normalized not in result:
            result.append(normalized)
    return sorted(result, key=lambda value: datetime.strptime(value, "%d-%m-%Y"))


def parse_hhmm(value: str) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value or ""):
        raise ValueError(f"Hora inválida: {value or '(vacía)'}.")
    return value


def form_config() -> dict[str, Any]:
    schedule_type = request.form.get("schedule_type", "exact")
    if schedule_type not in {"exact", "range"}:
        raise ValueError("Tipo de horario inválido.")
    exact_hours: list[str] = []
    hour_from = hour_to = None
    if schedule_type == "exact":
        exact_hours = [parse_hhmm(v.strip()) for v in request.form.get("exact_hours", "").split(",") if v.strip()]
        exact_hours = list(dict.fromkeys(exact_hours))
        if not exact_hours:
            raise ValueError("Agrega al menos una hora exacta.")
    else:
        hour_from = parse_hhmm(request.form.get("hour_from", ""))
        hour_to = parse_hhmm(request.form.get("hour_to", ""))
    quantity = int(request.form.get("quantity", "1"))
    if quantity < 1 or quantity > 8:
        raise ValueError("La cantidad debe estar entre 1 y 8.")
    outbound = request.form.get("outbound") == "on"
    return_trip = request.form.get("return_trip") == "on"
    if not (outbound or return_trip):
        raise ValueError("Selecciona ida, vuelta o ambas.")
    return {
        "name": request.form.get("name", "").strip() or "Monitor Tacerca",
        "dates": parse_dates(request.form.get("dates", "")),
        "schedule_type": schedule_type,
        "exact_hours": exact_hours,
        "hour_from": hour_from,
        "hour_to": hour_to,
        "quantity": quantity,
        "outbound": outbound,
        "return_trip": return_trip,
    }


def ensure_reservations(db: sqlite3.Connection, monitor_id: int, config: dict[str, Any]) -> None:
    stamp = now_text()
    directions = [key for key in ROUTES if config[key if key == "outbound" else "return_trip"]]
    for trip_date in config["dates"]:
        for direction in directions:
            db.execute(
                "INSERT OR IGNORE INTO reservations "
                "(monitor_id, trip_date, direction, status, updated_at) VALUES (?, ?, ?, 'pending', ?)",
                (monitor_id, trip_date, direction, stamp),
            )


@app.get("/health")
def health() -> tuple[dict[str, str], int]:
    return {"status": "ok"}, 200


@app.get("/")
@require_auth
def index() -> str:
    with connect() as db:
        monitors = db.execute("SELECT * FROM monitors ORDER BY id DESC").fetchall()
        reservations = db.execute("SELECT * FROM reservations ORDER BY trip_date, direction").fetchall()
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in reservations:
        grouped.setdefault(row["monitor_id"], []).append(row)
    return render_template("index.html", monitors=monitors, reservations=grouped, routes=ROUTES)


@app.post("/monitors")
@require_auth
def create_monitor() -> Any:
    check_csrf()
    try:
        config = form_config()
        stamp = now_text()
        with connect() as db:
            cursor = db.execute(
                """INSERT INTO monitors
                (name, dates_json, schedule_type, exact_hours_json, hour_from, hour_to,
                 quantity, outbound, return_trip, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    config["name"], json.dumps(config["dates"]), config["schedule_type"],
                    json.dumps(config["exact_hours"]), config["hour_from"], config["hour_to"],
                    config["quantity"], int(config["outbound"]), int(config["return_trip"]),
                    stamp, stamp,
                ),
            )
            ensure_reservations(db, int(cursor.lastrowid), config)
        flash("Monitor creado y activo.", "success")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/monitors/<int:monitor_id>/toggle")
@require_auth
def toggle_monitor(monitor_id: int) -> Any:
    check_csrf()
    with connect() as db:
        db.execute(
            "UPDATE monitors SET active = CASE active WHEN 1 THEN 0 ELSE 1 END, updated_at = ? WHERE id = ?",
            (now_text(), monitor_id),
        )
    return redirect(url_for("index"))


def monitor_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["dates"] = json.loads(data.pop("dates_json"))
    data["exact_hours"] = json.loads(data.pop("exact_hours_json"))
    return data


def hour_matches(config: dict[str, Any], hour: str) -> bool:
    if config["schedule_type"] == "exact":
        return hour in config["exact_hours"]
    value = int(hour[:2]) * 60 + int(hour[3:])
    start = int(config["hour_from"][:2]) * 60 + int(config["hour_from"][3:])
    end = int(config["hour_to"][:2]) * 60 + int(config["hour_to"][3:])
    return start <= value <= end if start <= end else value >= start or value <= end


def free_seats(trip: dict[str, Any], quantity: int) -> list[int]:
    reserved = extract_reserved_seat_numbers(trip)
    return [seat for seat in range(1, extract_total_seat_count(trip) + 1) if seat not in reserved][:quantity]


def confirmed_seat_count(verification: dict[str, Any]) -> int:
    data = booking_data(verification)
    owner = 1 if data.get("seating") or data.get("guest") else 0
    passengers = data.get("passengers")
    return owner + (len(passengers) if isinstance(passengers, list) else 0)


def build_wallet_payload(client: TacercaClient, trip: dict[str, Any], quantity: int) -> tuple[dict[str, Any], int]:
    seats = free_seats(trip, quantity)
    if len(seats) != quantity:
        raise RuntimeError(f"No hay {quantity} asientos libres.")
    payload = client.build_booking_payload(trip)
    payload["typePayment"] = "wallet"
    payload["seating"] = str(seats[0])
    payload["guest"]["seating"] = str(seats[0])
    if quantity > 1:
        favorites = client.get_profile().get("passengersFavorite", [])
        if not isinstance(favorites, list) or len(favorites) < quantity - 1:
            raise RuntimeError(f"Faltan pasajeros frecuentes en Tacerca ({len(favorites) if isinstance(favorites, list) else 0}/{quantity - 1}).")
        payload["passengers"] = [
            build_favorite_passenger_payload(passenger, seat)
            for passenger, seat in zip(favorites[: quantity - 1], seats[1:])
        ]
    fare = int(trip.get("price") or trip.get("totalPrice") or payload.get("totalPrice") or 0) * quantity
    if fare <= 0 or fare > MAX_PRICE_CLP:
        raise RuntimeError(f"Precio no autorizado: ${fare}; máximo ${MAX_PRICE_CLP}.")
    payload["totalPrice"] = fare
    return payload, fare


def mark_reservation(reservation_id: int, status: str, **fields: Any) -> None:
    allowed = {"trip_id", "departure_hour", "booking_code", "detail"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates.update(status=status, updated_at=now_text())
    sql = "UPDATE reservations SET " + ", ".join(f"{key} = ?" for key in updates) + " WHERE id = ?"
    with connect() as db:
        db.execute(sql, (*updates.values(), reservation_id))


def notify(message: str) -> None:
    try:
        send_telegram(message)
    except Exception:
        LOG.exception("No se pudo enviar la notificación de Telegram")


def process_reservation(client: TacercaClient, config: dict[str, Any], reservation: sqlite3.Row) -> None:
    direction = reservation["direction"]
    _, origin, destination = ROUTES[direction]
    trips = client.fetch_trips(reservation["trip_date"], origin, destination)
    candidates = [
        trip for trip in trips
        if hour_matches(config, trip_departure_hour(trip))
        and available_seats(trip) >= config["quantity"]
    ]
    candidates.sort(key=lambda trip: trip_departure_hour(trip))
    if not candidates:
        mark_reservation(reservation["id"], "waiting", detail="Sin cupos que cumplan la regla")
        return
    trip = candidates[0]
    trip_id = normalize_trip_id(trip)
    hour = trip_departure_hour(trip)
    existing = extract_booking_code_from_response(client.check_trip_exists(trip_id))
    if existing:
        verification = client.get_trip(existing)
        validate_booking_matches_trip(verification, trip)
        confirmed = confirmed_seat_count(verification)
        if confirmed and confirmed < config["quantity"]:
            mark_reservation(
                reservation["id"], "review", trip_id=trip_id,
                departure_hour=hour, booking_code=existing,
                detail=f"La reserva existente tiene {confirmed}/{config['quantity']} asientos; revisar manualmente.",
            )
            return
        mark_reservation(reservation["id"], "booked", trip_id=trip_id, departure_hour=hour, booking_code=existing, detail="Reserva existente confirmada")
        notify(f"Tacerca: reserva existente confirmada para {reservation['trip_date']} a las {hour}. Código {existing}.")
        return
    payload, fare = build_wallet_payload(client, trip, config["quantity"])
    client._profile_cache = None
    balance = int(client.get_profile().get("balance") or 0)
    if balance < fare:
        raise RuntimeError(f"Saldo insuficiente: ${balance}; se necesitan ${fare}.")
    mark_reservation(reservation["id"], "submitting", trip_id=trip_id, departure_hour=hour, detail="Enviando pago wallet; no reintentar si queda en revisión")
    try:
        response = client.create_trip_auth(payload)
    except Exception as exc:
        mark_reservation(reservation["id"], "review", detail=f"Resultado de pago incierto: {type(exc).__name__}. Revisar Tacerca.")
        notify(f"Tacerca: revisar manualmente el pago de {reservation['trip_date']} a las {hour}; no se reintentará automáticamente.")
        return
    code = extract_booking_code_from_response(response)
    if not code:
        mark_reservation(reservation["id"], "review", detail="Tacerca no devolvió código. Revisar antes de reintentar.")
        notify(f"Tacerca: pago sin código para {reservation['trip_date']} a las {hour}; revisar manualmente.")
        return
    try:
        verification = client.get_trip(code)
        validate_booking_matches_trip(verification, trip)
    except Exception as exc:
        mark_reservation(reservation["id"], "review", booking_code=code, detail=f"Reserva {code} sin verificación completa: {exc}")
        notify(f"Tacerca: reserva {code} requiere revisión manual antes de cualquier reintento.")
        return
    data = booking_data(verification)
    if data.get("mediaPayment") not in (None, "wallet"):
        mark_reservation(reservation["id"], "review", booking_code=code, detail="La confirmación no indica pago wallet.")
        return
    confirmed = confirmed_seat_count(verification)
    if confirmed and confirmed < config["quantity"]:
        mark_reservation(reservation["id"], "review", booking_code=code, detail=f"Tacerca confirmó {confirmed}/{config['quantity']} asientos.")
        notify(f"Tacerca: la reserva {code} confirmó menos asientos de los solicitados; revisar manualmente.")
        return
    mark_reservation(reservation["id"], "booked", trip_id=trip_id, departure_hour=hour, booking_code=code, detail=f"Reserva confirmada por ${fare} con wallet")
    notify(f"Tacerca: reserva confirmada para {reservation['trip_date']} a las {hour}, {config['quantity']} asiento(s), pago wallet. Código {code}.")


def worker_tick(client_factory: Callable[[], TacercaClient] = TacercaClient) -> None:
    today = datetime.now(ZONE).date()
    with connect() as db:
        monitor_rows = db.execute("SELECT * FROM monitors WHERE active = 1 ORDER BY id").fetchall()
    for row in monitor_rows:
        config = monitor_dict(row)
        try:
            client = client_factory()
            client.type_payment = "wallet"
            client.massive = False
            client.login()
            with connect() as db:
                pending = db.execute(
                    "SELECT * FROM reservations WHERE monitor_id = ? AND status IN ('pending', 'waiting') ORDER BY trip_date, direction",
                    (config["id"],),
                ).fetchall()
            for reservation in pending:
                trip_day = datetime.strptime(reservation["trip_date"], "%d-%m-%Y").date()
                if trip_day < today:
                    mark_reservation(reservation["id"], "expired", detail="La fecha ya pasó")
                    continue
                try:
                    process_reservation(client, config, reservation)
                except Exception as exc:
                    LOG.exception("Monitor %s, reserva %s", config["id"], reservation["id"])
                    if reservation["status"] != "submitting":
                        mark_reservation(reservation["id"], "waiting", detail=str(exc)[:500])
            with connect() as db:
                db.execute("UPDATE monitors SET last_check_at = ?, last_error = NULL WHERE id = ?", (now_text(), config["id"]))
        except Exception as exc:
            LOG.exception("Monitor %s", config["id"])
            with connect() as db:
                db.execute("UPDATE monitors SET last_check_at = ?, last_error = ? WHERE id = ?", (now_text(), str(exc)[:500], config["id"]))


def worker_loop() -> None:
    while True:
        try:
            worker_tick()
        except Exception:
            LOG.exception("Fallo general del worker")
        time.sleep(POLL_SECONDS)


def start_worker() -> None:
    if os.getenv("TACERCA_WEB_DISABLE_WORKER", "").lower() in {"1", "true", "yes"}:
        return
    threading.Thread(target=worker_loop, name="tacerca-worker", daemon=True).start()


init_db()
start_worker()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), threaded=True)
