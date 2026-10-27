"""Phase 3 tests — schedule-based projection, availability warning,
combined generator precedence, and the /plan/generate wiring that
persists the ``source`` tag on each saved proposal.
"""
import json
import os
import shutil
import tempfile
import unittest

TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-phase3-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "phase3-test-only")
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


# 2026-10-26 is a Monday (used as target week start throughout).
MONDAY = "2026-10-26"
TUESDAY = "2026-10-27"


def _reset_tables():
    conn = app.get_conn()
    for table in ("shift_proposals", "shift_proposal_batches",
                  "shift_locks", "shifts", "absences", "leave_requests",
                  "holidays", "workers", "clients", "worker_colors"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def _seed_client(name, schedule_json="", cfrom="", cto=""):
    conn = app.get_conn()
    conn.execute(
        "INSERT INTO clients (name, address, cleaning_frequency, "
        "contract_from, contract_to) VALUES (?,?,?,?,?)",
        (name, "Rue 1", schedule_json, cfrom, cto),
    )
    conn.commit()
    conn.close()


def _seed_worker(name, availability=""):
    conn = app.get_conn()
    conn.execute(
        "INSERT INTO workers (name, contract_end_date, availability) "
        "VALUES (?, '', ?)", (name, availability),
    )
    conn.commit()
    conn.close()


class ScheduleProjectorTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()

    def _schedule_only(self, target=MONDAY):
        conn = app.get_conn()
        try:
            return app.ap_project_from_schedule(conn, target)
        finally:
            conn.close()

    def test_weekly_schedule_projects_to_target_week(self):
        _seed_worker("Aida")
        _seed_client("Alpha", json.dumps([{
            "kind": "weekly", "weekday": 1,  # Tuesday
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]))
        props, scheduled = self._schedule_only()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["target_date"], TUESDAY)
        self.assertEqual(props[0]["client"], "Alpha")
        self.assertEqual(props[0]["worker"], "Aida")
        self.assertEqual(props[0]["source"], "schedule")
        self.assertEqual(scheduled, {"Alpha"})

    def test_multiple_visits_per_week(self):
        _seed_worker("Aida")
        _seed_client("Beta", json.dumps([
            {"kind": "weekly", "weekday": 0, "start": "08:00",
             "end": "10:00", "preferred_workers": ["Aida"]},
            {"kind": "weekly", "weekday": 3, "start": "14:00",
             "end": "16:00", "preferred_workers": ["Aida"]},
        ]))
        props, _ = self._schedule_only()
        self.assertEqual(len(props), 2)
        dates = sorted(p["target_date"] for p in props)
        self.assertEqual(dates[0], MONDAY)
        self.assertEqual(dates[1], "2026-10-29")  # Thursday

    def test_biweekly_due_on_anchor_week(self):
        _seed_worker("Aida")
        _seed_client("Gamma", json.dumps([{
            "kind": "biweekly", "weekday": 0,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-10-26",  # the target Monday
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["recurrence_period"], 2)

    def test_biweekly_skipped_on_off_week(self):
        _seed_worker("Aida")
        _seed_client("Delta", json.dumps([{
            "kind": "biweekly", "weekday": 0,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-10-19",  # one week BEFORE target
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only()
        self.assertEqual(props, [])  # off-cycle

    def test_monthly_first_week_match(self):
        # 2026-11-02 is first Monday of November.
        _seed_worker("Aida")
        _seed_client("Epsilon", json.dumps([{
            "kind": "monthly", "weekday": 0, "monthly_week": 1,
            "start": "08:00", "end": "10:00",
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only(target="2026-11-02")
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["recurrence_period"], 4)

    def test_monthly_last_week_match(self):
        _seed_worker("Aida")
        _seed_client("Zeta", json.dumps([{
            "kind": "monthly", "weekday": 0, "monthly_week": -1,
            "start": "08:00", "end": "10:00",
            "preferred_workers": ["Aida"],
        }]))
        # 2026-10-26 is the LAST Monday of October.
        props, _ = self._schedule_only(target=MONDAY)
        self.assertEqual(len(props), 1)

    def test_custom_interval_due(self):
        _seed_worker("Aida")
        _seed_client("Eta", json.dumps([{
            "kind": "custom", "weekday": 0, "custom_interval_weeks": 3,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-10-05",  # 3 weeks before target
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only(target=MONDAY)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["recurrence_period"], 3)

    def test_year_boundary(self):
        # anchor 2026-12-28 (Monday) + every 3 weeks → 2027-01-18
        _seed_worker("Aida")
        _seed_client("Theta", json.dumps([{
            "kind": "custom", "weekday": 0, "custom_interval_weeks": 3,
            "start": "08:00", "end": "10:00",
            "anchor_date": "2026-12-28",
            "preferred_workers": ["Aida"],
        }]))
        props, _ = self._schedule_only(target="2027-01-18")
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["target_date"], "2027-01-18")

    def test_contract_window_filters(self):
        _seed_worker("Aida")
        _seed_client("Iota", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]), cfrom="2027-01-01")
        props, _ = self._schedule_only(target=MONDAY)
        self.assertEqual(props, [])

    def test_unknown_preferred_worker_surfaces_warning(self):
        # Aida was deleted since the schedule was saved.
        _seed_client("Kappa", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["GhostA", "GhostB"],
        }]))
        props, _ = self._schedule_only()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["worker"], "")  # for manual pick
        codes = [i["code"] for i in props[0]["issues"]]
        self.assertIn("preferred_worker_unknown", codes)

    def test_corrupt_schedule_json_surfaces_blocking_issue(self):
        _seed_client("Lambda", "not{json")
        props, scheduled = self._schedule_only()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["client"], "Lambda")
        codes = [i["code"] for i in props[0]["issues"]]
        self.assertIn("schedule_invalid", codes)
        # Client is in the scheduled-set so history projector would
        # not duplicate for it.
        self.assertIn("Lambda", scheduled)


class CombinedGeneratorPrecedenceTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()

    def test_schedule_takes_precedence_over_history(self):
        _seed_worker("Aida")
        _seed_client("Mu", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]))
        # Seed enough history for the engine to also project Mu.
        conn = app.get_conn()
        for d in ("2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25",
                  "2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22"):
            conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (?, ?, ?, '08:00-11:00', 'done')",
                ("Aida", "Mu", d),
            )
        conn.commit()
        conn.close()

        conn = app.get_conn()
        try:
            merged = app.ap_generate_combined_proposals(
                conn, MONDAY, lookback_weeks=8,
                include_exceptional=False,
            )
        finally:
            conn.close()
        same_date_props = [p for p in merged if p["client"] == "Mu"
                           and p["target_date"] == TUESDAY]
        self.assertEqual(len(same_date_props), 1)
        self.assertEqual(same_date_props[0]["source"], "schedule")

    def test_history_still_runs_for_non_scheduled_clients(self):
        _seed_worker("Mila")
        _seed_client("Nu")  # no schedule
        conn = app.get_conn()
        for d in ("2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25",
                  "2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22"):
            conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (?, ?, ?, '09:00-11:00', 'done')",
                ("Mila", "Nu", d),
            )
        conn.commit()
        conn.close()
        conn = app.get_conn()
        try:
            merged = app.ap_generate_combined_proposals(
                conn, MONDAY, lookback_weeks=8,
            )
        finally:
            conn.close()
        hist = [p for p in merged if p["client"] == "Nu"]
        self.assertTrue(len(hist) >= 1)
        self.assertEqual(hist[0]["source"], "history")


class AvailabilityWarningTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()

    def _check_with_av(self, av):
        _seed_worker("Aida", availability=av)
        _seed_client("Omicron", json.dumps([{
            "kind": "weekly", "weekday": 1,  # Tuesday
            "start": "13:00", "end": "15:00",
            "preferred_workers": ["Aida"],
        }]))
        conn = app.get_conn()
        try:
            props = app.ap_generate_combined_proposals(conn, MONDAY)
            return app.ap_run_checks(conn, props, MONDAY)
        finally:
            conn.close()

    def test_shift_inside_working_hours_no_warning(self):
        checked = self._check_with_av(json.dumps({
            "tue": [{"start": "08:00", "end": "17:00"}],
        }))
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertNotIn("outside_working_hours", codes)

    def test_shift_outside_working_hours_warns(self):
        checked = self._check_with_av(json.dumps({
            "tue": [{"start": "08:00", "end": "12:00"}],
        }))
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertIn("outside_working_hours", codes)

    def test_day_off_warns(self):
        checked = self._check_with_av(json.dumps({"tue": []}))
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertIn("outside_working_hours", codes)

    def test_empty_availability_no_warning(self):
        checked = self._check_with_av("")
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertNotIn("outside_working_hours", codes)

    def test_absence_still_blocks(self):
        _seed_worker("Aida")
        _seed_client("Pi", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]))
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO absences (worker, type, date_from, date_to, note) "
            "VALUES ('Aida', 'sick', ?, ?, '')",
            (TUESDAY, TUESDAY),
        )
        conn.commit()
        try:
            props = app.ap_generate_combined_proposals(conn, MONDAY)
            checked = app.ap_run_checks(conn, props, MONDAY)
        finally:
            conn.close()
        codes = {i["code"] for p in checked for i in p["issues"]}
        self.assertIn("absence", codes)


class SchemaMigrationTests(unittest.TestCase):
    def test_source_column_present(self):
        conn = app.get_conn()
        try:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(shift_proposals)"
            ).fetchall()]
        finally:
            conn.close()
        self.assertIn("source", cols)

    def test_init_db_idempotent(self):
        app.init_db()
        app.init_db()


class HttpWiringTests(unittest.TestCase):
    def setUp(self):
        _reset_tables()
        self.client = app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"
            sess["role"] = "admin"
            sess["lang"] = "en"

    def _csrf(self):
        resp = self.client.get("/plan/generate")
        # Confirm GET actually rendered the form, not a redirect.
        assert resp.status_code == 200, \
            f"GET /plan/generate returned {resp.status_code}"
        with self.client.session_transaction() as sess:
            return sess[app.AP_CSRF_SESSION_KEY]

    def test_generate_persists_schedule_sourced_proposal(self):
        _seed_worker("Aida")
        _seed_client("Rho", json.dumps([{
            "kind": "weekly", "weekday": 1,
            "start": "08:00", "end": "11:00",
            "preferred_workers": ["Aida"],
        }]))
        resp = self.client.post("/plan/generate", data={
            "target_week_start": MONDAY,
            "lookback_weeks": "8",
            "csrf_token": self._csrf(),
        })
        self.assertIn(resp.status_code, (302, 303))
        # Read back the single proposal and confirm source="schedule".
        conn = app.get_conn()
        try:
            rows = conn.execute(
                "SELECT client, target_date, source FROM shift_proposals"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Rho")
        self.assertEqual(rows[0][1], TUESDAY)
        self.assertEqual(rows[0][2], "schedule")


class TranslationCoverageTests(unittest.TestCase):
    PHASE3_KEYS = [
        "auto_plan_issue_outside_working_hours",
        "auto_plan_issue_schedule_invalid",
        "auto_plan_issue_preferred_worker_unknown",
        "auto_plan_source_schedule",
        "auto_plan_source_history",
    ]

    def test_each_language_has_each_key(self):
        for lang in ("bos", "fr", "en", "de", "pt"):
            with app.app.test_request_context():
                from flask import session as flask_session
                flask_session["lang"] = lang
                tr = app.t()
                for key in self.PHASE3_KEYS:
                    self.assertIn(key, tr,
                                  f"{lang} missing {key}")


if __name__ == "__main__":
    unittest.main()
