"""Seed staging-only recurrence history for the auto-planning engine.

Deliberately not named ``test_*.py`` so a plain ``unittest discover``
run never picks it up. The companion module
``test_seed_staging_auto_planning.py`` exercises the pure logic of this
script against isolated SQLite temp databases; nothing in that test
module performs a real PostgreSQL connection.

What this script does
---------------------

Insert exactly ONE fictitious client, ONE planning-only worker, and FOUR
historical Monday shifts (same client, same worker, same time range) so
the auto-planning engine can detect a weekly recurrence and propose a
shift for the next Monday. Every created row is named with the prefix
``APSTG_TEST_`` so the cleanup path can target precisely those rows and
nothing else.

Protection layers (all enforced BEFORE opening a connection)
------------------------------------------------------------

The script refuses to run if any of the following conditions fail:

  1. ``APP_ENV`` is exactly ``staging`` (case-insensitive, stripped).
  2. ``AUTO_PLANNING_STAGING_SEED_CONFIRM`` is exactly the value
     ``STAGING_SEED_ONLY_I_UNDERSTAND``.
  3. ``DATABASE_URL`` is set and parses as PostgreSQL (``postgres``
     or ``postgresql``), with both a host and a database path.
  4. The database path (``current_database()``) is exactly
     ``luxmann_planner_staging`` — not a prefix match, not a contains
     match.
  5. The host name starts with the known staging instance id
     ``dpg-db9ptee1egvs7386h4hg-a``. A different host (even if its
     database happens to be named ``luxmann_planner_staging``) is
     rejected.
  6. Neither the host nor the database name contains any of the known
     production markers (``planer-app``, ``prod``, ``production``,
     ``luxmann_planner_prod``).
  7. After connecting, ``SELECT current_database()`` is re-read from
     the server and must again match ``luxmann_planner_staging``.
     Writes only start if (3)-(7) all agree.
  8. Optional defense-in-depth: if the operator sets
     ``AUTO_PLANNING_PRODUCTION_DATABASE_SHA256`` to the hex SHA256 of
     the full production DATABASE_URL, the script aborts when its own
     DATABASE_URL hashes to that value.

Nothing in these checks reads a Render Service secret, SMTP config,
or anything from the production service.

Side effects explicitly refused
-------------------------------

  * No email, no SMS, no geocoding, no webhook, no outbound HTTP.
  * No schema changes (no DDL at all).
  * No row touched outside the ``APSTG_TEST_`` prefix.
  * No ``DELETE`` run unless ``--cleanup`` is passed explicitly.
  * No password, secret, or DATABASE_URL ever written to stdout.
  * No automatic schedule, cron, or background thread.

CLI
---

  python tests/seed_staging_auto_planning.py --dry-run
  python tests/seed_staging_auto_planning.py --seed
  python tests/seed_staging_auto_planning.py --cleanup

All three require the full set of guard environment variables. The
default (no flag) is ``--dry-run``: refuse to write anything until the
operator opts in explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence, Tuple


# ---------------------------------------------------------------------------
# Constants — single source of truth. Change them here, nowhere else.
# ---------------------------------------------------------------------------

CONFIRM_ENV = "AUTO_PLANNING_STAGING_SEED_CONFIRM"
CONFIRM_VALUE = "STAGING_SEED_ONLY_I_UNDERSTAND"

EXPECTED_DATABASE_NAME = "luxmann_planner_staging"
EXPECTED_HOST_PREFIX = "dpg-db9ptee1egvs7386h4hg-a"

PRODUCTION_MARKERS = (
    "planer-app",
    "luxmann_planner_prod",
    "prod",
    "production",
)

TEST_PREFIX = "APSTG_TEST_"

CLIENT_NAME = f"{TEST_PREFIX}Client_A"
WORKER_NAME = f"{TEST_PREFIX}Radnik_A"
SHIFT_TIME_RANGE = "09:00-11:00"
SHIFT_DATES: Tuple[str, ...] = (
    "2026-09-21",
    "2026-09-28",
    "2026-10-05",
    "2026-10-12",
)
SHIFT_STATUS = "planned"


# ---------------------------------------------------------------------------
# Errors — distinct types so tests can assert on each guard individually.
# ---------------------------------------------------------------------------


class StagingSeedError(RuntimeError):
    """Base class for every refusal in this script."""


class StagingEnvRefused(StagingSeedError):
    """A pre-connection environment guard failed."""


class StagingDatabaseMismatch(StagingSeedError):
    """The connected database is not the expected staging database."""


# ---------------------------------------------------------------------------
# Guards — all return the parsed DATABASE_URL on success; raise otherwise.
# ---------------------------------------------------------------------------


def _refuse(message: str) -> None:
    """Raise an environment refusal.

    The message never contains the DATABASE_URL, a password, or any other
    secret value. We only hint at WHICH guard failed, not what the actual
    value was.
    """
    raise StagingEnvRefused(message)


def _database_fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _parsed_database_url(env: "os._Environ[str] | dict[str, str]") -> Tuple[
    urllib.parse.ParseResult, str, str
]:
    """Return ``(parsed, host, database_name)`` for the DATABASE_URL in env.

    Raises ``StagingEnvRefused`` on any shape problem. Does NOT leak the
    URL into the exception message.
    """
    raw = str(env.get("DATABASE_URL", "")).strip()
    if not raw:
        _refuse("DATABASE_URL is missing.")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("postgres", "postgresql"):
        _refuse("DATABASE_URL is not a PostgreSQL URL.")
    host = (parsed.hostname or "").strip().lower()
    database_name = urllib.parse.unquote((parsed.path or "").lstrip("/"))
    if not host:
        _refuse("DATABASE_URL has no host.")
    if not database_name:
        _refuse("DATABASE_URL has no database name.")
    return parsed, host, database_name


def assert_staging_environment(env=None) -> Tuple[str, str, str]:
    """Validate every pre-connection guard.

    Returns ``(database_url, host, database_name)`` on success. The URL is
    returned so the caller can hand it to psycopg2 without re-parsing;
    callers must never log it.
    """
    if env is None:
        env = os.environ

    app_env = str(env.get("APP_ENV", "")).strip().lower()
    if app_env != "staging":
        _refuse("APP_ENV must be exactly 'staging'.")

    confirm = str(env.get(CONFIRM_ENV, ""))
    if not secrets.compare_digest(confirm, CONFIRM_VALUE):
        _refuse(f"{CONFIRM_ENV} must be set to the dedicated seed token.")

    parsed, host, database_name = _parsed_database_url(env)

    if database_name != EXPECTED_DATABASE_NAME:
        _refuse(
            "DATABASE_URL does not point at the expected staging database."
        )

    if not host.startswith(EXPECTED_HOST_PREFIX):
        _refuse("DATABASE_URL host is not the expected staging host.")

    joined = f"{host}/{database_name}".lower()
    for marker in PRODUCTION_MARKERS:
        if marker in joined:
            _refuse("DATABASE_URL carries a production marker; refusing.")

    production_hash = str(
        env.get("AUTO_PLANNING_PRODUCTION_DATABASE_SHA256", "")
    ).strip().lower()
    raw = str(env.get("DATABASE_URL", "")).strip()
    if production_hash and secrets.compare_digest(
        production_hash, _database_fingerprint(raw)
    ):
        _refuse(
            "DATABASE_URL fingerprint matches the recorded production hash."
        )

    return raw, host, database_name


def assert_connected_database(conn) -> str:
    """Read ``current_database()`` from the live connection and verify it.

    This is the second, independent check: even if DATABASE_URL claimed
    to point at staging, the server-side identity must agree. Returns the
    database name on success.
    """
    cur = conn.cursor()
    try:
        cur.execute("SELECT current_database()")
        row = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass
    if not row or not row[0]:
        raise StagingDatabaseMismatch(
            "current_database() returned nothing; refusing to write."
        )
    database_name = str(row[0])
    if database_name != EXPECTED_DATABASE_NAME:
        raise StagingDatabaseMismatch(
            "current_database() does not match the expected staging DB."
        )
    return database_name


# ---------------------------------------------------------------------------
# Idempotent seeding — pure logic, DB-agnostic (SQL 'ON CONFLICT').
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedPlan:
    client: str
    worker: str
    time_range: str
    dates: Sequence[str]
    status: str

    def validate(self) -> None:
        for value in self.dates:
            parsed = date.fromisoformat(value)
            if parsed.weekday() != 0:
                raise ValueError(
                    f"seed date {value} is not a Monday (weekday 0)."
                )
        if not self.client.startswith(TEST_PREFIX):
            raise ValueError("client name must carry the test prefix.")
        if not self.worker.startswith(TEST_PREFIX):
            raise ValueError("worker name must carry the test prefix.")


DEFAULT_PLAN = SeedPlan(
    client=CLIENT_NAME,
    worker=WORKER_NAME,
    time_range=SHIFT_TIME_RANGE,
    dates=SHIFT_DATES,
    status=SHIFT_STATUS,
)


@dataclass
class SeedReport:
    client_created: bool
    worker_created: bool
    shifts_created: int
    shifts_already_present: int
    verified_shift_rows: int


def _ensure_named_row(
    cur, table: str, name: str, extra_columns: Iterable[Tuple[str, str]] = ()
) -> bool:
    """Insert a row keyed on the UNIQUE ``name`` column, idempotently.

    Returns True iff an INSERT actually happened. Uses an explicit SELECT
    rather than ``ON CONFLICT`` so the same code path runs under SQLite
    (tests) and PostgreSQL (production of this script).
    """
    cur.execute(f"SELECT 1 FROM {table} WHERE name = %s LIMIT 1", (name,))
    found = cur.fetchone()
    if found:
        return False
    columns = ["name"] + [col for col, _ in extra_columns]
    values = [name] + [val for _, val in extra_columns]
    placeholders = ", ".join(["%s"] * len(values))
    cur.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(values),
    )
    return True


def _ensure_shift(cur, plan: SeedPlan, shift_date: str) -> bool:
    cur.execute(
        "SELECT 1 FROM shifts "
        "WHERE worker = %s AND client = %s AND date = %s AND time = %s "
        "LIMIT 1",
        (plan.worker, plan.client, shift_date, plan.time_range),
    )
    if cur.fetchone():
        return False
    cur.execute(
        "INSERT INTO shifts (worker, client, date, time, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        (plan.worker, plan.client, shift_date, plan.time_range, plan.status),
    )
    return True


def seed(conn, plan: SeedPlan = DEFAULT_PLAN, dry_run: bool = False) -> SeedReport:
    """Insert the fictional seed inside a single transaction.

    On any exception the transaction is rolled back and the exception
    re-raised. On ``dry_run=True`` nothing is committed: the SELECTs still
    run so the report reflects what WOULD have been written.
    """
    plan.validate()
    cur = conn.cursor()
    created_shifts = 0
    already_present = 0
    try:
        client_created = _ensure_named_row(cur, "clients", plan.client)
        worker_created = _ensure_named_row(
            cur, "workers", plan.worker, extra_columns=(("address", ""),)
        )
        for shift_date in plan.dates:
            if _ensure_shift(cur, plan, shift_date):
                created_shifts += 1
            else:
                already_present += 1

        cur.execute(
            "SELECT COUNT(*) FROM shifts "
            "WHERE worker = %s AND client = %s AND time = %s AND date IN "
            f"({', '.join(['%s'] * len(plan.dates))})",
            (plan.worker, plan.client, plan.time_range, *plan.dates),
        )
        verified = int(cur.fetchone()[0])
        if verified != len(plan.dates):
            raise StagingSeedError(
                f"verification failed: expected {len(plan.dates)} shifts, "
                f"saw {verified}."
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return SeedReport(
        client_created=client_created,
        worker_created=worker_created,
        shifts_created=created_shifts,
        shifts_already_present=already_present,
        verified_shift_rows=verified,
    )


@dataclass
class CleanupReport:
    shifts_deleted: int
    workers_deleted: int
    clients_deleted: int


def cleanup(
    conn, plan: SeedPlan = DEFAULT_PLAN, dry_run: bool = False
) -> CleanupReport:
    """Delete ONLY rows that carry the ``APSTG_TEST_`` prefix.

    Shifts are deleted by matching ``worker`` / ``client`` text columns
    against the prefix; the worker/client lookup rows are deleted by
    exact name. Nothing else is touched. On any exception the
    transaction is rolled back.
    """
    cur = conn.cursor()
    pattern = f"{TEST_PREFIX}%"
    try:
        cur.execute(
            "DELETE FROM shifts WHERE worker LIKE %s OR client LIKE %s",
            (pattern, pattern),
        )
        shifts_deleted = cur.rowcount or 0

        cur.execute("DELETE FROM workers WHERE name LIKE %s", (pattern,))
        workers_deleted = cur.rowcount or 0

        cur.execute("DELETE FROM clients WHERE name LIKE %s", (pattern,))
        clients_deleted = cur.rowcount or 0
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return CleanupReport(
        shifts_deleted=int(shifts_deleted),
        workers_deleted=int(workers_deleted),
        clients_deleted=int(clients_deleted),
    )


# ---------------------------------------------------------------------------
# CLI — opens the real psycopg2 connection; tests never reach this path.
# ---------------------------------------------------------------------------


def _open_postgres_connection(database_url: str):
    """Open a psycopg2 connection with safe defaults.

    Isolated here so tests do not have to import psycopg2 to exercise
    the guards or the SQL plan.
    """
    import psycopg2  # noqa: WPS433 — lazy import keeps tests DB-free

    return psycopg2.connect(database_url, connect_timeout=10)


def _print_report(report) -> None:
    """Emit a non-sensitive summary to stdout.

    No URL, no secrets, no row content beyond counts and the single
    constant client/worker name (which already carry the test prefix).
    """
    if isinstance(report, SeedReport):
        print("Seed result:")
        print(f"  client created: {report.client_created}")
        print(f"  worker created: {report.worker_created}")
        print(f"  shifts created: {report.shifts_created}")
        print(f"  shifts already present: {report.shifts_already_present}")
        print(f"  verified shift rows: {report.verified_shift_rows}")
    else:
        print("Cleanup result:")
        print(f"  shifts deleted: {report.shifts_deleted}")
        print(f"  workers deleted: {report.workers_deleted}")
        print(f"  clients deleted: {report.clients_deleted}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed or clean up the APSTG_TEST_ auto-planning history on the "
            "Luxmann Planner STAGING PostgreSQL database."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Default. Read-only: run all guards and reads, rollback writes.",
    )
    mode.add_argument(
        "--seed",
        action="store_true",
        help="Perform the seed insertion (idempotent).",
    )
    mode.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete every row carrying the APSTG_TEST_ prefix.",
    )
    args = parser.parse_args(argv)

    database_url, _, _ = assert_staging_environment()

    conn = _open_postgres_connection(database_url)
    try:
        assert_connected_database(conn)
        if args.cleanup:
            report = cleanup(conn, dry_run=False)
        elif args.seed:
            report = seed(conn, dry_run=False)
        else:
            report = seed(conn, dry_run=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    _print_report(report)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except StagingSeedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)
