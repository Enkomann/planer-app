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


if __name__ == "__main__":
    unittest.main()
