import os
import shutil
import sqlite3
import tempfile
import unittest
import urllib.parse
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


class Phase4DraftRoutesTests(unittest.TestCase):
    TARGET_WEEK = "2026-11-02"
    HISTORY_DATES = [
        "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28",
        "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26",
    ]

    def setUp(self):
        conn = app.get_conn()
        for table in (
            "shift_proposals", "shift_proposal_batches", "shift_locks",
            "shifts", "absences", "leave_requests", "holidays",
            "workers", "clients",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        self.login(self.client)

    def login(self, client, username="admin", role="admin"):
        with client.session_transaction() as flask_session:
            flask_session["user"] = username
            flask_session["role"] = role
            flask_session["lang"] = "en"

    def seed_weekly_history(self, worker="Aida", client="Client A",
                            add_worker=True):
        conn = app.get_conn()
        if add_worker:
            conn.execute(
                "INSERT INTO workers (name, contract_end_date) VALUES (?, '')",
                (worker,),
            )
        conn.execute(
            "INSERT INTO clients (name, contract_from, contract_to) "
            "VALUES (?, '2026-01-01', '2026-12-31')",
            (client,),
        )
        conn.executemany(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES (?, ?, ?, '08:00-10:00', 'done')",
            [(worker, client, day) for day in self.HISTORY_DATES],
        )
        conn.commit()
        conn.close()

    def generate(self, lookback=8, include_exceptional=False):
        data = {
            "target_week_start": self.TARGET_WEEK,
            "lookback_weeks": str(lookback),
        }
        if include_exceptional:
            data["include_exceptional"] = "1"
        return self.client.post("/plan/generate", data=data)

    def batch_id_from_response(self, response):
        parsed = urllib.parse.urlparse(response.headers["Location"])
        return int(urllib.parse.parse_qs(parsed.query)["batch"][0])

    def db_rows(self, query, params=()):
        conn = app.get_conn()
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    def test_generate_target_week_creates_reviewable_draft_without_shift_writes(self):
        self.seed_weekly_history()
        before = self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "ORDER BY id"
        )

        response = self.generate(8)

        self.assertEqual(response.status_code, 302)
        batch_id = self.batch_id_from_response(response)
        batch = self.db_rows(
            "SELECT target_week_start, lookback_weeks, include_exceptional "
            "FROM shift_proposal_batches WHERE id = ?", (batch_id,),
        )[0]
        proposals = self.db_rows(
            "SELECT target_date, client, worker, time FROM shift_proposals "
            "WHERE batch_id = ?", (batch_id,),
        )
        after = self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "ORDER BY id"
        )
        self.assertEqual(batch, (self.TARGET_WEEK, 8, 0))
        self.assertEqual(
            proposals, [(self.TARGET_WEEK, "Client A", "Aida", "08:00-10:00")]
        )
        self.assertEqual(after, before)

        review = self.client.get(response.headers["Location"])
        self.assertEqual(review.status_code, 200)
        self.assertIn(b'data-proposal-id="', review.data)
        self.assertIn(b'Client A', review.data)
        self.assertIn(b'value="2026-11-02"', review.data)

    def test_history_choices_2_4_and_8_are_persisted_and_generate(self):
        self.seed_weekly_history()
        batch_ids = [
            self.batch_id_from_response(self.generate(weeks))
            for weeks in (2, 4, 8)
        ]
        rows = self.db_rows(
            "SELECT lookback_weeks FROM shift_proposal_batches "
            "WHERE id IN (?, ?, ?) ORDER BY lookback_weeks", tuple(batch_ids),
        )
        counts = self.db_rows(
            "SELECT batch_id, COUNT(*) FROM shift_proposals "
            "WHERE batch_id IN (?, ?, ?) GROUP BY batch_id", tuple(batch_ids),
        )
        self.assertEqual(rows, [(2,), (4,), (8,)])
        self.assertEqual(sorted(count for _, count in counts), [1, 1, 1])

    def test_next_week_is_the_generate_form_default(self):
        expected = app.ap_default_target_week_start().isoformat().encode()
        response = self.client.get("/plan/generate")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="' + expected + b'"', response.data)
        self.assertIn(b'value="8" checked', response.data)

    def test_permissions_restrict_generate_and_review_to_admin(self):
        anonymous = app.app.test_client()
        self.assertEqual(anonymous.get("/plan/generate").status_code, 302)
        self.assertIn("/login", anonymous.get("/plan/generate").headers["Location"])

        worker = app.app.test_client()
        self.login(worker, username="Aida", role="worker")
        self.assertEqual(worker.get("/plan/generate").status_code, 403)
        self.assertEqual(worker.post("/plan/generate").status_code, 403)
        self.assertEqual(worker.get("/plan/review?batch=1").status_code, 403)

    def test_constraint_warnings_are_stored_and_rendered(self):
        self.seed_weekly_history()
        conn = app.get_conn()
        conn.execute(
            "INSERT INTO absences (worker, type, date_from, date_to, note) "
            "VALUES ('Aida', 'vacation', ?, ?, '')",
            (self.TARGET_WEEK, self.TARGET_WEEK),
        )
        conn.commit()
        conn.close()

        response = self.generate()
        batch_id = self.batch_id_from_response(response)
        issue_text = self.db_rows(
            "SELECT issues FROM shift_proposals WHERE batch_id = ?", (batch_id,),
        )[0][0]
        self.assertIn('"code": "absence"', issue_text)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'data-issue-code="absence"', review.data)
        self.assertIn(b'Manual review required', review.data)

    def test_confidence_below_threshold_is_clearly_manual(self):
        self.seed_weekly_history()
        conn = app.get_conn()
        conn.execute(
            "DELETE FROM shifts WHERE date < '2026-10-19'"
        )
        conn.commit()
        conn.close()

        response = self.generate(8)
        batch_id = self.batch_id_from_response(response)
        confidence = self.db_rows(
            "SELECT confidence FROM shift_proposals WHERE batch_id = ?",
            (batch_id,),
        )[0][0]
        self.assertLess(confidence, app.AP_DEFAULT_MIN_CONFIDENCE)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'Manual review required', review.data)
        self.assertIn(b'25%', review.data)

    def test_removed_or_renamed_historical_worker_is_actionable(self):
        self.seed_weekly_history(worker="Former Worker", add_worker=False)
        response = self.generate()
        batch_id = self.batch_id_from_response(response)
        issue_text = self.db_rows(
            "SELECT issues FROM shift_proposals WHERE batch_id = ?", (batch_id,),
        )[0][0]
        self.assertIn('"code": "worker_missing"', issue_text)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'data-issue-code="worker_missing"', review.data)
        self.assertIn(b'Former Worker', review.data)
        self.assertIn(b'no longer exists or was renamed', review.data)

    def test_repeated_generation_reuses_same_batch_without_duplicates(self):
        self.seed_weekly_history()
        first = self.generate()
        second = self.generate()
        self.assertEqual(first.headers["Location"], second.headers["Location"])
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposal_batches")[0][0], 1
        )
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposals")[0][0], 1
        )

    def test_draft_is_private_to_user_and_originating_session(self):
        self.seed_weekly_history()
        response = self.generate()
        location = response.headers["Location"]
        self.assertEqual(self.client.get(location).status_code, 200)

        same_user_new_session = app.app.test_client()
        self.login(same_user_new_session, username="admin", role="admin")
        self.assertEqual(same_user_new_session.get(location).status_code, 404)

        other_admin = app.app.test_client()
        self.login(other_admin, username="other-admin", role="admin")
        self.assertEqual(other_admin.get(location).status_code, 404)

    def test_existing_locked_shift_is_preserved_and_reported(self):
        self.seed_weekly_history()
        conn = app.get_conn()
        cursor = conn.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES ('Aida', 'Existing Client', ?, '09:00-11:00', 'planned')",
            (self.TARGET_WEEK,),
        )
        existing_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO shift_locks (shift_id, locked_at, locked_by) "
            "VALUES (?, 'now', 'admin')", (existing_id,),
        )
        conn.commit()
        conn.close()
        before = self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "WHERE id = ?", (existing_id,),
        )[0]

        response = self.generate()

        after = self.db_rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "WHERE id = ?", (existing_id,),
        )[0]
        issue_text = self.db_rows(
            "SELECT issues FROM shift_proposals ORDER BY id DESC LIMIT 1"
        )[0][0]
        self.assertEqual(after, before)
        self.assertIn('"code": "overlap_existing"', issue_text)
        self.assertIn('"code": "locked"', issue_text)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b'data-existing-shift-id="', review.data)
        self.assertIn(b'Locked', review.data)

    def test_invalid_non_monday_target_is_rejected_without_draft(self):
        response = self.client.post("/plan/generate", data={
            "target_week_start": "2026-11-03", "lookback_weeks": "8",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.db_rows("SELECT COUNT(*) FROM shift_proposal_batches")[0][0], 0
        )


if __name__ == "__main__":
    unittest.main()
