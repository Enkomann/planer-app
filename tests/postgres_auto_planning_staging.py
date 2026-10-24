"""Opt-in PostgreSQL integration checks for automatic planning.

This module is deliberately not named ``test_*.py``: the normal local
``unittest discover`` run must stay on its temporary SQLite database. Run
this file only against a new, disposable staging database and only after
reading AUTO_PLANNING_STAGING.md.
"""

import hashlib
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, timedelta
from unittest import mock


CONFIRM_VALUE = "STAGING_ONLY_I_UNDERSTAND"


def _abort(message):
    raise SystemExit(f"STAGING SAFETY CHECK FAILED: {message}")


def _database_fingerprint(value):
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _guarded_staging_url():
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        _abort("APP_ENV must be exactly 'staging'.")
    if os.environ.get("AUTO_PLANNING_STAGING_CONFIRM", "") != CONFIRM_VALUE:
        _abort(
            "AUTO_PLANNING_STAGING_CONFIRM must be exactly "
            f"'{CONFIRM_VALUE}'."
        )

    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        _abort("DATABASE_URL is missing.")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("postgres", "postgresql"):
        _abort("DATABASE_URL is not PostgreSQL.")
    database_name = urllib.parse.unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not database_name:
        _abort("DATABASE_URL has no host or database name.")
    marker = f"{parsed.hostname}/{database_name}".lower()
    if not any(word in marker for word in ("staging", "test")):
        _abort("the host/database name must contain 'staging' or 'test'.")

    service_name = os.environ.get("RENDER_SERVICE_NAME", "").strip().lower()
    if service_name and not any(
        word in service_name for word in ("staging", "test")
    ):
        _abort("RENDER_SERVICE_NAME is not visibly a staging service.")

    production_hash = os.environ.get(
        "AUTO_PLANNING_PRODUCTION_DATABASE_SHA256", ""
    ).strip().lower()
    if production_hash and secrets.compare_digest(
        production_hash, _database_fingerprint(value)
    ):
        _abort("the database fingerprint matches production.")
    return value, database_name


STAGING_URL, STAGING_DATABASE_NAME = _guarded_staging_url()
TEST_STORAGE = tempfile.mkdtemp(prefix="luxmann-pg-staging-")
os.environ["DATABASE_URL"] = STAGING_URL
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "postgres-staging-tests-process-only")

# The suite never sends mail or calls geocoding. Removing these variables in
# this process makes that guarantee explicit even if staging is misconfigured.
for _key in (
    "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
    "SMTP_REPLY_TO", "IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD",
    "EMAIL_ARCHIVE_BCC", "EMAIL_SCHEDULER_SECRET", "ORS_API_KEY",
):
    os.environ.pop(_key, None)

import app  # noqa: E402


if not app.USE_POSTGRES:
    _abort("app.py did not enter PostgreSQL mode.")
if not app.auto_planning_enabled():
    _abort("AUTO_PLANNING_ENABLED must be explicitly enabled for staging tests.")

app.app.config.update(TESTING=True)
RUN_ID = secrets.token_hex(4)
KEEP_FIXTURES = os.environ.get(
    "AUTO_PLANNING_STAGING_KEEP_FIXTURES", ""
).strip().lower() in ("1", "true", "yes")


def _execute(conn, query, params=()):
    return conn.cursor().execute(query, params)


def _rows(conn, query, params=()):
    return conn.cursor().execute(query, params).fetchall()


class PostgreSQLAutoPlanningStagingTests(unittest.TestCase):
    """End-to-end checks using fake, uniquely prefixed staging rows only."""

    def setUp(self):
        test_slug = re.sub(r"[^a-z0-9]+", "_", self._testMethodName.lower())
        self.prefix = f"APSTG_{RUN_ID}_{test_slug[:28]}"
        self.admin = f"{self.prefix}_admin"
        self.worker_a = f"{self.prefix}_worker_a"
        self.worker_b = f"{self.prefix}_worker_b"
        self.client_a = f"{self.prefix}_client_a"
        self.client_b = f"{self.prefix}_client_b"
        anchor = date(2035, 1, 1)
        self.target = anchor + timedelta(days=(-anchor.weekday()) % 7)
        self.target_iso = self.target.isoformat()
        self.history_dates = [
            (self.target - timedelta(weeks=weeks)).isoformat()
            for weeks in range(8, 0, -1)
        ]

        conn = app.get_conn()
        try:
            for worker_name in (self.worker_a, self.worker_b):
                _execute(
                    conn,
                    "INSERT INTO workers (name, address, contract_type, "
                    "contract_end_date) VALUES (?, 'Test only', '', '')",
                    (worker_name,),
                )
            for client_name in (self.client_a, self.client_b):
                _execute(
                    conn,
                    "INSERT INTO clients (name, address, phone, email, "
                    "contract_from, contract_to, notes) "
                    "VALUES (?, '1 Test Street', '', '', '2030-01-01', "
                    "'2040-12-31', 'Synthetic staging fixture')",
                    (client_name,),
                )
            conn.commit()
        finally:
            conn.close()

        self.client = app.app.test_client()
        self._login(self.client)

    def tearDown(self):
        if KEEP_FIXTURES:
            print(f"Kept fake staging fixture prefix: {self.prefix}")
            return
        conn = app.get_conn()
        try:
            batch_ids = [
                int(row[0]) for row in _rows(
                    conn,
                    "SELECT id FROM shift_proposal_batches "
                    "WHERE created_by = ?",
                    (self.admin,),
                )
            ]
            for batch_id in batch_ids:
                _execute(
                    conn, "DELETE FROM shift_proposals WHERE batch_id = ?",
                    (batch_id,),
                )
            for batch_id in batch_ids:
                _execute(
                    conn,
                    "DELETE FROM shift_proposal_batches WHERE id = ?",
                    (batch_id,),
                )

            shift_ids = [
                int(row[0]) for row in _rows(
                    conn,
                    "SELECT id FROM shifts WHERE client IN (?, ?)",
                    (self.client_a, self.client_b),
                )
            ]
            for shift_id in shift_ids:
                _execute(
                    conn, "DELETE FROM shift_locks WHERE shift_id = ?",
                    (shift_id,),
                )
            _execute(
                conn, "DELETE FROM shifts WHERE client IN (?, ?)",
                (self.client_a, self.client_b),
            )
            _execute(
                conn, "DELETE FROM absences WHERE worker IN (?, ?)",
                (self.worker_a, self.worker_b),
            )
            _execute(
                conn, "DELETE FROM leave_requests WHERE worker IN (?, ?)",
                (self.worker_a, self.worker_b),
            )
            _execute(
                conn, "DELETE FROM clients WHERE name IN (?, ?)",
                (self.client_a, self.client_b),
            )
            _execute(
                conn, "DELETE FROM workers WHERE name IN (?, ?)",
                (self.worker_a, self.worker_b),
            )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_STORAGE, ignore_errors=True)

    def _login(self, client, owner_token=None, csrf_token=None,
               username=None, role="admin"):
        with client.session_transaction() as flask_session:
            flask_session["user"] = username or self.admin
            flask_session["role"] = role
            flask_session["lang"] = "en"
            if owner_token is not None:
                flask_session[app.AP_DRAFT_SESSION_KEY] = owner_token
            if csrf_token is not None:
                flask_session[app.AP_CSRF_SESSION_KEY] = csrf_token

    def _csrf(self, client=None):
        client = client or self.client
        response = client.get("/plan/generate")
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as flask_session:
            return flask_session[app.AP_CSRF_SESSION_KEY]

    def _security_tokens(self):
        csrf = self._csrf()
        with self.client.session_transaction() as flask_session:
            return flask_session[app.AP_DRAFT_SESSION_KEY], csrf

    def _seed_history(self, worker=None, client=None, hours="08:00-10:00"):
        worker = worker or self.worker_a
        client = client or self.client_a
        conn = app.get_conn()
        try:
            for day in self.history_dates:
                _execute(
                    conn,
                    "INSERT INTO shifts (worker, client, date, time, status) "
                    "VALUES (?, ?, ?, ?, 'done')",
                    (worker, client, day, hours),
                )
            conn.commit()
        finally:
            conn.close()

    def _generate(self, lookback=8):
        response = self.client.post("/plan/generate", data={
            "target_week_start": self.target_iso,
            "lookback_weeks": str(lookback),
            "csrf_token": self._csrf(),
        })
        self.assertEqual(response.status_code, 302)
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(response.headers["Location"]).query
        )
        return int(query["batch"][0])

    def _proposals(self, batch_id):
        conn = app.get_conn()
        try:
            return app.ap_load_draft_proposals(conn, batch_id)
        finally:
            conn.close()

    def _proposal_form(self, proposal, action="save", **changes):
        start_time, end_time = proposal["time"].split("-", 1)
        data = {
            "csrf_token": self._csrf(),
            "action": action,
            "workers": changes.pop(
                "workers", app.split_workers(proposal["worker"])
            ),
            "client": changes.pop("client", proposal["client"]),
            "target_date": changes.pop(
                "target_date", proposal["target_date"]
            ),
            "start_time": changes.pop("start_time", start_time),
            "end_time": changes.pop("end_time", end_time),
        }
        data.update(changes)
        return data

    def _update(self, batch_id, proposal_id, action="save", **changes):
        proposal = next(
            item for item in self._proposals(batch_id)
            if item["id"] == proposal_id
        )
        return self.client.post(
            f"/plan/review/{batch_id}/proposal/{proposal_id}/update",
            data=self._proposal_form(proposal, action=action, **changes),
        )

    def _accept(self, batch_id, proposal_id, **changes):
        response = self._update(
            batch_id, proposal_id, action="accept", **changes
        )
        self.assertEqual(response.status_code, 302)

    def _apply(self, batch_id, proposal_ids, client=None, csrf=None):
        target_client = client or self.client
        return target_client.post(
            f"/plan/review/{batch_id}/apply",
            data={
                "csrf_token": csrf or self._csrf(target_client),
                "confirm_apply": "1",
                "proposal_ids": [str(value) for value in proposal_ids],
            },
        )

    def _target_shifts(self):
        conn = app.get_conn()
        try:
            return _rows(
                conn,
                "SELECT id, worker, client, date, time, status FROM shifts "
                "WHERE date >= ? AND date <= ? AND client IN (?, ?) "
                "ORDER BY id",
                (
                    self.target_iso,
                    (self.target + timedelta(days=6)).isoformat(),
                    self.client_a, self.client_b,
                ),
            )
        finally:
            conn.close()

    def _insert_target_shift(self, worker=None, client=None,
                             hours="12:00-13:00"):
        conn = app.get_conn()
        try:
            row = _execute(
                conn,
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (?, ?, ?, ?, 'planned') RETURNING id",
                (
                    worker or self.worker_b, client or self.client_b,
                    self.target_iso, hours,
                ),
            ).fetchone()
            conn.commit()
            return int(row[0])
        finally:
            conn.close()

    def test_01_schema_creation_is_idempotent(self):
        app.init_db()
        app.init_db()
        conn = app.get_conn()
        try:
            tables = {
                row[0] for row in _rows(
                    conn,
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND "
                    "table_name IN ('shift_locks', "
                    "'shift_proposal_batches', 'shift_proposals')",
                )
            }
            self.assertEqual(tables, {
                "shift_locks", "shift_proposal_batches", "shift_proposals",
            })
            columns = {
                row[0] for row in _rows(
                    conn,
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'shift_proposals'",
                )
            }
            self.assertTrue({
                "batch_id", "status", "issues", "manual_override",
                "applied_shift_id",
            }.issubset(columns))
        finally:
            conn.close()

    def test_02_generation_uses_eight_weeks_without_writing_target_shifts(self):
        self._seed_history()
        before = len(self._target_shifts())
        batch_id = self._generate(8)
        self.assertEqual(len(self._proposals(batch_id)), 1)
        self.assertEqual(len(self._target_shifts()), before)
        conn = app.get_conn()
        try:
            row = _rows(
                conn,
                "SELECT lookback_weeks FROM shift_proposal_batches "
                "WHERE id = ?", (batch_id,),
            )[0]
            self.assertEqual(row[0], 8)
        finally:
            conn.close()

    def test_03_manual_proposal_edit_stays_in_draft(self):
        self._seed_history()
        batch_id = self._generate()
        proposal = self._proposals(batch_id)[0]
        response = self._update(
            batch_id, proposal["id"], workers=[self.worker_b],
            client=self.client_b,
            target_date=(self.target + timedelta(days=1)).isoformat(),
            start_time="10:15", end_time="12:45",
        )
        self.assertEqual(response.status_code, 302)
        changed = self._proposals(batch_id)[0]
        self.assertEqual(changed["worker"], self.worker_b)
        self.assertEqual(changed["client"], self.client_b)
        self.assertEqual(changed["time"], "10:15-12:45")
        self.assertTrue(changed["manual_override"])
        self.assertEqual(self._target_shifts(), [])

    def test_04_accept_many_and_insert_only_selected_rows(self):
        self._seed_history()
        self._seed_history(self.worker_b, self.client_b, "11:00-13:00")
        batch_id = self._generate()
        proposal_ids = [item["id"] for item in self._proposals(batch_id)]
        response = self.client.post(
            f"/plan/review/{batch_id}/bulk-accept",
            data={
                "csrf_token": self._csrf(),
                "proposal_ids": [str(value) for value in proposal_ids],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(all(
            item["status"] == "accepted"
            for item in self._proposals(batch_id)
        ))
        response = self._apply(batch_id, proposal_ids)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self._target_shifts()), 2)
        self.assertTrue(all(
            item["status"] == "applied" and item["applied_shift_id"]
            for item in self._proposals(batch_id)
        ))

    def test_05_repeated_application_is_idempotent(self):
        self._seed_history()
        batch_id = self._generate()
        proposal_id = self._proposals(batch_id)[0]["id"]
        self._accept(batch_id, proposal_id)
        self.assertEqual(
            self._apply(batch_id, [proposal_id]).status_code, 302
        )
        self.assertEqual(
            self._apply(batch_id, [proposal_id]).status_code, 409
        )
        self.assertEqual(len(self._target_shifts()), 1)

    def test_06_two_simultaneous_applications_insert_once(self):
        self._seed_history()
        batch_id = self._generate()
        proposal_id = self._proposals(batch_id)[0]["id"]
        self._accept(batch_id, proposal_id)
        owner_token, csrf_token = self._security_tokens()
        clients = [app.app.test_client(), app.app.test_client()]
        for client in clients:
            self._login(
                client, owner_token=owner_token, csrf_token=csrf_token
            )
        barrier = threading.Barrier(2)

        def submit(client):
            barrier.wait(timeout=5)
            return self._apply(
                batch_id, [proposal_id], client=client, csrf=csrf_token
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(submit, clients))
        self.assertEqual(sorted(statuses), [302, 409])
        self.assertEqual(len(self._target_shifts()), 1)

    def test_07_apply_and_manual_edit_cannot_commit_inconsistently(self):
        self._seed_history()
        batch_id = self._generate()
        proposal = self._proposals(batch_id)[0]
        self._accept(batch_id, proposal["id"])
        owner_token, csrf_token = self._security_tokens()
        apply_client = app.app.test_client()
        edit_client = app.app.test_client()
        for client in (apply_client, edit_client):
            self._login(
                client, owner_token=owner_token, csrf_token=csrf_token
            )
        barrier = threading.Barrier(2)
        edit_form = self._proposal_form(
            self._proposals(batch_id)[0], workers=[self.worker_b]
        )
        edit_form["csrf_token"] = csrf_token

        def apply_request():
            barrier.wait(timeout=5)
            return apply_client.post(
                f"/plan/review/{batch_id}/apply",
                data={
                    "csrf_token": csrf_token,
                    "confirm_apply": "1",
                    "proposal_ids": [str(proposal["id"])],
                },
            ).status_code

        def edit_request():
            barrier.wait(timeout=5)
            return edit_client.post(
                f"/plan/review/{batch_id}/proposal/"
                f"{proposal['id']}/update",
                data=edit_form,
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = [
                executor.submit(apply_request),
                executor.submit(edit_request),
            ]
            statuses = [future.result(timeout=10) for future in statuses]
        self.assertEqual(sorted(statuses), [302, 409])
        target = self._target_shifts()
        current = self._proposals(batch_id)[0]
        if target:
            self.assertEqual(len(target), 1)
            self.assertEqual(current["status"], "applied")
        else:
            self.assertEqual(current["worker"], self.worker_b)
            self.assertEqual(current["status"], "proposed")

    def test_08_lock_blocks_move_and_delete_until_explicit_unlock(self):
        self._seed_history()
        batch_id = self._generate()
        shift_id = self._insert_target_shift()
        csrf = self._csrf()
        response = self.client.post(
            f"/plan/review/{batch_id}/shift/{shift_id}/lock",
            data={"csrf_token": csrf},
        )
        self.assertEqual(response.status_code, 302)
        move = self.client.post("/move_shift", data={
            "shift_id": str(shift_id),
            "date": (self.target + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(move.status_code, 409)
        self.client.post(f"/delete_shift/{shift_id}")
        self.assertEqual(len(self._target_shifts()), 1)

        response = self.client.post(
            f"/plan/review/{batch_id}/shift/{shift_id}/unlock",
            data={"csrf_token": csrf},
        )
        self.assertEqual(response.status_code, 302)
        move = self.client.post("/move_shift", data={
            "shift_id": str(shift_id),
            "date": (self.target + timedelta(days=1)).isoformat(),
        })
        self.assertEqual(move.status_code, 200)
        self.client.post(f"/delete_shift/{shift_id}")
        self.assertEqual(self._target_shifts(), [])
        conn = app.get_conn()
        try:
            self.assertEqual(
                _rows(
                    conn,
                    "SELECT shift_id FROM shift_locks WHERE shift_id = ?",
                    (shift_id,),
                ),
                [],
            )
        finally:
            conn.close()

    def test_09_failed_second_insert_rolls_back_entire_selection(self):
        self._seed_history()
        self._seed_history(self.worker_b, self.client_b, "11:00-13:00")
        batch_id = self._generate()
        proposal_ids = [item["id"] for item in self._proposals(batch_id)]
        for proposal_id in proposal_ids:
            self._accept(batch_id, proposal_id)
        original = app.ap_insert_shift_from_proposal
        calls = {"count": 0}

        def fail_second(conn, proposal):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("forced PostgreSQL rollback check")
            return original(conn, proposal)

        with mock.patch.object(
            app, "ap_insert_shift_from_proposal", side_effect=fail_second
        ):
            response = self._apply(batch_id, proposal_ids)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._target_shifts(), [])
        self.assertTrue(all(
            item["applied_shift_id"] is None
            for item in self._proposals(batch_id)
        ))

    def test_10_week_read_is_not_blocked_by_application_table_lock(self):
        self._seed_history()
        write_conn = app.get_conn()
        app.ap_lock_shift_mutations(write_conn)
        reader = app.app.test_client()
        self._login(reader)
        timed_out = False
        status = None
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                lambda: reader.get(
                    f"/week?start={self.target_iso}"
                ).status_code
            )
            try:
                status = future.result(timeout=3)
            except TimeoutError:
                timed_out = True
            finally:
                write_conn.rollback()
                write_conn.close()
            if timed_out:
                status = future.result(timeout=5)
        self.assertFalse(timed_out, "SELECT /week waited on EXCLUSIVE lock")
        self.assertEqual(status, 200)

    def test_11_permissions_and_csrf_are_enforced_on_postgres(self):
        self._seed_history()
        missing = self.client.post("/plan/generate", data={
            "target_week_start": self.target_iso,
            "lookback_weeks": "8",
        })
        self.assertEqual(missing.status_code, 400)
        invalid = self.client.post("/plan/generate", data={
            "target_week_start": self.target_iso,
            "lookback_weeks": "8",
            "csrf_token": "invalid",
        })
        self.assertEqual(invalid.status_code, 400)
        worker_client = app.app.test_client()
        self._login(
            worker_client, username=self.worker_a, role="worker",
            csrf_token="worker-token",
        )
        self.assertEqual(worker_client.get("/plan/generate").status_code, 403)
        self.assertEqual(worker_client.post(
            "/plan/generate",
            data={
                "target_week_start": self.target_iso,
                "lookback_weeks": "8",
                "csrf_token": "worker-token",
            },
        ).status_code, 403)

    def test_12_advisory_batch_lock_serializes_postgres_connections(self):
        lock_id = int(RUN_ID, 16)
        first = app.get_conn()
        app.ap_lock_batch_mutation(first, lock_id)
        started = threading.Event()

        def acquire_second():
            second = app.get_conn()
            try:
                started.set()
                begin = time.monotonic()
                app.ap_lock_batch_mutation(second, lock_id)
                elapsed = time.monotonic() - begin
                second.rollback()
                return elapsed
            finally:
                second.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(acquire_second)
            self.assertTrue(started.wait(timeout=3))
            time.sleep(0.25)
            self.assertFalse(future.done())
            first.rollback()
            first.close()
            elapsed = future.result(timeout=5)
        self.assertGreaterEqual(elapsed, 0.20)


if __name__ == "__main__":
    print(
        "Running guarded PostgreSQL auto-planning tests against database "
        f"'{STAGING_DATABASE_NAME}' with synthetic prefix APSTG_{RUN_ID}."
    )
    unittest.main(verbosity=2)
