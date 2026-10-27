"""Phase 2 tests: workers.availability JSON schema, parser,
check_shift_within_working_hours helper, and the /add_worker +
/edit_worker form paths.
"""
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse

TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-worker-availability-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "worker-availability-test-only")
os.environ["AUTO_PLANNING_MODE"] = "write_enabled"

import app  # noqa: E402

app.app.config["AUTO_PLANNING_MODE"] = "write_enabled"
try:
    app.init_db()
except Exception:
    pass


def tearDownModule():
    shutil.rmtree(TEST_STORAGE, ignore_errors=True)
    try:
        os.rmdir(TEST_TEMP_ROOT)
    except OSError:
        pass


class ParseEmptyAndValidTests(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(app.parse_worker_availability(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(app.parse_worker_availability(""))

    def test_whitespace_returns_none(self):
        self.assertIsNone(app.parse_worker_availability("  \n\t"))

    def test_working_day_single_interval(self):
        raw = json.dumps({"mon": [{"start": "08:00", "end": "16:00"}]})
        parsed = app.parse_worker_availability(raw)
        self.assertEqual(parsed, {"mon": [{"start": "08:00", "end": "16:00"}]})

    def test_working_day_multiple_non_overlapping_intervals(self):
        raw = json.dumps({"tue": [
            {"start": "08:00", "end": "12:00"},
            {"start": "13:00", "end": "17:00"},
        ]})
        parsed = app.parse_worker_availability(raw)
        self.assertEqual(len(parsed["tue"]), 2)

    def test_explicit_day_off_preserved(self):
        raw = json.dumps({"wed": []})
        parsed = app.parse_worker_availability(raw)
        self.assertEqual(parsed, {"wed": []})

    def test_undefined_day_not_in_dict(self):
        # Only Monday configured; Tuesday–Sunday absent → undefined.
        raw = json.dumps({"mon": [{"start": "08:00", "end": "17:00"}]})
        parsed = app.parse_worker_availability(raw)
        self.assertIn("mon", parsed)
        self.assertNotIn("tue", parsed)

    def test_adjacent_intervals_allowed(self):
        raw = json.dumps({"fri": [
            {"start": "08:00", "end": "12:00"},
            {"start": "12:00", "end": "16:00"},
        ]})
        parsed = app.parse_worker_availability(raw)
        self.assertEqual(len(parsed["fri"]), 2)

    def test_round_trip_stable(self):
        raw = json.dumps({
            "mon": [{"start": "08:00", "end": "12:00"}],
            "wed": [],
            "fri": [
                {"start": "09:00", "end": "11:00"},
                {"start": "14:00", "end": "16:00"},
            ],
        })
        parsed = app.parse_worker_availability(raw)
        serialized = app.serialize_worker_availability(parsed)
        reparsed = app.parse_worker_availability(serialized)
        self.assertEqual(parsed, reparsed)

    def test_serialize_none_returns_empty_string(self):
        self.assertEqual(app.serialize_worker_availability(None), "")


class ParseInvalidTests(unittest.TestCase):
    def _bad(self, raw, code):
        with self.assertRaises(app.WorkerAvailabilityInvalid) as ctx:
            app.parse_worker_availability(raw)
        self.assertEqual(ctx.exception.code, code)

    def test_invalid_json(self):
        self._bad("not{json", "invalid_json")

    def test_not_an_object(self):
        self._bad(json.dumps([{"mon": []}]), "not_an_object")

    def test_bad_day_key(self):
        self._bad(json.dumps({"monday": []}), "bad_day_key")

    def test_day_not_a_list(self):
        self._bad(json.dumps({"mon": "08:00-17:00"}), "day_not_a_list")

    def test_interval_not_object(self):
        self._bad(json.dumps({"mon": ["08:00-17:00"]}),
                  "interval_not_object")

    def test_unknown_key(self):
        self._bad(json.dumps({"mon": [{"start": "08:00", "end": "17:00",
                                        "note": "extra"}]}),
                  "unknown_key")

    def test_bad_start(self):
        self._bad(json.dumps({"mon": [{"start": "8am", "end": "17:00"}]}),
                  "bad_start")

    def test_bad_end(self):
        self._bad(json.dumps({"mon": [{"start": "08:00", "end": "25:00"}]}),
                  "bad_end")

    def test_end_before_start(self):
        self._bad(json.dumps({"mon": [{"start": "17:00", "end": "08:00"}]}),
                  "end_before_start")

    def test_overlapping_intervals(self):
        self._bad(json.dumps({"mon": [
            {"start": "08:00", "end": "12:00"},
            {"start": "11:00", "end": "15:00"},
        ]}), "overlapping_intervals")

    def test_too_many_intervals(self):
        intervals = [{"start": f"{h:02d}:00", "end": f"{h:02d}:30"}
                     for h in range(9)]  # 9 > max=8
        self._bad(json.dumps({"mon": intervals}), "too_many_intervals")


class CheckShiftHelperTests(unittest.TestCase):
    """``check_shift_within_working_hours``: undefined → None; day off
    → warning; outside the configured interval → warning; inside →
    None. 2026-10-26 is a Monday.
    """
    MONDAY = "2026-10-26"
    TUESDAY = "2026-10-27"

    def test_empty_availability_no_warning(self):
        self.assertIsNone(app.check_shift_within_working_hours(
            "", self.MONDAY, "08:00", "16:00",
        ))

    def test_none_availability_no_warning(self):
        self.assertIsNone(app.check_shift_within_working_hours(
            None, self.MONDAY, "08:00", "16:00",
        ))

    def test_day_missing_from_dict_no_warning(self):
        # Only Tuesday configured; Monday shift must NOT warn.
        av = json.dumps({"tue": [{"start": "08:00", "end": "16:00"}]})
        self.assertIsNone(app.check_shift_within_working_hours(
            av, self.MONDAY, "09:00", "11:00",
        ))

    def test_shift_inside_interval_no_warning(self):
        av = json.dumps({"mon": [{"start": "08:00", "end": "17:00"}]})
        self.assertIsNone(app.check_shift_within_working_hours(
            av, self.MONDAY, "09:00", "12:00",
        ))

    def test_shift_outside_interval_warns(self):
        av = json.dumps({"mon": [{"start": "08:00", "end": "12:00"}]})
        self.assertEqual(
            app.check_shift_within_working_hours(
                av, self.MONDAY, "13:00", "15:00",
            ),
            "outside_working_hours",
        )

    def test_day_off_warns(self):
        av = json.dumps({"mon": []})
        self.assertEqual(
            app.check_shift_within_working_hours(
                av, self.MONDAY, "08:00", "09:00",
            ),
            "outside_working_hours",
        )

    def test_shift_straddling_two_adjacent_intervals_warns(self):
        """09:00–13:00 spans end-of-A (12:00) and start-of-B (12:00);
        it is NOT fully inside either range → warn."""
        av = json.dumps({"mon": [
            {"start": "08:00", "end": "12:00"},
            {"start": "12:00", "end": "17:00"},
        ]})
        self.assertEqual(
            app.check_shift_within_working_hours(
                av, self.MONDAY, "09:00", "13:00",
            ),
            "outside_working_hours",
        )

    def test_shift_wholly_inside_second_interval_ok(self):
        av = json.dumps({"mon": [
            {"start": "08:00", "end": "12:00"},
            {"start": "13:00", "end": "17:00"},
        ]})
        self.assertIsNone(app.check_shift_within_working_hours(
            av, self.MONDAY, "14:00", "16:00",
        ))

    def test_malformed_availability_silently_no_warning(self):
        # Validation lives at save-time; the helper is used on render
        # and must never crash — any garbage returns None.
        self.assertIsNone(app.check_shift_within_working_hours(
            "{broken", self.MONDAY, "08:00", "09:00",
        ))


class SchemaMigrationTests(unittest.TestCase):
    def test_column_present(self):
        conn = app.get_conn()
        try:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(workers)"
            ).fetchall()]
        finally:
            conn.close()
        self.assertIn("availability", cols)

    def test_init_db_idempotent(self):
        app.init_db()
        app.init_db()


class HttpFormTests(unittest.TestCase):
    def setUp(self):
        conn = app.get_conn()
        for table in ("shifts", "workers", "worker_colors"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["lang"] = "en"

    def _row(self, name):
        conn = app.get_conn()
        try:
            r = conn.execute(
                "SELECT COALESCE(availability,'') FROM workers "
                "WHERE name=?", (name,),
            ).fetchone()
            return r[0] if r else None
        finally:
            conn.close()

    def test_add_worker_without_availability_stores_empty(self):
        resp = self.client.post("/add_worker", data={
            "worker_name": "Quick Add",
            "address": "Rue 1",
        })
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(self._row("Quick Add"), "")

    def test_add_worker_with_valid_availability(self):
        payload = json.dumps({
            "mon": [{"start": "08:00", "end": "17:00"}],
            "wed": [],  # explicit day off
        })
        resp = self.client.post("/add_worker", data={
            "worker_name": "Mila",
            "address": "Rue 2",
            "availability_json": payload,
        })
        self.assertIn(resp.status_code, (302, 303))
        stored = self._row("Mila")
        parsed = app.parse_worker_availability(stored)
        self.assertEqual(parsed["mon"], [{"start": "08:00",
                                          "end": "17:00"}])
        self.assertEqual(parsed["wed"], [])

    def test_add_worker_with_invalid_availability_rejects(self):
        resp = self.client.post("/add_worker", data={
            "worker_name": "Bad",
            "address": "Rue 3",
            "availability_json": "not{json",
        })
        self.assertIn(resp.status_code, (302, 303))
        self.assertIsNone(self._row("Bad"))

    def test_edit_worker_preserves_availability_when_field_blank(self):
        """JS-less POST: the hidden input stays at its HTML default "";
        existing availability must NOT be clobbered."""
        existing = app.serialize_worker_availability(
            app.parse_worker_availability(json.dumps({
                "mon": [{"start": "08:00", "end": "16:00"}],
            })),
        )
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO workers (name, address, availability) "
            "VALUES (?,?,?)", ("Preserve", "Rue 4", existing),
        )
        conn.commit()
        conn.close()
        self.client.post(
            "/edit_worker/" + urllib.parse.quote("Preserve"),
            data={
                "name": "Preserve",
                "address": "Rue 4",
                "availability_json": "",  # JS didn't fire
            },
        )
        self.assertEqual(self._row("Preserve"), existing)

    def test_edit_worker_explicit_clear_with_empty_object(self):
        existing = app.serialize_worker_availability(
            app.parse_worker_availability(json.dumps({
                "mon": [{"start": "08:00", "end": "16:00"}],
            })),
        )
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO workers (name, address, availability) "
            "VALUES (?,?,?)", ("Clear Me", "Rue 5", existing),
        )
        conn.commit()
        conn.close()
        self.client.post(
            "/edit_worker/" + urllib.parse.quote("Clear Me"),
            data={
                "name": "Clear Me",
                "address": "Rue 5",
                "availability_json": "{}",
            },
        )
        self.assertEqual(self._row("Clear Me"), "")

    def test_edit_worker_updates_availability(self):
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO workers (name, address) VALUES (?,?)",
            ("Updater", "Rue 6"),
        )
        conn.commit()
        conn.close()
        payload = json.dumps({
            "tue": [{"start": "09:00", "end": "12:00"},
                    {"start": "13:00", "end": "17:00"}],
            "thu": [],
        })
        self.client.post(
            "/edit_worker/" + urllib.parse.quote("Updater"),
            data={
                "name": "Updater",
                "address": "Rue 6",
                "availability_json": payload,
            },
        )
        stored = self._row("Updater")
        parsed = app.parse_worker_availability(stored)
        self.assertEqual(len(parsed["tue"]), 2)
        self.assertEqual(parsed["thu"], [])
        self.assertNotIn("mon", parsed)  # undefined for Monday

    def test_edit_worker_rejects_overlap_without_touching_row(self):
        existing = app.serialize_worker_availability(
            app.parse_worker_availability(json.dumps({
                "mon": [{"start": "08:00", "end": "12:00"}],
            })),
        )
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO workers (name, address, availability) "
            "VALUES (?,?,?)", ("Overlap Guard", "Rue 7", existing),
        )
        conn.commit()
        conn.close()
        payload = json.dumps({"mon": [
            {"start": "08:00", "end": "12:00"},
            {"start": "11:00", "end": "13:00"},
        ]})
        self.client.post(
            "/edit_worker/" + urllib.parse.quote("Overlap Guard"),
            data={
                "name": "Overlap Guard",
                "address": "Rue 7",
                "availability_json": payload,
            },
        )
        self.assertEqual(self._row("Overlap Guard"), existing)

    def test_non_admin_cannot_add(self):
        worker_client = app.app.test_client()
        with worker_client.session_transaction() as sess:
            sess["user"] = "someone"
            sess["role"] = "worker"
            sess["lang"] = "en"
        resp = worker_client.post("/add_worker", data={
            "worker_name": "Role Attempt",
            "address": "Rue X",
            "availability_json": json.dumps({"mon": []}),
        })
        self.assertIn(resp.status_code, (302, 303))
        self.assertIsNone(self._row("Role Attempt"))

    def test_non_admin_cannot_edit(self):
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO workers (name, address, availability) "
            "VALUES (?,?,?)", ("Target", "Rue 8", ""),
        )
        conn.commit()
        conn.close()
        worker_client = app.app.test_client()
        with worker_client.session_transaction() as sess:
            sess["user"] = "someone"
            sess["role"] = "worker"
            sess["lang"] = "en"
        resp = worker_client.post(
            "/edit_worker/" + urllib.parse.quote("Target"),
            data={
                "name": "Target",
                "address": "Rue 8",
                "availability_json": json.dumps({"mon": []}),
            },
        )
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(self._row("Target"), "")

    def test_backward_compat_existing_worker_without_availability(self):
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO workers (name, address) VALUES (?,?)",
            ("Legacy", "Rue 9"),
        )
        conn.commit()
        conn.close()
        # Legacy row has '' from the DEFAULT; GET must not crash.
        resp = self.client.get(
            "/edit_worker/" + urllib.parse.quote("Legacy"),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._row("Legacy"), "")


class TranslationCoverageTests(unittest.TestCase):
    AVAILABILITY_KEYS = [
        "auto_plan_availability_title",
        "auto_plan_availability_empty_hint",
        "auto_plan_availability_mode_undefined",
        "auto_plan_availability_mode_day_off",
        "auto_plan_availability_mode_working",
        "auto_plan_availability_add_interval",
        "auto_plan_availability_invalid",
        "auto_plan_availability_invalid_invalid_json",
        "auto_plan_availability_invalid_not_an_object",
        "auto_plan_availability_invalid_bad_day_key",
        "auto_plan_availability_invalid_day_not_a_list",
        "auto_plan_availability_invalid_too_many_intervals",
        "auto_plan_availability_invalid_interval_not_object",
        "auto_plan_availability_invalid_unknown_key",
        "auto_plan_availability_invalid_bad_start",
        "auto_plan_availability_invalid_bad_end",
        "auto_plan_availability_invalid_end_before_start",
        "auto_plan_availability_invalid_overlapping_intervals",
        "auto_plan_warning_outside_working_hours",
    ]

    def test_every_language_has_every_key(self):
        for lang in ("bos", "fr", "en", "de", "pt"):
            with app.app.test_request_context():
                from flask import session as flask_session
                flask_session["lang"] = lang
                tr = app.t()
                for key in self.AVAILABILITY_KEYS:
                    self.assertIn(key, tr,
                                  f"{lang} missing translation {key}")


if __name__ == "__main__":
    unittest.main()
