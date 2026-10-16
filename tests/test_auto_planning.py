import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date


# Importing app.py runs its existing idempotent init_db(). Point it at an
# isolated temporary SQLite tree before import so tests can never touch the
# configured PostgreSQL service or the developer's normal local database.
TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-auto-planning-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "auto-planning-test-only")

import app  # noqa: E402


def tearDownModule():
    shutil.rmtree(TEST_STORAGE, ignore_errors=True)
    try:
        os.rmdir(TEST_TEMP_ROOT)
    except OSError:
        pass


def shift(row_id, workers, client, day, hours="08:00-10:00"):
    return (row_id, workers, client, day, hours, "done")


class RecurrenceEngineTests(unittest.TestCase):
    def test_lookback_bounds_are_date_only_across_dst_changes(self):
        self.assertEqual(
            app.ap_lookback_bounds("2026-03-30", 2),
            (date(2026, 3, 16), date(2026, 3, 29)),
        )
        self.assertEqual(
            app.ap_lookback_bounds("2026-10-26", 2),
            (date(2026, 10, 12), date(2026, 10, 25)),
        )

    def test_weekly_recurrence(self):
        rows = [
            shift(i, "Aida", "Maison A", day)
            for i, day in enumerate([
                "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
                "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
            ], 1)
        ]
        groups = app.ap_group_by_key(rows)
        proposals = app.ap_project(groups, "2026-11-02", lookback_weeks=8)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["recurrence_period"], 1)
        self.assertEqual(proposals[0]["confidence"], 1.0)
        self.assertFalse(proposals[0]["requires_manual_validation"])

    def test_every_two_weeks(self):
        rows = [
            shift(i, "Aida", "Bureau B", day)
            for i, day in enumerate(
                ["2026-09-07", "2026-09-21", "2026-10-05", "2026-10-19"], 1
            )
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=8
        )
        self.assertEqual(proposals[0]["recurrence_period"], 2)
        self.assertEqual(proposals[0]["confidence"], 1.0)

    def test_every_three_weeks(self):
        rows = [
            shift(i, "Aida", "Restaurant C", day)
            for i, day in enumerate(
                ["2026-09-07", "2026-09-28", "2026-10-19"], 1
            )
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-09", lookback_weeks=8
        )
        self.assertEqual(proposals[0]["recurrence_period"], 3)
        self.assertEqual(proposals[0]["target_date"], "2026-11-09")

    def test_every_four_weeks_monthly_cycle(self):
        rows = [
            shift(1, "Aida", "Appartement D", "2026-09-07"),
            shift(2, "Aida", "Appartement D", "2026-10-05"),
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=8
        )
        self.assertEqual(proposals[0]["recurrence_period"], 4)
        self.assertEqual(proposals[0]["confidence"], 1.0)

    def test_calendar_monthly_cycle_handles_five_week_gap(self):
        rows = [
            shift(1, "Aida", "Monthly office", "2026-09-07"),
            shift(2, "Aida", "Monthly office", "2026-10-05"),
            shift(3, "Aida", "Monthly office", "2026-11-02"),
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-12-07", lookback_weeks=8
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["recurrence_period"], 4)
        self.assertTrue(proposals[0]["calendar_monthly"])

    def test_missing_occurrence_keeps_cadence_but_lowers_confidence(self):
        rows = [
            shift(1, "Aida", "Interrupted weekly", "2026-10-05"),
            shift(2, "Aida", "Interrupted weekly", "2026-10-12"),
            shift(3, "Aida", "Interrupted weekly", "2026-10-26"),
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=4
        )
        self.assertEqual(proposals[0]["recurrence_period"], 1)
        self.assertAlmostEqual(proposals[0]["confidence"], 0.75)

    def test_team_signature_is_order_independent(self):
        rows = [
            shift(1, "Aida, Edita", "Maison E", "2026-10-19"),
            shift(2, "Edita, Aida", "Maison E", "2026-10-26"),
        ]
        groups = app.ap_group_by_key(rows)
        self.assertEqual(len(groups), 1)
        proposals = app.ap_project(groups, "2026-11-02", lookback_weeks=2)
        self.assertEqual(proposals[0]["worker"], "Aida, Edita")

    def test_one_shot_is_hidden_unless_exceptional_is_requested(self):
        groups = app.ap_group_by_key([
            shift(1, "Aida", "One shot", "2026-10-26")
        ])
        self.assertEqual(
            app.ap_project(groups, "2026-11-02", lookback_weeks=8), []
        )
        proposals = app.ap_project(
            groups, "2026-11-02", include_exceptional=True,
            lookback_weeks=8,
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["recurrence_period"], 0)
        self.assertTrue(proposals[0]["requires_manual_validation"])

    def test_low_confidence_cycle_remains_visible_for_manual_review(self):
        rows = [
            shift(1, "Aida", "Sparse weekly", "2026-10-19"),
            shift(2, "Aida", "Sparse weekly", "2026-10-26"),
        ]
        proposals = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=8
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["recurrence_period"], 1)
        self.assertAlmostEqual(proposals[0]["confidence"], 0.25)
        self.assertTrue(proposals[0]["requires_manual_validation"])

    def test_non_quarter_hour_times_and_duration_are_preserved(self):
        rows = [
            shift(1, "Aida", "Precise", "2026-10-19", "08:07-09:23"),
            shift(2, "Aida", "Precise", "2026-10-26", "08:07-09:23"),
        ]
        proposal = app.ap_project(
            app.ap_group_by_key(rows), "2026-11-02", lookback_weeks=2
        )[0]
        self.assertEqual(proposal["time"], "08:07-09:23")
        self.assertAlmostEqual(proposal["duration_hours"], 76 / 60.0)

    def test_fetch_history_uses_inclusive_bounds_and_stable_shape(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE shifts (id INTEGER PRIMARY KEY, worker TEXT, "
            "client TEXT, date TEXT, time TEXT, status TEXT)"
        )
        conn.executemany(
            "INSERT INTO shifts VALUES (?,?,?,?,?,?)",
            [
                shift(1, "Aida", "Before", "2026-10-18"),
                shift(2, "Aida", "First", "2026-10-19"),
                shift(3, "Aida", "Last", "2026-11-01"),
                shift(4, "Aida", "After", "2026-11-02"),
            ],
        )
        rows = app.ap_fetch_history(conn, "2026-10-19", "2026-11-01")
        conn.close()
        self.assertEqual([row[2] for row in rows], ["First", "Last"])
        self.assertTrue(all(len(row) == 6 for row in rows))


def proposal(day="2026-11-02", hours="08:00-10:00", workers="Aida",
             client="Client A", **extra):
    value = {
        "target_date": day,
        "time": hours,
        "worker": workers,
        "client": client,
        "issues": [],
    }
    value.update(extra)
    return value


class ConstraintChecksTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE shifts (
                id INTEGER PRIMARY KEY, worker TEXT, client TEXT,
                date TEXT, time TEXT, status TEXT
            );
            CREATE TABLE shift_locks (
                shift_id INTEGER PRIMARY KEY,
                locked_at TEXT DEFAULT '', locked_by TEXT DEFAULT ''
            );
            CREATE TABLE absences (
                id INTEGER PRIMARY KEY, worker TEXT, type TEXT,
                date_from TEXT, date_to TEXT, note TEXT DEFAULT ''
            );
            CREATE TABLE leave_requests (
                id INTEGER PRIMARY KEY, worker TEXT, type TEXT,
                date_from TEXT, date_to TEXT, note TEXT,
                status TEXT, created_at TEXT, admin_note TEXT
            );
            CREATE TABLE holidays (
                id INTEGER PRIMARY KEY, date TEXT UNIQUE, name TEXT
            );
            CREATE TABLE clients (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE,
                contract_from TEXT, contract_to TEXT
            );
            CREATE TABLE workers (
                id INTEGER PRIMARY KEY, name TEXT UNIQUE,
                contract_end_date TEXT DEFAULT ''
            );
        """)
        self.conn.executemany(
            "INSERT INTO workers (name, contract_end_date) VALUES (?,?)",
            [("Aida", ""), ("Edita", ""), ("Samela", "2026-10-31")],
        )
        self.conn.executemany(
            "INSERT INTO clients (name, contract_from, contract_to) "
            "VALUES (?,?,?)",
            [
                ("Client A", "2026-01-01", "2026-12-31"),
                ("Expired Client", "2025-01-01", "2026-10-31"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def issue_codes(self, checked_proposal):
        return [issue["code"] for issue in checked_proposal["issues"]]

    def test_batch_overlap_marks_both_proposals_for_shared_worker(self):
        proposed = [
            proposal(hours="08:00-10:00", workers="Aida, Edita"),
            proposal(hours="09:30-11:00", workers="Edita", client="Client B"),
        ]
        issues = app.ap_check_overlap_batch(proposed)
        self.assertEqual(issues[0][0]["code"], "overlap_batch")
        self.assertEqual(issues[1][0]["code"], "overlap_batch")
        self.assertEqual(issues[0][0]["workers"], ["Edita"])

    def test_adjacent_times_do_not_overlap(self):
        proposed = [
            proposal(hours="08:00-10:00"),
            proposal(hours="10:00-12:00", client="Client B"),
        ]
        self.assertEqual(app.ap_check_overlap_batch(proposed), {0: [], 1: []})

    def test_existing_overlap_and_lock_are_both_blocking(self):
        self.conn.execute(
            "INSERT INTO shifts VALUES (1,?,?,?,?,?)",
            ("Aida", "Existing", "2026-11-02", "09:00-11:00", "planned"),
        )
        self.conn.execute(
            "INSERT INTO shift_locks VALUES (1,?,?)", ("now", "admin")
        )
        issues = app.ap_check_overlap_existing(
            self.conn, proposal(hours="10:30-12:00")
        )
        self.assertEqual([item["code"] for item in issues],
                         ["overlap_existing", "locked"])
        self.assertTrue(app.ap_check_shift_locked(self.conn, 1))

    def test_existing_shift_for_other_worker_is_not_a_conflict(self):
        self.conn.execute(
            "INSERT INTO shifts VALUES (1,?,?,?,?,?)",
            ("Edita", "Existing", "2026-11-02", "09:00-11:00", "planned"),
        )
        self.assertEqual(
            app.ap_check_overlap_existing(
                self.conn, proposal(hours="09:30-10:30", workers="Aida")
            ),
            [],
        )

    def test_absence_is_inclusive_and_blocks_one_team_member(self):
        self.conn.execute(
            "INSERT INTO absences VALUES (1,?,?,?,?,?)",
            ("Edita", "vacation", "2026-11-02", "2026-11-04", ""),
        )
        self.assertTrue(app.ap_check_absence(self.conn, "Edita", "2026-11-02"))
        checked = app.ap_run_checks(
            self.conn,
            [proposal(workers="Aida, Edita")],
            "2026-11-02",
        )[0]
        self.assertIn("absence", self.issue_codes(checked))
        self.assertTrue(checked["has_blocking_issues"])

    def test_only_approved_leave_is_blocking(self):
        self.conn.executemany(
            "INSERT INTO leave_requests VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (1, "Aida", "vacation", "2026-11-02", "2026-11-02", "",
                 "pending", "now", ""),
                (2, "Edita", "vacation", "2026-11-02", "2026-11-02", "",
                 "approved", "now", ""),
            ],
        )
        self.assertFalse(
            app.ap_check_leave_approved(self.conn, "Aida", "2026-11-02")
        )
        self.assertTrue(
            app.ap_check_leave_approved(self.conn, "Edita", "2026-11-02")
        )

    def test_holiday_and_client_contract_are_warnings_only(self):
        self.conn.execute(
            "INSERT INTO holidays VALUES (1,?,?)",
            ("2026-11-02", "Allerseelen"),
        )
        checked = app.ap_run_checks(
            self.conn,
            [proposal(client="Expired Client")],
            "2026-11-02",
        )[0]
        self.assertIn("holiday", self.issue_codes(checked))
        self.assertIn("contract", self.issue_codes(checked))
        self.assertFalse(checked["has_blocking_issues"])

    def test_missing_or_expired_worker_is_blocking(self):
        missing = app.ap_run_checks(
            self.conn, [proposal(workers="Unknown")], "2026-11-02"
        )[0]
        expired = app.ap_run_checks(
            self.conn, [proposal(workers="Samela")], "2026-11-02"
        )[0]
        self.assertIn("worker_missing", self.issue_codes(missing))
        self.assertIn("worker_contract", self.issue_codes(expired))
        self.assertTrue(missing["has_blocking_issues"])
        self.assertTrue(expired["has_blocking_issues"])

    def test_integration_combines_batch_overlap_and_absence_without_writes(self):
        self.conn.execute(
            "INSERT INTO absences VALUES (1,?,?,?,?,?)",
            ("Aida", "sick", "2026-11-02", "2026-11-02", ""),
        )
        proposed = [
            proposal(hours="08:00-10:00", workers="Aida, Edita"),
            proposal(hours="09:00-11:00", workers="Edita", client="Client B"),
        ]
        before = self.conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
        checked = app.ap_run_checks(self.conn, proposed, "2026-11-02")
        after = self.conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(
            set(self.issue_codes(checked[0])), {"absence", "overlap_batch"}
        )
        self.assertEqual(
            set(self.issue_codes(checked[1])), {"overlap_batch"}
        )

    def test_rechecking_does_not_duplicate_issues(self):
        self.conn.execute(
            "INSERT INTO holidays VALUES (1,?,?)",
            ("2026-11-02", "Holiday"),
        )
        first = app.ap_run_checks(
            self.conn, [proposal(issues=[
                {"code": "exceptional", "severity": "warning"}
            ])], "2026-11-02"
        )
        second = app.ap_run_checks(self.conn, first, "2026-11-02")
        self.assertEqual(self.issue_codes(second[0]).count("exceptional"), 1)
        self.assertEqual(self.issue_codes(second[0]).count("holiday"), 1)

    def test_outside_target_week_and_invalid_time_are_blocking(self):
        checked = app.ap_run_checks(
            self.conn,
            [proposal(day="2026-11-09"), proposal(hours="12:00-11:00")],
            "2026-11-02",
        )
        self.assertIn("outside_target_week", self.issue_codes(checked[0]))
        self.assertIn("invalid_data", self.issue_codes(checked[1]))


if __name__ == "__main__":
    unittest.main()
