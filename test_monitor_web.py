import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["TACERCA_WEB_DISABLE_WORKER"] = "true"
os.environ["TACERCA_WEB_PASSWORD"] = "test-password"
os.environ["TACERCA_WEB_SECRET_KEY"] = "test-secret"

import monitor_web


class MonitorWebTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        monitor_web.DB_PATH = Path(self.tempdir.name) / "monitor.db"
        monitor_web.init_db()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_parse_dates_accepts_both_formats_and_removes_duplicates(self):
        self.assertEqual(
            monitor_web.parse_dates("2026-11-03, 04-11-2026, 2026-11-03"),
            ["03-11-2026", "04-11-2026"],
        )

    def test_range_can_cross_midnight(self):
        config = {"schedule_type": "range", "hour_from": "22:00", "hour_to": "02:00"}
        self.assertTrue(monitor_web.hour_matches(config, "23:30"))
        self.assertTrue(monitor_web.hour_matches(config, "01:30"))
        self.assertFalse(monitor_web.hour_matches(config, "12:00"))

    def test_confirmed_seat_count_includes_owner_and_passengers(self):
        verification = {"data": {"seating": "7", "passengers": [{"seating": "8"}]}}
        self.assertEqual(monitor_web.confirmed_seat_count(verification), 2)

    def test_wallet_payload_uses_favorite_passengers(self):
        trip = {
            "_id": "planning",
            "activeTrip": "active",
            "hourInit": "06:54",
            "hourFinish": "07:30",
            "price": 3700,
            "vehicle": {"seatings": 3},
            "seatings": [{"seating": 2}],
        }
        client = Mock()
        client.build_booking_payload.return_value = {
            "typePayment": "wallet", "seating": "3", "guest": {"seating": "3"},
            "passengers": [], "totalPrice": 3700,
        }
        client.get_profile.return_value = {
            "passengersFavorite": [{"firstName": "Ana", "lastName": "Pérez", "rut": "1-9"}]
        }
        payload, fare = monitor_web.build_wallet_payload(client, trip, 2)
        self.assertEqual(payload["seating"], "1")
        self.assertEqual(payload["passengers"][0]["seating"], "3")
        self.assertEqual(fare, 7400)

    def test_create_monitor_requires_auth(self):
        client = monitor_web.app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 401)

    @patch("monitor_web.send_telegram")
    def test_uncertain_payment_is_never_retried(self, _telegram):
        config = {
            "quantity": 1, "schedule_type": "exact", "exact_hours": ["06:54"],
            "hour_from": None, "hour_to": None,
        }
        with monitor_web.connect() as db:
            monitor_id = db.execute(
                "INSERT INTO monitors (name, dates_json, schedule_type, exact_hours_json, quantity, outbound, return_trip, active, created_at, updated_at) VALUES ('x','[]','exact','[]',1,1,0,1,'x','x')"
            ).lastrowid
            reservation_id = db.execute(
                "INSERT INTO reservations (monitor_id, trip_date, direction, status, updated_at) VALUES (?, '03-11-2026', 'outbound', 'pending', 'x')",
                (monitor_id,),
            ).lastrowid
            reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        trip = {"_id": "p", "activeTrip": "a", "hourInit": "06:54", "price": 3700, "vehicle": {"seatings": 1}, "seatings": []}
        client = Mock()
        client.fetch_trips.return_value = [trip]
        client.check_trip_exists.return_value = {}
        client.build_booking_payload.return_value = {"typePayment": "wallet", "guest": {}, "totalPrice": 3700}
        client.get_profile.return_value = {"balance": 10000}
        client.create_trip_auth.side_effect = TimeoutError("timeout")
        monitor_web.process_reservation(client, config, reservation)
        with monitor_web.connect() as db:
            status = db.execute("SELECT status FROM reservations WHERE id = ?", (reservation_id,)).fetchone()[0]
        self.assertEqual(status, "review")


if __name__ == "__main__":
    unittest.main()
