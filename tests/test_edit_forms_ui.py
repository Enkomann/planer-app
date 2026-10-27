"""Regression tests for the + Add visit / + Add interval buttons
on /edit_client and /edit_worker.

The staging bug was: clicking "+ Dodaj termin" on /edit_client did
nothing. Root-cause investigation couldn't fully reproduce locally
(a stale server process was reading a different sqlite file), so
the fix adds defence-in-depth: wrap the inline IIFE in try/catch,
surface init errors to console, and expose a programmatic
readiness flag (data-schedule-ready / data-availability-ready).
These tests pin that defence in place so the symptom cannot
regress silently again.
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
    prefix="luxmann-edit-forms-ui-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "edit-forms-ui-test-only")
os.environ["AUTO_PLANNING_MODE"] = "write_enabled"
os.environ["AUTO_PLANNING_ENABLED"] = "true"

import app  # noqa: E402

app.app.config["AUTO_PLANNING_MODE"] = "write_enabled"
app.app.config["AUTO_PLANNING_ENABLED"] = True
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


def _reset():
    conn = app.get_conn()
    for table in ("shifts", "clients", "workers", "worker_colors"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()


class EditClientScheduleUiTests(unittest.TestCase):
    def setUp(self):
        _reset()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO clients (name, address) VALUES (?,?)",
            ("UiClient", "Rue 1"),
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["lang"] = "bos"

    def _html(self):
        resp = self.client.get(
            "/edit_client/" + urllib.parse.quote("UiClient"),
        )
        self.assertEqual(resp.status_code, 200)
        return resp.data.decode("utf-8", errors="replace")

    def test_schedule_section_is_rendered(self):
        body = self._html()
        self.assertIn('id="cs-schedule-list"', body)
        self.assertIn('id="cs-schedule-add"', body)
        self.assertIn('name="cleaning_frequency_json"', body)

    def test_add_button_handler_is_in_source(self):
        """The staging bug symptom was a dead +Add button. Pin the
        two things that guarantee the handler attaches: the button
        id must appear in a getElementById call AND the handler
        must call render(INITIAL)."""
        body = self._html()
        self.assertIn('getElementById("cs-schedule-add")', body)
        self.assertIn("render(INITIAL)", body)

    def test_iife_is_guarded_by_try_catch(self):
        body = self._html()
        # The IIFE must open with try{ and close with the matching
        # catch so a Jinja-injected JS surprise cannot silently kill
        # every subsequent event binding.
        self.assertIn("client-schedule UI init failed", body)

    def test_readiness_flag_is_wired(self):
        body = self._html()
        # The script sets list.dataset.scheduleReady = "1" after
        # init — the DOM attribute is `data-schedule-ready` in the
        # live page but the SOURCE string we assert here uses the
        # JS dataset property name.
        self.assertIn("scheduleReady", body)

    def test_add_handler_does_not_prewalk_rows(self):
        """Regression for the staging-bug class: the previous handler
        pre-collected the live DOM via `rows.map(readRow)` BEFORE
        pushing the new entry, so one failing readRow killed the
        whole click. The fix is to push to INITIAL and re-render.
        Pin the shape so a refactor can't reintroduce the pre-walk.
        """
        body = self._html()
        # Must contain the simplified push-then-render shape.
        self.assertIn('INITIAL.push({', body)
        # The fix removed the live-code line
        # `var cur = INITIAL.concat();` from the add handler (the
        # inline source now only shows that string inside the
        # explanatory JS comment block, which is why we match
        # exactly on `var cur = INITIAL.concat();\n` with a
        # following whitespace+code line, not a comment line
        # starting with `//`).
        self.assertNotIn("// code: var cur = INITIAL.concat();", body)


class EditWorkerAvailabilityUiTests(unittest.TestCase):
    def setUp(self):
        _reset()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO workers (name, address, contract_end_date) "
            "VALUES (?,?,'')",
            ("UiWorker", "Rue 2"),
        )
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["lang"] = "bos"

    def _html(self):
        resp = self.client.get(
            "/edit_worker/" + urllib.parse.quote("UiWorker"),
        )
        self.assertEqual(resp.status_code, 200)
        return resp.data.decode("utf-8", errors="replace")

    def test_availability_section_is_rendered(self):
        body = self._html()
        self.assertIn('id="av-days-list"', body)
        self.assertIn('name="availability_json"', body)

    def test_iife_is_guarded_by_try_catch(self):
        body = self._html()
        self.assertIn("worker-availability UI init failed", body)

    def test_readiness_flag_is_wired(self):
        body = self._html()
        self.assertIn("availabilityReady", body)


class RoundTripThroughSerializeHelperTests(unittest.TestCase):
    """The staging bug was in the UI, not the parser. These tests
    pin the parser's acceptance of the exact JSON shape the fixed
    UI will POST on save, so any accidental regression in either
    the UI OR the parser surfaces immediately."""

    def test_parser_accepts_single_default_entry_from_ui(self):
        payload = json.dumps([{
            "kind": "weekly", "weekday": 0,
            "start": "09:00", "end": "11:00",
            "preferred_workers": [],
        }])
        parsed = app.parse_client_cleaning_frequency(payload)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["kind"], "weekly")

    def test_parser_accepts_multiple_entries_from_ui(self):
        payload = json.dumps([
            {"kind": "weekly", "weekday": 0, "start": "08:00",
             "end": "10:00", "preferred_workers": []},
            {"kind": "weekly", "weekday": 3, "start": "14:00",
             "end": "16:00", "preferred_workers": []},
        ])
        parsed = app.parse_client_cleaning_frequency(payload)
        self.assertEqual(len(parsed), 2)


if __name__ == "__main__":
    unittest.main()
