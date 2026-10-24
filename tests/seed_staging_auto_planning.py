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
import re
import secrets
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Sequence, Tuple


# ---------------------------------------------------------------------------
# Constants — single source of truth. Change them here, nowhere else.
# ---------------------------------------------------------------------------

CONFIRM_ENV = "AUTO_PLANNING_STAGING_SEED_CONFIRM"
CONFIRM_VALUE = "STAGING_SEED_ONLY_I_UNDERSTAND"

EXPECTED_DATABASE_NAME = "luxmann_planner_staging"

# Staging PostgreSQL instance id. The accepted hostnames are either
# EXACTLY this id (Render's internal hostname shape, resolvable only
# from inside a Render service) OR this id followed by a single
# ``.<region>-postgres.render.com`` suffix. Everything else — including
# the lookalike ``dpg-db9ptee1egvs7386h4hg-a.evil.example`` and any
# ``.render.com`` host that is not Render's own PostgreSQL endpoint
# shape — is refused before a credential leaves the process. See
# ``_is_allowed_staging_host`` for the full test.
EXPECTED_HOST_PREFIX = "dpg-db9ptee1egvs7386h4hg-a"
# Format: <id>.<region>-postgres.render.com. ``<region>`` is a lowercase
# alphanumeric slug Render uses to name the region (frankfurt, oregon,
# virginia, singapore, ohio, …). We enforce the exact suffix
# ``-postgres.render.com`` and a single intermediate label; this
# cannot be reproduced on an attacker-controlled domain.
_RENDER_PG_EXTERNAL_SUFFIX = re.compile(
    r"^" + re.escape(EXPECTED_HOST_PREFIX)
    + r"\.[a-z0-9]+-postgres\.render\.com$"
)

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


def _is_allowed_staging_host(host: str) -> bool:
    """Return True iff ``host`` is one of the accepted staging names.

    Allowed:
      * the exact Render-internal hostname ``EXPECTED_HOST_PREFIX``;
      * ``EXPECTED_HOST_PREFIX`` + ``.<region>-postgres.render.com``,
        where ``<region>`` is a single lowercase alphanumeric label.
    Rejected (non-exhaustive, see tests):
      * ``dpg-...a.evil.example``;
      * ``dpg-...a.render.com.evil.example``;
      * ``evil-dpg-db9ptee1egvs7386h4hg-a.frankfurt-postgres.render.com``;
      * ``dpg-db9ptee1egvs7386h4hg-a.frankfurt-postgres.render.com.evil``.
    """
    if host == EXPECTED_HOST_PREFIX:
        return True
    return bool(_RENDER_PG_EXTERNAL_SUFFIX.match(host))


def _parsed_database_url(env: "os._Environ[str] | dict[str, str]") -> Tuple[
    urllib.parse.ParseResult, str, str
]:
    """Return ``(parsed, host, database_name)`` for the DATABASE_URL in env.

    Raises ``StagingEnvRefused`` on any shape problem. Does NOT leak the
    URL into the exception message.

    This parser is strict on the DSN shape so psycopg2 cannot be
    handed a connection target different from the one we validated:
      * scheme must be ``postgres`` / ``postgresql`` (not ``pq://`` etc);
      * query string MUST be empty — a parameter like
        ``?host=evil.example`` or ``?sslmode=disable`` in the URL
        is handled by libpq AT CONNECT TIME, so accepting it here
        would silently bypass every host-level guard above;
      * fragment must be empty — no ``#``-encoded ambiguity;
      * host and database_name must be non-empty, both go into
        the explicit kwargs we pass to :func:`psycopg2.connect`.
    """
    raw = str(env.get("DATABASE_URL", "")).strip()
    if not raw:
        _refuse("DATABASE_URL is missing.")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("postgres", "postgresql"):
        _refuse("DATABASE_URL is not a PostgreSQL URL.")
    if parsed.query:
        _refuse(
            "DATABASE_URL carries query-string parameters; refusing "
            "(a parameter like ?host=... or ?sslmode=disable would "
            "override the destination libpq connects to)."
        )
    if parsed.fragment:
        _refuse("DATABASE_URL carries a URL fragment; refused.")
    host = (parsed.hostname or "").strip().lower()
    database_name = urllib.parse.unquote((parsed.path or "").lstrip("/"))
    if not host:
        _refuse("DATABASE_URL has no host.")
    if not database_name:
        _refuse("DATABASE_URL has no database name.")
    if "/" in database_name:
        _refuse("DATABASE_URL path has more than one segment; refused.")
    try:
        port = parsed.port
    except ValueError:
        _refuse("DATABASE_URL has a non-integer port.")
    if port is not None and port != 5432:
        _refuse(
            "DATABASE_URL port is not 5432; refused (prevents a "
            "redirect-by-port to a different listener)."
        )
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

    if not _is_allowed_staging_host(host):
        _refuse("DATABASE_URL host is not an allowed staging host.")

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


@dataclass
class SeedPlanDescription:
    """What the seed WOULD do, computed from read-only SELECTs."""
    client_needs_insert: bool
    worker_needs_insert: bool
    shifts_needing_insert: Tuple[str, ...]
    shifts_already_present: Tuple[str, ...]


def _row_exists(cur, table: str, name: str) -> bool:
    cur.execute(f"SELECT 1 FROM {table} WHERE name = %s LIMIT 1", (name,))
    return cur.fetchone() is not None


def _shift_exists(cur, plan: SeedPlan, shift_date: str) -> bool:
    cur.execute(
        "SELECT 1 FROM shifts "
        "WHERE worker = %s AND client = %s AND date = %s AND time = %s "
        "LIMIT 1",
        (plan.worker, plan.client, shift_date, plan.time_range),
    )
    return cur.fetchone() is not None


def plan_seed(conn, plan: SeedPlan = DEFAULT_PLAN) -> SeedPlanDescription:
    """Compute what seed() WOULD do using read-only SELECTs.

    Issues no INSERT/UPDATE/DELETE at any point — not even rolled back —
    so PostgreSQL sequences, triggers and WAL stay quiet. Safe to call
    on a healthy production replica (though we still refuse to do so
    via the hostname guard).
    """
    plan.validate()
    cur = conn.cursor()
    try:
        client_present = _row_exists(cur, "clients", plan.client)
        worker_present = _row_exists(cur, "workers", plan.worker)
        needing: List[str] = []
        present: List[str] = []
        for shift_date in plan.dates:
            if _shift_exists(cur, plan, shift_date):
                present.append(shift_date)
            else:
                needing.append(shift_date)
    finally:
        try:
            cur.close()
        except Exception:
            pass
    # dry-run does not open or close a transaction; psycopg2's default
    # autocommit is False but SELECT alone starts no explicit write
    # transaction. We rollback() defensively in case the driver started
    # an implicit one — this is still a no-op for pure reads.
    try:
        conn.rollback()
    except Exception:
        pass
    return SeedPlanDescription(
        client_needs_insert=not client_present,
        worker_needs_insert=not worker_present,
        shifts_needing_insert=tuple(needing),
        shifts_already_present=tuple(present),
    )


def seed(conn, plan: SeedPlan = DEFAULT_PLAN, dry_run: bool = False):
    """Seed the staging DB. Writes only on ``dry_run=False``.

    ``dry_run=True`` delegates to :func:`plan_seed` and returns the
    plan description — no INSERT, UPDATE or DELETE statement is ever
    executed, so PostgreSQL sequences and ON-INSERT triggers are not
    disturbed. Live mode runs inside a single transaction; on any
    exception the transaction is rolled back before the exception
    propagates.
    """
    if dry_run:
        return plan_seed(conn, plan)

    plan.validate()
    cur = conn.cursor()
    try:
        client_created = not _row_exists(cur, "clients", plan.client)
        if client_created:
            cur.execute(
                "INSERT INTO clients (name) VALUES (%s)",
                (plan.client,),
            )
        worker_created = not _row_exists(cur, "workers", plan.worker)
        if worker_created:
            cur.execute(
                "INSERT INTO workers (name, address) VALUES (%s, %s)",
                (plan.worker, ""),
            )

        created_shifts = 0
        already_present = 0
        for shift_date in plan.dates:
            if _shift_exists(cur, plan, shift_date):
                already_present += 1
                continue
            cur.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                (plan.worker, plan.client, shift_date, plan.time_range,
                 plan.status),
            )
            created_shifts += 1

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
    locks_deleted: int = 0


@dataclass
class CleanupPlan:
    """What cleanup() WOULD do. Dry-run only."""
    shifts_to_delete: int
    locks_to_delete: int
    worker_to_delete: bool
    client_to_delete: bool


def _count_targeted_shift_ids(cur, plan: SeedPlan) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM shifts WHERE worker = %s AND client = %s",
        (plan.worker, plan.client),
    )
    return int(cur.fetchone()[0])


def _count_targeted_shift_locks(cur, plan: SeedPlan) -> int:
    """Count shift_locks rows attached to the shifts we would delete."""
    cur.execute(
        "SELECT COUNT(*) FROM shift_locks WHERE shift_id IN ("
        "  SELECT id FROM shifts WHERE worker = %s AND client = %s"
        ")",
        (plan.worker, plan.client),
    )
    return int(cur.fetchone()[0])


def plan_cleanup(conn, plan: SeedPlan = DEFAULT_PLAN) -> CleanupPlan:
    """Compute what cleanup() WOULD do. Issues no DELETE/INSERT/UPDATE."""
    cur = conn.cursor()
    try:
        shifts = _count_targeted_shift_ids(cur, plan)
        locks = _count_targeted_shift_locks(cur, plan)
        has_worker = _row_exists(cur, "workers", plan.worker)
        has_client = _row_exists(cur, "clients", plan.client)
    finally:
        try:
            cur.close()
        except Exception:
            pass
    try:
        conn.rollback()
    except Exception:
        pass
    return CleanupPlan(
        shifts_to_delete=shifts,
        locks_to_delete=locks,
        worker_to_delete=has_worker,
        client_to_delete=has_client,
    )


def cleanup(
    conn, plan: SeedPlan = DEFAULT_PLAN, dry_run: bool = False
):
    """Delete ONLY the fictional rows this script created.

    Matching uses the EXACT constant names, never ``LIKE`` with a
    user-supplied prefix. A row called ``APSTG_TEST_Client_AX`` or
    ``APSTG_TEST_`` (the bare prefix) is therefore not touched. The
    deletion order is: shift_locks → shifts → workers → clients
    (foreign-key-safe even though the schema uses text references).
    On any exception the transaction is rolled back.
    """
    if dry_run:
        return plan_cleanup(conn, plan)

    # Second-line defense: the plan accepted via the dataclass must
    # carry the test prefix. plan.validate() already checks this, but
    # we re-assert here because cleanup() is the ONLY DELETE in this
    # module.
    plan.validate()
    if plan.client != CLIENT_NAME or plan.worker != WORKER_NAME:
        raise StagingSeedError(
            "cleanup refuses a plan whose names do not match the "
            "pinned APSTG_TEST_ constants."
        )

    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM shift_locks WHERE shift_id IN ("
            "  SELECT id FROM shifts WHERE worker = %s AND client = %s"
            ")",
            (plan.worker, plan.client),
        )
        locks_deleted = cur.rowcount or 0

        cur.execute(
            "DELETE FROM shifts WHERE worker = %s AND client = %s",
            (plan.worker, plan.client),
        )
        shifts_deleted = cur.rowcount or 0

        cur.execute("DELETE FROM workers WHERE name = %s", (plan.worker,))
        workers_deleted = cur.rowcount or 0

        cur.execute("DELETE FROM clients WHERE name = %s", (plan.client,))
        clients_deleted = cur.rowcount or 0
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
    conn.commit()
    return CleanupReport(
        shifts_deleted=int(shifts_deleted),
        workers_deleted=int(workers_deleted),
        clients_deleted=int(clients_deleted),
        locks_deleted=int(locks_deleted),
    )


# ---------------------------------------------------------------------------
# CLI — opens the real psycopg2 connection; tests never reach this path.
# ---------------------------------------------------------------------------


def _open_postgres_connection(database_url: str):
    """Open a psycopg2 connection using EXPLICIT kwargs — never the raw DSN.

    The DSN was already parsed and validated by
    :func:`assert_staging_environment`; here we extract the five
    components we actually trust (host, port, dbname, user, password)
    and pass them to :func:`psycopg2.connect` as named arguments.
    psycopg2 then hands them to libpq's keyword interface, which
    cannot be reinterpreted via a ``?host=`` query-string parameter.
    ``sslmode`` is pinned to ``require`` here — the operator cannot
    weaken it from the environment.
    """
    import psycopg2  # noqa: WPS433 — lazy import keeps tests DB-free

    parsed = urllib.parse.urlparse(database_url)
    kwargs = dict(
        host=(parsed.hostname or "").strip().lower(),
        port=parsed.port or 5432,
        dbname=urllib.parse.unquote((parsed.path or "").lstrip("/")),
        sslmode="require",
        connect_timeout=10,
    )
    if parsed.username:
        kwargs["user"] = urllib.parse.unquote(parsed.username)
    if parsed.password:
        kwargs["password"] = urllib.parse.unquote(parsed.password)
    return psycopg2.connect(**kwargs)


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
    elif isinstance(report, SeedPlanDescription):
        print("Dry-run plan (no SQL writes issued):")
        print(f"  client would be inserted: {report.client_needs_insert}")
        print(f"  worker would be inserted: {report.worker_needs_insert}")
        print(
            f"  shifts needing insert: {len(report.shifts_needing_insert)}"
        )
        print(
            "  shifts already present: "
            f"{len(report.shifts_already_present)}"
        )
    elif isinstance(report, CleanupPlan):
        print("Dry-run cleanup plan (no SQL writes issued):")
        print(f"  shifts to delete: {report.shifts_to_delete}")
        print(f"  shift_locks to delete: {report.locks_to_delete}")
        print(f"  worker to delete: {report.worker_to_delete}")
        print(f"  client to delete: {report.client_to_delete}")
    else:
        print("Cleanup result:")
        print(f"  shift_locks deleted: {report.locks_deleted}")
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
