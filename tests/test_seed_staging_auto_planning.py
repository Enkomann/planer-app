"""Unit tests for ``tests/seed_staging_auto_planning.py``.

The tests NEVER connect to a real PostgreSQL instance. Guard logic is
exercised by passing a dict in place of ``os.environ``; data logic is
exercised against a disposable SQLite temp database wrapped so that
``%s`` placeholders behave as under psycopg2.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import seed_staging_auto_planning as seed_mod  # noqa: E402


VALID_HOST = f"{seed_mod.EXPECTED_HOST_PREFIX}.frankfurt-postgres.render.com"
VALID_URL = (
    f"postgresql://user:pw@{VALID_HOST}:5432/{seed_mod.EXPECTED_DATABASE_NAME}"
)


def _good_env(**overrides):
    base = {
        "APP_ENV": "staging",
        seed_mod.CONFIRM_ENV: seed_mod.CONFIRM_VALUE,
        "DATABASE_URL": VALID_URL,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# SQLite shim: makes the seed script's psycopg2 '%s' SQL run against sqlite3.
# ---------------------------------------------------------------------------


class _SqliteCursor:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = -1

    def execute(self, sql, params=()):
        self._cur.execute(sql.replace("%s", "?"), params)
        self.rowcount = self._cur.rowcount
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def close(self):
        self._cur.close()


class _SqliteConn:
    """Minimal wrapper exposing the subset of API the seed script uses."""

    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        self._conn.isolation_level = None
        self._conn.execute("BEGIN")

    def cursor(self):
        return _SqliteCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()
        self._conn.execute("BEGIN")

    def rollback(self):
        self._conn.rollback()
        self._conn.execute("BEGIN")

    def close(self):
        try:
            self._conn.rollback()
        except Exception:
            pass
        self._conn.close()


def _schema_sql():
    return [
        """
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            contract_signed_at TEXT DEFAULT '',
            contract_from TEXT DEFAULT '',
            contract_to TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            address TEXT DEFAULT '',
            contract_type TEXT DEFAULT '',
            contract_end_date TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker TEXT,
            client TEXT,
            date TEXT,
            time TEXT,
            status TEXT DEFAULT 'planned'
        )
        """,
    ]


def _fresh_conn(tmpdir):
    path = os.path.join(tmpdir, f"staging-stub-{os.urandom(4).hex()}.db")
    conn = _SqliteConn(path)
    cur = conn.cursor()
    for stmt in _schema_sql():
        cur.execute(stmt)
    conn.commit()
    cur.close()
    return conn, path


# ---------------------------------------------------------------------------
# Guard tests — no DB touched.
# ---------------------------------------------------------------------------


class EnvGuardTests(unittest.TestCase):
    def test_app_env_must_be_staging(self):
        with self.assertRaises(seed_mod.StagingEnvRefused) as cm:
            seed_mod.assert_staging_environment(_good_env(APP_ENV="production"))
        self.assertIn("APP_ENV", str(cm.exception))

    def test_app_env_missing_is_refused(self):
        env = _good_env()
        env.pop("APP_ENV")
        with self.assertRaises(seed_mod.StagingEnvRefused):
            seed_mod.assert_staging_environment(env)

    def test_confirm_token_required(self):
        env = _good_env()
        env[seed_mod.CONFIRM_ENV] = "wrong-token"
        with self.assertRaises(seed_mod.StagingEnvRefused) as cm:
            seed_mod.assert_staging_environment(env)
        self.assertIn(seed_mod.CONFIRM_ENV, str(cm.exception))

    def test_database_url_missing_is_refused(self):
        env = _good_env()
        env.pop("DATABASE_URL")
        with self.assertRaises(seed_mod.StagingEnvRefused) as cm:
            seed_mod.assert_staging_environment(env)
        self.assertIn("DATABASE_URL", str(cm.exception))

    def test_non_postgres_scheme_is_refused(self):
        env = _good_env(
            DATABASE_URL=f"mysql://u:p@{VALID_HOST}/{seed_mod.EXPECTED_DATABASE_NAME}"
        )
        with self.assertRaises(seed_mod.StagingEnvRefused):
            seed_mod.assert_staging_environment(env)

    def test_wrong_database_name_is_refused(self):
        env = _good_env(
            DATABASE_URL=f"postgresql://u:p@{VALID_HOST}/some_other_db"
        )
        with self.assertRaises(seed_mod.StagingEnvRefused):
            seed_mod.assert_staging_environment(env)

    def test_wrong_host_is_refused(self):
        env = _good_env(
            DATABASE_URL=(
                f"postgresql://u:p@dpg-NOTTHESAMEHOST-a.frankfurt-postgres"
                f".render.com/{seed_mod.EXPECTED_DATABASE_NAME}"
            )
        )
        with self.assertRaises(seed_mod.StagingEnvRefused) as cm:
            seed_mod.assert_staging_environment(env)
        self.assertIn("host", str(cm.exception).lower())

    def test_production_marker_in_url_is_refused(self):
        hostile = (
            f"postgresql://u:p@{seed_mod.EXPECTED_HOST_PREFIX}-planer-app.r.com"
            f"/{seed_mod.EXPECTED_DATABASE_NAME}"
        )
        env = _good_env(DATABASE_URL=hostile)
        with self.assertRaises(seed_mod.StagingEnvRefused) as cm:
            seed_mod.assert_staging_environment(env)
        self.assertIn("production", str(cm.exception).lower())

    def test_production_hash_match_is_refused(self):
        digest = hashlib.sha256(VALID_URL.encode()).hexdigest()
        env = _good_env(AUTO_PLANNING_PRODUCTION_DATABASE_SHA256=digest)
        with self.assertRaises(seed_mod.StagingEnvRefused) as cm:
            seed_mod.assert_staging_environment(env)
        self.assertIn("fingerprint", str(cm.exception).lower())

    def test_good_env_returns_parsed_values(self):
        url, host, db = seed_mod.assert_staging_environment(_good_env())
        self.assertEqual(url, VALID_URL)
        self.assertEqual(db, seed_mod.EXPECTED_DATABASE_NAME)
        self.assertTrue(host.startswith(seed_mod.EXPECTED_HOST_PREFIX))

    def test_refusal_messages_never_leak_the_url(self):
        env = _good_env(APP_ENV="production")
        try:
            seed_mod.assert_staging_environment(env)
        except seed_mod.StagingEnvRefused as exc:
            self.assertNotIn("render.com", str(exc))
            self.assertNotIn(VALID_URL, str(exc))
            self.assertNotIn("pw", str(exc))


class ConnectedDatabaseGuardTests(unittest.TestCase):
    def _fake_conn(self, returned_name):
        class _Cur:
            def __init__(self):
                self.closed = False

            def execute(self, sql):
                assert "current_database" in sql.lower()

            def fetchone(self):
                return (returned_name,) if returned_name is not None else None

            def close(self):
                self.closed = True

        class _Conn:
            def cursor(self_inner):
                return _Cur()

        return _Conn()

    def test_matching_database_is_accepted(self):
        name = seed_mod.assert_connected_database(
            self._fake_conn(seed_mod.EXPECTED_DATABASE_NAME)
        )
        self.assertEqual(name, seed_mod.EXPECTED_DATABASE_NAME)

    def test_wrong_database_is_refused(self):
        with self.assertRaises(seed_mod.StagingDatabaseMismatch):
            seed_mod.assert_connected_database(
                self._fake_conn("luxmann_planner_prod")
            )

    def test_empty_database_name_is_refused(self):
        with self.assertRaises(seed_mod.StagingDatabaseMismatch):
            seed_mod.assert_connected_database(self._fake_conn(None))


# ---------------------------------------------------------------------------
# Data tests — SQLite stub stands in for the staging PG schema.
# ---------------------------------------------------------------------------


class SeedPlanTests(unittest.TestCase):
    def test_default_plan_dates_are_four_consecutive_mondays(self):
        self.assertEqual(len(seed_mod.DEFAULT_PLAN.dates), 4)
        seed_mod.DEFAULT_PLAN.validate()  # raises on non-Monday
        self.assertEqual(seed_mod.DEFAULT_PLAN.time_range, "09:00-11:00")
        self.assertTrue(
            seed_mod.DEFAULT_PLAN.client.startswith(seed_mod.TEST_PREFIX)
        )
        self.assertTrue(
            seed_mod.DEFAULT_PLAN.worker.startswith(seed_mod.TEST_PREFIX)
        )

    def test_plan_rejects_non_monday_dates(self):
        bad_plan = seed_mod.SeedPlan(
            client=f"{seed_mod.TEST_PREFIX}X",
            worker=f"{seed_mod.TEST_PREFIX}Y",
            time_range="09:00-11:00",
            dates=("2026-09-22",),  # Tuesday
            status="planned",
        )
        with self.assertRaises(ValueError):
            bad_plan.validate()

    def test_plan_rejects_unprefixed_names(self):
        bad_plan = seed_mod.SeedPlan(
            client="unprefixed",
            worker=f"{seed_mod.TEST_PREFIX}Y",
            time_range="09:00-11:00",
            dates=seed_mod.SHIFT_DATES,
            status="planned",
        )
        with self.assertRaises(ValueError):
            bad_plan.validate()


class SeedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="staging-seed-tests-")

    def _conn(self):
        conn, _ = _fresh_conn(self._tmpdir)
        self.addCleanup(conn.close)
        return conn

    def test_seed_creates_client_worker_and_four_shifts(self):
        conn = self._conn()
        report = seed_mod.seed(conn)
        self.assertTrue(report.client_created)
        self.assertTrue(report.worker_created)
        self.assertEqual(report.shifts_created, 4)
        self.assertEqual(report.shifts_already_present, 0)
        self.assertEqual(report.verified_shift_rows, 4)

        cur = conn.cursor()
        cur.execute(
            "SELECT date, time, worker, client, status FROM shifts "
            "WHERE worker = ? AND client = ? ORDER BY date",
            (seed_mod.WORKER_NAME, seed_mod.CLIENT_NAME),
        )
        rows = cur.fetchall()
        self.assertEqual(
            [r[0] for r in rows],
            list(seed_mod.SHIFT_DATES),
        )
        for row in rows:
            self.assertEqual(row[1], "09:00-11:00")
            self.assertEqual(row[2], seed_mod.WORKER_NAME)
            self.assertEqual(row[3], seed_mod.CLIENT_NAME)
            self.assertEqual(row[4], "planned")

    def test_seed_is_idempotent(self):
        conn = self._conn()
        first = seed_mod.seed(conn)
        second = seed_mod.seed(conn)
        self.assertEqual(first.shifts_created, 4)
        self.assertEqual(second.shifts_created, 0)
        self.assertEqual(second.shifts_already_present, 4)
        self.assertFalse(second.client_created)
        self.assertFalse(second.worker_created)
        self.assertEqual(second.verified_shift_rows, 4)

        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM shifts WHERE worker = ? AND client = ?",
            (seed_mod.WORKER_NAME, seed_mod.CLIENT_NAME),
        )
        self.assertEqual(cur.fetchone()[0], 4)

    def test_dry_run_does_not_commit(self):
        conn = self._conn()
        report = seed_mod.seed(conn, dry_run=True)
        self.assertEqual(report.verified_shift_rows, 4)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM shifts")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM clients")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM workers")
        self.assertEqual(cur.fetchone()[0], 0)

    def test_seed_never_touches_non_prefixed_rows(self):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO clients (name) VALUES (?)", ("Real_Client",))
        cur.execute("INSERT INTO workers (name) VALUES (?)", ("Real_Worker",))
        cur.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Real_Worker", "Real_Client", "2026-09-21", "09:00-11:00", "planned"),
        )
        conn.commit()

        seed_mod.seed(conn)

        cur.execute(
            "SELECT COUNT(*) FROM shifts WHERE worker = ? AND client = ?",
            ("Real_Worker", "Real_Client"),
        )
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT COUNT(*) FROM clients WHERE name = ?", ("Real_Client",))
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT COUNT(*) FROM workers WHERE name = ?", ("Real_Worker",))
        self.assertEqual(cur.fetchone()[0], 1)

    def test_seed_rolls_back_on_failure(self):
        """Force an exception mid-transaction and verify nothing persists.

        We wrap the connection so the third ``shifts`` INSERT raises. The
        client row, worker row, and the first two shift INSERTs must all be
        rolled back; nothing written outside the ``APSTG_TEST_`` prefix must
        be touched either.
        """
        conn = self._conn()
        cur = conn.cursor()
        # Pre-existing legitimate row we must never damage.
        cur.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Real_Worker", "Real_Client", "2026-09-21", "08:00-09:00", "planned"),
        )
        conn.commit()

        class _FailingCursor:
            def __init__(self, inner):
                self._inner = inner
                self._insert_count = 0
                self.rowcount = -1

            def execute(self, sql, params=()):
                upper = sql.strip().upper()
                if upper.startswith("INSERT INTO SHIFTS"):
                    self._insert_count += 1
                    if self._insert_count == 3:
                        raise RuntimeError("simulated DB failure")
                self._inner.execute(sql, params)
                self.rowcount = self._inner.rowcount
                return self

            def fetchone(self):
                return self._inner.fetchone()

            def fetchall(self):
                return self._inner.fetchall()

            def close(self):
                self._inner.close()

        class _FailingConn:
            def __init__(self, inner):
                self._inner = inner

            def cursor(self_inner):
                return _FailingCursor(conn.cursor())

            def commit(self_inner):
                conn.commit()

            def rollback(self_inner):
                conn.rollback()

        failing = _FailingConn(conn)
        with self.assertRaises(RuntimeError):
            seed_mod.seed(failing)

        # Rolled back: no APSTG rows at all.
        cur.execute(
            "SELECT COUNT(*) FROM clients WHERE name = ?",
            (seed_mod.CLIENT_NAME,),
        )
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute(
            "SELECT COUNT(*) FROM workers WHERE name = ?",
            (seed_mod.WORKER_NAME,),
        )
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute(
            "SELECT COUNT(*) FROM shifts WHERE worker = ? OR client = ?",
            (seed_mod.WORKER_NAME, seed_mod.CLIENT_NAME),
        )
        self.assertEqual(cur.fetchone()[0], 0)
        # Pre-existing legitimate row untouched.
        cur.execute(
            "SELECT COUNT(*) FROM shifts WHERE worker = ? AND client = ?",
            ("Real_Worker", "Real_Client"),
        )
        self.assertEqual(cur.fetchone()[0], 1)


class CleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="staging-cleanup-tests-")

    def _conn(self):
        conn, _ = _fresh_conn(self._tmpdir)
        self.addCleanup(conn.close)
        return conn

    def test_cleanup_removes_only_prefixed_rows(self):
        conn = self._conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO clients (name) VALUES (?)", ("Real_Client",))
        cur.execute("INSERT INTO workers (name) VALUES (?)", ("Real_Worker",))
        cur.execute(
            "INSERT INTO shifts (worker, client, date, time, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Real_Worker", "Real_Client", "2026-09-21", "09:00-11:00", "planned"),
        )
        conn.commit()

        seed_mod.seed(conn)
        report = seed_mod.cleanup(conn)
        self.assertEqual(report.shifts_deleted, 4)
        self.assertEqual(report.workers_deleted, 1)
        self.assertEqual(report.clients_deleted, 1)

        cur.execute("SELECT COUNT(*) FROM clients")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT name FROM clients")
        self.assertEqual(cur.fetchone()[0], "Real_Client")

        cur.execute("SELECT COUNT(*) FROM workers")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT name FROM workers")
        self.assertEqual(cur.fetchone()[0], "Real_Worker")

        cur.execute("SELECT COUNT(*) FROM shifts")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT worker, client FROM shifts")
        self.assertEqual(cur.fetchone(), ("Real_Worker", "Real_Client"))

    def test_cleanup_dry_run_does_not_commit(self):
        conn = self._conn()
        seed_mod.seed(conn)
        report = seed_mod.cleanup(conn, dry_run=True)
        self.assertEqual(report.shifts_deleted, 4)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM shifts")
        self.assertEqual(cur.fetchone()[0], 4)
        cur.execute("SELECT COUNT(*) FROM clients")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT COUNT(*) FROM workers")
        self.assertEqual(cur.fetchone()[0], 1)


# ---------------------------------------------------------------------------
# Compatibility with the real auto-planning recurrence engine.
# ---------------------------------------------------------------------------


class RecurrenceEngineCompatibilityTests(unittest.TestCase):
    """Feed the exact rows the seed script writes into the real engine.

    This verifies that the dates/time format we insert produce a
    recurrence key the live auto-planning engine will group together.
    """

    def test_four_shifts_produce_one_weekly_recurrence_key(self):
        import importlib

        app_mod = importlib.import_module("app")

        shifts = [
            (
                1,  # id
                seed_mod.WORKER_NAME,
                seed_mod.CLIENT_NAME,
                d,
                seed_mod.SHIFT_TIME_RANGE,
                "planned",
            )
            for d in seed_mod.SHIFT_DATES
        ]
        grouped = app_mod.ap_group_by_key(shifts)
        self.assertEqual(len(grouped), 1, grouped)
        (key, occurrences), = grouped.items()
        client, _workers_sig, weekday, start, end = key
        self.assertEqual(client, seed_mod.CLIENT_NAME)
        self.assertEqual(weekday, 0)  # Monday
        self.assertEqual(start, "09:00")
        self.assertEqual(end, "11:00")
        self.assertEqual(len(occurrences), 4)
        period = app_mod.ap_period_weeks(occurrences)
        self.assertEqual(period, 1)


# ---------------------------------------------------------------------------
# CLI refuses to connect without the full guard environment.
# ---------------------------------------------------------------------------


class CliGuardTests(unittest.TestCase):
    def test_main_refuses_without_app_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                seed_mod, "_open_postgres_connection"
            ) as opener:
                with self.assertRaises(seed_mod.StagingEnvRefused):
                    seed_mod.main(["--seed"])
                opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
