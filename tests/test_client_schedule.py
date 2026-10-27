"""Phase 1 tests: client cleaning_frequency JSON schema, parser, forms."""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import urllib.parse

TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-client-schedule-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "client-schedule-test-only")
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


class ParseEmptyTests(unittest.TestCase):
    """Empty values preserve pre-feature behaviour (history-based)."""

    def test_none_returns_empty_list(self):
        self.assertEqual(app.parse_client_cleaning_frequency(None), [])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(app.parse_client_cleaning_frequency(""), [])

    def test_whitespace_returns_empty_list(self):
        self.assertEqual(app.parse_client_cleaning_frequency("   \n\t"), [])

    def test_bytes_empty_returns_empty_list(self):
        self.assertEqual(app.parse_client_cleaning_frequency(b""), [])

    def test_serialize_empty_returns_empty_string(self):
        self.assertEqual(app.serialize_client_cleaning_frequency([]), "")
        self.assertEqual(app.serialize_client_cleaning_frequency(None), "")


class ParseValidTests(unittest.TestCase):
    """Each supported kind round-trips through parse+serialize."""

    def _roundtrip(self, entries):
        raw = json.dumps(entries)
        parsed = app.parse_client_cleaning_frequency(raw)
        reparsed = app.parse_client_cleaning_frequency(
            app.serialize_client_cleaning_frequency(parsed),
        )
        self.assertEqual(parsed, reparsed)
        return parsed

    def test_weekly(self):
        parsed = self._roundtrip([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }])
        self.assertEqual(parsed[0]["kind"], "weekly")
        self.assertEqual(parsed[0]["preferred_workers"], ["Aida"])

    def test_biweekly_requires_anchor(self):
        parsed = self._roundtrip([{
            "kind": "biweekly", "weekday": 0,
            "start": "09:00", "end": "12:00",
            "anchor_date": "2026-01-05",  # Monday
            "preferred_workers": [],
        }])
        self.assertEqual(parsed[0]["anchor_date"], "2026-01-05")

    def test_monthly_week(self):
        parsed = self._roundtrip([{
            "kind": "monthly", "weekday": 2, "monthly_week": -1,
            "start": "07:30", "end": "09:30",
            "preferred_workers": ["A", "B"],
        }])
        self.assertEqual(parsed[0]["monthly_week"], -1)
        self.assertNotIn("custom_interval_weeks", parsed[0])

    def test_custom_interval(self):
        parsed = self._roundtrip([{
            "kind": "custom", "weekday": 3, "custom_interval_weeks": 3,
            "start": "10:00", "end": "12:00",
            "anchor_date": "2026-01-08",  # Thursday (weekday=3)
            "preferred_workers": [],
        }])
        self.assertEqual(parsed[0]["custom_interval_weeks"], 3)

    def test_year_boundary_custom_anchor(self):
        """Anchor 2026-12-28 (Mon) + 3-week cycle must validate."""
        parsed = self._roundtrip([{
            "kind": "custom", "weekday": 0, "custom_interval_weeks": 3,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-12-28",
            "preferred_workers": [],
        }])
        self.assertEqual(parsed[0]["anchor_date"], "2026-12-28")

    def test_multiple_entries_per_week(self):
        parsed = self._roundtrip([
            {"kind": "weekly", "weekday": 1, "start": "08:00",
             "end": "10:00", "preferred_workers": []},
            {"kind": "weekly", "weekday": 4, "start": "14:00",
             "end": "16:00", "preferred_workers": ["Aida"]},
        ])
        self.assertEqual(len(parsed), 2)


class ParseInvalidTests(unittest.TestCase):
    def _bad(self, raw, expected_code):
        with self.assertRaises(app.ClientScheduleInvalid) as ctx:
            app.parse_client_cleaning_frequency(raw)
        self.assertEqual(ctx.exception.code, expected_code)

    def test_not_json(self):
        self._bad("not{json", "invalid_json")

    def test_not_a_list(self):
        self._bad('{"kind":"weekly"}', "not_a_list")

    def test_too_many_entries(self):
        huge = json.dumps([
            {"kind": "weekly", "weekday": 0, "start": "08:00",
             "end": "09:00", "preferred_workers": []}
        ] * 33)
        self._bad(huge, "too_many_entries")

    def test_entry_not_object(self):
        self._bad('["weekly"]', "entry_not_object")

    def test_unknown_key(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "08:00", "end": "09:00",
            "preferred_workers": [], "surprise": 1,
        }]), "unknown_key")

    def test_bad_kind(self):
        self._bad(json.dumps([{
            "kind": "yearly", "weekday": 0,
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "bad_kind")

    def test_bad_weekday_type(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": "Mon",
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "bad_weekday")

    def test_bad_weekday_range(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 7,
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "bad_weekday")

    def test_bad_start(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "8am", "end": "09:00",
            "preferred_workers": [],
        }]), "bad_start")

    def test_bad_end(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "08:00", "end": "25:00",
            "preferred_workers": [],
        }]), "bad_end")

    def test_end_before_start(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "10:00", "end": "09:00",
            "preferred_workers": [],
        }]), "end_before_start")

    def test_end_equal_start(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "10:00", "end": "10:00",
            "preferred_workers": [],
        }]), "end_before_start")

    def test_biweekly_requires_anchor(self):
        self._bad(json.dumps([{
            "kind": "biweekly", "weekday": 0,
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "anchor_date_required")

    def test_custom_requires_anchor(self):
        self._bad(json.dumps([{
            "kind": "custom", "weekday": 0, "custom_interval_weeks": 3,
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "anchor_date_required")

    def test_bad_anchor_date(self):
        self._bad(json.dumps([{
            "kind": "biweekly", "weekday": 0,
            "start": "08:00", "end": "09:00",
            "anchor_date": "2026/01/05",
            "preferred_workers": [],
        }]), "bad_anchor_date")

    def test_anchor_weekday_mismatch(self):
        self._bad(json.dumps([{
            "kind": "biweekly", "weekday": 0,  # Monday
            "start": "08:00", "end": "09:00",
            "anchor_date": "2026-01-06",  # Tuesday
            "preferred_workers": [],
        }]), "anchor_weekday_mismatch")

    def test_bad_monthly_week(self):
        self._bad(json.dumps([{
            "kind": "monthly", "weekday": 0, "monthly_week": 5,
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "bad_monthly_week")

    def test_monthly_week_on_weekly_rejected(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0, "monthly_week": 1,
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "monthly_week_not_allowed")

    def test_bad_custom_interval(self):
        self._bad(json.dumps([{
            "kind": "custom", "weekday": 0, "custom_interval_weeks": 1,
            "anchor_date": "2026-01-05",
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "bad_custom_interval")

    def test_custom_interval_too_large(self):
        self._bad(json.dumps([{
            "kind": "custom", "weekday": 0, "custom_interval_weeks": 53,
            "anchor_date": "2026-01-05",
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "bad_custom_interval")

    def test_custom_interval_on_weekly_rejected(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0, "custom_interval_weeks": 2,
            "start": "08:00", "end": "09:00",
            "preferred_workers": [],
        }]), "custom_interval_not_allowed")

    def test_duplicate_preferred_workers(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "08:00", "end": "09:00",
            "preferred_workers": ["A", "A"],
        }]), "preferred_worker_duplicate")

    def test_preferred_workers_not_list(self):
        self._bad(json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "08:00", "end": "09:00",
            "preferred_workers": "A",
        }]), "preferred_workers_not_a_list")


class SchemaMigrationTests(unittest.TestCase):
    """``init_db`` adds ``cleaning_frequency`` once and is idempotent."""

    def test_column_present(self):
        conn = app.get_conn()
        try:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(clients)"
            ).fetchall()]
        finally:
            conn.close()
        self.assertIn("cleaning_frequency", cols)

    def test_init_db_is_idempotent(self):
        # Already called at import; call again — must not raise.
        app.init_db()
        app.init_db()


class HttpFormTests(unittest.TestCase):
    def setUp(self):
        conn = app.get_conn()
        for table in ("shifts", "clients", "workers"):
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
                "SELECT COALESCE(cleaning_frequency,'') FROM clients "
                "WHERE name=?", (name,),
            ).fetchone()
            return r[0] if r else None
        finally:
            conn.close()

    def test_add_client_without_schedule_field_stores_empty(self):
        # Dashboard quick-add path: field missing entirely → ''
        resp = self.client.post("/add_client", data={
            "client_name": "QA Client",
            "address": "Rue 1",
            "next": "/",
        })
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(self._row("QA Client"), "")

    def test_add_client_with_valid_schedule(self):
        payload = json.dumps([{
            "kind": "weekly", "weekday": 2,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }])
        resp = self.client.post("/add_client", data={
            "client_name": "Full Form Client",
            "address": "Rue 2",
            "client_type": "private",
            "hourly_rate": "30",
            "vat_rate": "8",
            "cleaning_frequency_json": payload,
        })
        self.assertIn(resp.status_code, (302, 303))
        stored = self._row("Full Form Client")
        self.assertNotEqual(stored, "")
        parsed = app.parse_client_cleaning_frequency(stored)
        self.assertEqual(parsed[0]["kind"], "weekly")
        self.assertEqual(parsed[0]["preferred_workers"], ["Aida"])

    def test_add_client_with_invalid_schedule_rejects(self):
        resp = self.client.post("/add_client", data={
            "client_name": "Bad Client",
            "address": "Rue 3",
            "client_type": "private",
            "hourly_rate": "30",
            "vat_rate": "8",
            "cleaning_frequency_json": "not{json",
        })
        self.assertIn(resp.status_code, (302, 303))
        # Row must NOT have been inserted.
        self.assertIsNone(self._row("Bad Client"))

    def test_edit_client_preserves_schedule_when_field_absent(self):
        # Seed a client that already has a schedule, then POST a
        # minimal /edit_client form that doesn't include the hidden
        # cleaning_frequency_json input: existing JSON must survive.
        existing = app.serialize_client_cleaning_frequency(
            app.parse_client_cleaning_frequency(json.dumps([{
                "kind": "weekly", "weekday": 0,
                "start": "08:00", "end": "10:00",
                "preferred_workers": [],
            }])),
        )
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO clients (name, address, cleaning_frequency) "
            "VALUES (?,?,?)", ("Preserve Me", "Rue 9", existing),
        )
        conn.commit()
        conn.close()
        # Not sending cleaning_frequency_json intentionally.
        self.client.post(
            "/edit_client/" + urllib.parse.quote("Preserve Me"),
            data={
                "name": "Preserve Me",
                "address": "Rue 9",
                "client_type": "private",
                "hourly_rate": "25",
                "vat_rate": "8",
            },
        )
        self.assertEqual(self._row("Preserve Me"), existing)

    def test_edit_client_updates_schedule(self):
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO clients (name, address) VALUES (?,?)",
            ("Edit Me", "Rue 10"),
        )
        conn.commit()
        conn.close()
        payload = json.dumps([{
            "kind": "biweekly", "weekday": 0,
            "start": "08:00", "end": "11:00",
            "anchor_date": "2026-01-05",
            "preferred_workers": [],
        }])
        self.client.post(
            "/edit_client/" + urllib.parse.quote("Edit Me"),
            data={
                "name": "Edit Me",
                "address": "Rue 10",
                "client_type": "private",
                "hourly_rate": "25",
                "vat_rate": "8",
                "cleaning_frequency_json": payload,
            },
        )
        stored = self._row("Edit Me")
        parsed = app.parse_client_cleaning_frequency(stored)
        self.assertEqual(parsed[0]["kind"], "biweekly")
        self.assertEqual(parsed[0]["anchor_date"], "2026-01-05")

    def test_non_admin_cannot_add(self):
        worker = app.app.test_client()
        with worker.session_transaction() as sess:
            sess["user"] = "someone"
            sess["role"] = "worker"
            sess["lang"] = "en"
        resp = worker.post("/add_client", data={
            "client_name": "Role Attempt",
            "address": "Rue X",
        })
        self.assertIn(resp.status_code, (302, 303))
        self.assertIsNone(self._row("Role Attempt"))


class TranslationCoverageTests(unittest.TestCase):
    """Every supported language must carry each new schedule key."""

    SCHEDULE_KEYS = [
        "auto_plan_schedule_title",
        "auto_plan_schedule_add",
        "auto_plan_schedule_remove",
        "auto_plan_frequency_kind_weekly",
        "auto_plan_frequency_kind_biweekly",
        "auto_plan_frequency_kind_monthly",
        "auto_plan_frequency_kind_custom",
        "auto_plan_frequency_weekday_label",
        "auto_plan_frequency_start",
        "auto_plan_frequency_end",
        "auto_plan_frequency_anchor_date",
        "auto_plan_frequency_monthly_week",
        "auto_plan_frequency_custom_interval_weeks",
        "auto_plan_frequency_preferred_workers",
        "auto_plan_schedule_invalid",
        "auto_plan_schedule_invalid_invalid_json",
        "auto_plan_schedule_invalid_bad_kind",
        "auto_plan_schedule_invalid_bad_weekday",
        "auto_plan_schedule_invalid_bad_start",
        "auto_plan_schedule_invalid_bad_end",
        "auto_plan_schedule_invalid_end_before_start",
        "auto_plan_schedule_invalid_anchor_date_required",
        "auto_plan_schedule_invalid_bad_anchor_date",
        "auto_plan_schedule_invalid_anchor_weekday_mismatch",
        "auto_plan_schedule_invalid_bad_monthly_week",
        "auto_plan_schedule_invalid_bad_custom_interval",
    ]

    def test_all_five_languages_have_every_key(self):
        for lang in ("bos", "fr", "en", "de", "pt"):
            client = app.app.test_client()
            with client.session_transaction() as sess:
                sess["user"] = "admin"
                sess["role"] = "admin"
                sess["lang"] = lang
            with app.app.test_request_context():
                from flask import session as flask_session
                flask_session["lang"] = lang
                tr = app.t()
                for key in self.SCHEDULE_KEYS:
                    self.assertIn(key, tr,
                                  f"{lang} missing translation key {key}")


if __name__ == "__main__":
    unittest.main()
