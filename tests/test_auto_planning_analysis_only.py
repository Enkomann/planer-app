"""Tests for the read-only ``AUTO_PLANNING_MODE=analysis_only`` mode.

The suite exercises both the HTTP layer (write endpoints must return
403, GET /plan/analyze computes correctly) and the write-safety
invariant (no INSERT/UPDATE/DELETE is issued through the analyze
pipeline, verified by a recording cursor). None of these tests opens
a socket; everything runs in-process against an isolated SQLite
temp database.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest import mock


TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp_analysis_only")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-analysis-only-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("APP_ENV", None)
os.environ.pop("ADMIN_BOOTSTRAP_PASSWORD", None)
os.environ.pop("ADMIN_BOOTSTRAP_USERNAME", None)
os.environ.pop("AUTO_PLANNING_MODE", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "analysis-only-tests")


import app as app_mod  # noqa: E402


TEST_ADMIN_PASSWORD = "integration-test-analysis-admin"


def tearDownModule():
    # Mirror test_admin_bootstrap.py: do NOT shutil.rmtree TEST_STORAGE —
    # other modules share this process's `app` import.
    pass


class _AnalysisOnlyBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="luxmann-analysis-fx-")

    def setUp(self):
        # One unique DB per test — Windows file locks make a shared one
        # racy, as learned in the HTTP-seed tests.
        self._db_path = os.path.join(
            self._tmp, f"planer-{os.urandom(4).hex()}.db"
        )
        self.addCleanup(
            mock.patch.object(app_mod, "SQLITE_PATH", self._db_path).stop
        )
        mock.patch.object(app_mod, "SQLITE_PATH", self._db_path).start()

        env_patch = mock.patch.dict(
            os.environ,
            {
                "ADMIN_BOOTSTRAP_PASSWORD": TEST_ADMIN_PASSWORD,
                "ADMIN_BOOTSTRAP_USERNAME": "admin",
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("APP_ENV", None)

        app_mod.init_db()

        # Enable the feature flag + analysis_only mode AT CONFIG LEVEL —
        # the parser is tested separately so we go through app.config
        # directly here.
        cfg_patch = mock.patch.dict(
            app_mod.app.config,
            {
                "AUTO_PLANNING_ENABLED": True,
                "AUTO_PLANNING_MODE": self.auto_planning_mode(),
                "TESTING": True,
            },
        )
        cfg_patch.start()
        self.addCleanup(cfg_patch.stop)
        self.client = app_mod.app.test_client()
        self._login()

    def auto_planning_mode(self):
        # Overridden in some subclasses to test the "mode OFF" path.
        return app_mod.AUTO_PLANNING_MODE_ANALYSIS_ONLY

    def _login(self):
        resp = self.client.post("/login", data={
            "username": "admin",
            "password": TEST_ADMIN_PASSWORD,
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True))

    def _logout(self):
        self.client.get("/logout")

    def _csrf_token(self):
        # Prime the session so ap_csrf_token() sets the token, then
        # read it back from the app's session interface via a /plan/analyze
        # GET (which exposes it in the HTML).
        resp = self.client.get("/plan/analyze")
        body = resp.get_data(as_text=True)
        import re
        match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"', body
        )
        return match.group(1) if match else ""

    def _seed_four_mondays(
        self,
        worker="radnik_regular",
        client="client_regular",
        time="09:00-11:00",
        status="planned",
    ):
        """Create 4 weekly recurring shifts the analyzer should detect."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO workers (name, address) VALUES (?, ?)",
                (worker, ""),
            )
            conn.execute(
                "INSERT OR IGNORE INTO clients (name, address) VALUES (?, ?)",
                (client, "Rue Test"),
            )
            for date in (
                "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12",
            ):
                conn.execute(
                    "INSERT INTO shifts "
                    "(worker, client, date, time, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (worker, client, date, time, status),
                )
            conn.commit()


# ---------------------------------------------------------------------------
# Env-var parser is a tiny function; test it directly, no HTTP needed.
# ---------------------------------------------------------------------------


class ParseAutoPlanningModeTests(unittest.TestCase):
    def test_analysis_only_is_recognised(self):
        self.assertEqual(
            app_mod.parse_auto_planning_mode("analysis_only"),
            "analysis_only",
        )

    def test_write_enabled_is_recognised(self):
        """P1: ``write_enabled`` is the only mode that re-enables writes."""
        self.assertEqual(
            app_mod.parse_auto_planning_mode("write_enabled"),
            "write_enabled",
        )

    def test_case_insensitive_and_whitespace_trimmed(self):
        self.assertEqual(
            app_mod.parse_auto_planning_mode("  Analysis_Only  "),
            "analysis_only",
        )
        self.assertEqual(
            app_mod.parse_auto_planning_mode("  WRITE_ENABLED "),
            "write_enabled",
        )

    def test_any_other_value_returns_empty_string(self):
        """P1 fail-closed: unknown/missing/garbage ⇒ ''."""
        for value in (
            "", None, "readonly", "no", "1", "true", "analyze",
            "write", "writable", "yes", "analyze_only",
        ):
            self.assertEqual(
                app_mod.parse_auto_planning_mode(value),
                "",
                f"unexpected pass for {value!r}",
            )

    def test_helpers_match_the_config_token(self):
        with mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_MODE": "analysis_only"},
        ):
            self.assertTrue(app_mod.auto_planning_analysis_only())
            self.assertFalse(app_mod.auto_planning_write_enabled())
        with mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_MODE": "write_enabled"},
        ):
            self.assertFalse(app_mod.auto_planning_analysis_only())
            self.assertTrue(app_mod.auto_planning_write_enabled())
        with mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_MODE": ""},
        ):
            self.assertFalse(app_mod.auto_planning_analysis_only())
            self.assertFalse(app_mod.auto_planning_write_enabled())

    def test_feature_active_requires_both_flag_and_mode(self):
        """P1: feature flag on + mode empty ⇒ 404 everywhere."""
        with mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_ENABLED": True, "AUTO_PLANNING_MODE": ""},
        ):
            self.assertFalse(app_mod.auto_planning_feature_active())
        with mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_ENABLED": True,
             "AUTO_PLANNING_MODE": "analysis_only"},
        ):
            self.assertTrue(app_mod.auto_planning_feature_active())
        with mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_ENABLED": True,
             "AUTO_PLANNING_MODE": "write_enabled"},
        ):
            self.assertTrue(app_mod.auto_planning_feature_active())
        with mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_ENABLED": False,
             "AUTO_PLANNING_MODE": "write_enabled"},
        ):
            self.assertFalse(app_mod.auto_planning_feature_active())


# ---------------------------------------------------------------------------
# Write endpoints are refused with 403 when analysis-only is on.
# ---------------------------------------------------------------------------


class PhaseFiveWriteGateTests(_AnalysisOnlyBase):
    def test_generate_post_is_refused(self):
        resp = self.client.post("/plan/generate", data={})
        self.assertEqual(resp.status_code, 403)

    def test_generate_get_redirects_to_analyze(self):
        resp = self.client.get("/plan/generate", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            resp.headers.get("Location", "").endswith("/plan/analyze"),
            resp.headers.get("Location"),
        )

    def test_update_proposal_post_is_refused(self):
        resp = self.client.post(
            "/plan/review/1/proposal/1/update",
            data={"action": "accept"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_bulk_accept_post_is_refused(self):
        resp = self.client.post(
            "/plan/review/1/bulk-accept", data={},
        )
        self.assertEqual(resp.status_code, 403)

    def test_apply_post_is_refused(self):
        resp = self.client.post(
            "/plan/review/1/apply", data={},
        )
        self.assertEqual(resp.status_code, 403)

    def test_cancel_post_is_refused(self):
        resp = self.client.post(
            "/plan/review/1/cancel", data={},
        )
        self.assertEqual(resp.status_code, 403)

    def test_lock_shift_post_is_refused(self):
        resp = self.client.post(
            "/plan/review/1/shift/1/lock", data={},
        )
        # The exact URL layout for lock/unlock in app.py is checked by
        # scanning the routing table; a different path still gets 403.
        # We accept 403 or 404 (if the URL is wrong) but NEVER 2xx.
        self.assertIn(resp.status_code, (403, 404))

    def test_refusal_bypasses_csrf_check(self):
        """P1: the write guard refuses BEFORE validating csrf_token so
        a token-less direct POST cannot slip through on a different
        error path (400 would be the csrf-missing symptom)."""
        resp = self.client.post(
            "/plan/review/1/apply",
            data={},  # no csrf_token
        )
        # Must be 403 (not in write mode), not 400 (csrf missing).
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# /plan/analyze happy path: GET renders form, POST renders proposals,
# no DB write occurs.
# ---------------------------------------------------------------------------


class AnalyzeRouteTests(_AnalysisOnlyBase):
    def test_get_renders_form_with_banner(self):
        resp = self.client.get("/plan/analyze")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # Banner div marker — language-independent.
        self.assertIn('class="apa-banner"', body)
        self.assertIn('name="target_week_start"', body)
        self.assertIn("csrf_token", body)

    def test_non_admin_is_refused(self):
        self._logout()
        resp = self.client.get("/plan/analyze", follow_redirects=False)
        # Not authenticated → redirect to /login.
        self.assertIn(resp.status_code, (302, 403))

    def test_post_computes_and_renders_proposals_from_real_history(self):
        """4 Monday shifts over 4 weeks → 1 weekly recurrence proposed."""
        self._seed_four_mondays()
        token = self._csrf_token()
        resp = self.client.post("/plan/analyze", data={
            "csrf_token": token,
            "target_week_start": "2026-10-19",
            "lookback_weeks": "4",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # The proposal row carries the client/worker/date/time from
        # our seed.
        self.assertIn("client_regular", body)
        self.assertIn("radnik_regular", body)
        self.assertIn("09:00-11:00", body)
        self.assertIn("2026-10-19", body)

    def test_non_monday_target_week_renders_error(self):
        token = self._csrf_token()
        resp = self.client.post("/plan/analyze", data={
            "csrf_token": token,
            "target_week_start": "2026-10-20",  # Tuesday
            "lookback_weeks": "4",
        })
        self.assertEqual(resp.status_code, 400)

    def test_post_without_csrf_is_refused(self):
        resp = self.client.post("/plan/analyze", data={
            "target_week_start": "2026-10-19",
            "lookback_weeks": "4",
        })
        self.assertEqual(resp.status_code, 400)

    def test_empty_history_produces_no_proposals_banner(self):
        token = self._csrf_token()
        resp = self.client.post("/plan/analyze", data={
            "csrf_token": token,
            "target_week_start": "2026-10-19",
            "lookback_weeks": "4",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # Language-neutral: the proposals table only renders when
        # there is at least one proposal, so its header cell for the
        # client column must be absent on an empty history.
        self.assertNotIn("client_regular", body)
        self.assertNotIn("radnik_regular", body)


# ---------------------------------------------------------------------------
# No INSERT/UPDATE/DELETE issued through the analyze path.
# ---------------------------------------------------------------------------


class AnalyzeWriteSafetyTests(_AnalysisOnlyBase):
    def test_analyze_pipeline_issues_no_write_sql(self):
        """P-RO: wrap get_conn to record every SQL verb. On a /plan/analyze
        POST we must see only SELECT (and the belt-and-braces ROLLBACK)."""
        self._seed_four_mondays()
        token = self._csrf_token()

        statements: list[str] = []

        class _VerbRecConn(sqlite3.Connection):
            def cursor(self, *a, **k):
                inner_cur = sqlite3.Connection.cursor(self, *a, **k)

                class _Cur:
                    def __init__(self_, c):
                        self_._c = c

                    def execute(self_, sql, params=()):
                        statements.append(
                            sql.strip().split()[0].upper()
                        )
                        return self_._c.execute(sql, params)

                    def fetchone(self_):
                        return self_._c.fetchone()

                    def fetchall(self_):
                        return self_._c.fetchall()

                    def close(self_):
                        self_._c.close()

                    def __getattr__(self_, name):
                        return getattr(self_._c, name)

                return _Cur(inner_cur)

            def commit(self):
                statements.append("COMMIT")
                return sqlite3.Connection.commit(self)

            def rollback(self):
                statements.append("ROLLBACK")
                return sqlite3.Connection.rollback(self)

        def _wrapped_conn():
            return sqlite3.connect(
                app_mod.SQLITE_PATH, factory=_VerbRecConn,
            )

        with mock.patch.object(app_mod, "get_conn", _wrapped_conn):
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 200)

        forbidden = {"INSERT", "UPDATE", "DELETE", "COMMIT",
                     "CREATE", "ALTER", "DROP", "TRUNCATE"}
        hit = forbidden.intersection(statements)
        self.assertFalse(
            hit, f"analyze path emitted write SQL: {hit}; log: {statements}",
        )
        # At least one SELECT must have happened (otherwise the
        # recording wrapper would not have seen the pipeline).
        self.assertIn("SELECT", statements)


# ---------------------------------------------------------------------------
# Mode OFF: /plan/analyze 404 and /plan/generate works again.
# ---------------------------------------------------------------------------


class ModeMissingBehaviourTests(_AnalysisOnlyBase):
    """P1 fail-closed: an empty / invalid mode disables ALL /plan/* routes."""

    def auto_planning_mode(self):
        return ""

    def test_analyze_route_404_when_mode_missing(self):
        resp = self.client.get("/plan/analyze")
        self.assertEqual(resp.status_code, 404)

    def test_generate_404_when_mode_missing(self):
        """P1: without a valid mode, /plan/generate is 404 — the
        historical persistent flow never re-activates on an
        unconfigured deployment."""
        resp = self.client.get("/plan/generate")
        self.assertEqual(resp.status_code, 404)

    def test_generate_post_404_when_mode_missing(self):
        resp = self.client.post("/plan/generate", data={})
        self.assertEqual(resp.status_code, 404)

    def test_review_404_when_mode_missing(self):
        resp = self.client.get("/plan/review")
        self.assertEqual(resp.status_code, 404)

    def test_phase5_write_endpoint_404_when_mode_missing(self):
        """A direct POST hits require_auto_planning_feature first → 404."""
        resp = self.client.post("/plan/review/1/apply", data={})
        self.assertEqual(resp.status_code, 404)


class WriteEnabledModeTests(_AnalysisOnlyBase):
    """When write_enabled is explicitly chosen, writes are permitted."""

    def auto_planning_mode(self):
        return app_mod.AUTO_PLANNING_MODE_WRITE_ENABLED

    def test_analyze_route_404_in_write_mode(self):
        """``/plan/analyze`` exists only in analysis_only — in write
        mode a user hits /plan/generate instead."""
        resp = self.client.get("/plan/analyze")
        self.assertEqual(resp.status_code, 404)

    def test_generate_get_returns_form_in_write_mode(self):
        resp = self.client.get("/plan/generate")
        self.assertEqual(resp.status_code, 200)


class InitDbDdlGateTests(unittest.TestCase):
    """P2: ``init_db`` only creates the auto-planning draft/lock tables
    when the mode is explicitly ``write_enabled``.
    """

    def _run_init_db_with_mode(self, mode):
        tmp = tempfile.mkdtemp(prefix="luxmann-ddl-gate-")
        db_path = os.path.join(tmp, "planer.db")
        with mock.patch.object(app_mod, "SQLITE_PATH", db_path):
            with mock.patch.dict(
                app_mod.app.config,
                {"AUTO_PLANNING_MODE": mode},
            ):
                with mock.patch.dict(
                    os.environ,
                    {"ADMIN_BOOTSTRAP_PASSWORD": "x" * 20,
                     "ADMIN_BOOTSTRAP_USERNAME": "admin"},
                    clear=False,
                ):
                    os.environ.pop("APP_ENV", None)
                    app_mod.init_db()
        return db_path

    def _tables(self, db_path):
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        return {row[0] for row in rows}

    def test_analysis_only_boot_does_not_create_auto_plan_tables(self):
        db_path = self._run_init_db_with_mode("analysis_only")
        tables = self._tables(db_path)
        self.assertNotIn("shift_locks", tables)
        self.assertNotIn("shift_proposal_batches", tables)
        self.assertNotIn("shift_proposals", tables)

    def test_write_enabled_boot_creates_auto_plan_tables(self):
        db_path = self._run_init_db_with_mode("write_enabled")
        tables = self._tables(db_path)
        self.assertIn("shift_locks", tables)
        self.assertIn("shift_proposal_batches", tables)
        self.assertIn("shift_proposals", tables)

    def test_missing_mode_boot_does_not_create_auto_plan_tables(self):
        db_path = self._run_init_db_with_mode("")
        tables = self._tables(db_path)
        self.assertNotIn("shift_locks", tables)
        self.assertNotIn("shift_proposal_batches", tables)
        self.assertNotIn("shift_proposals", tables)

    def test_pre_existing_tables_are_preserved_in_analysis_only(self):
        """Deployments that already migrated keep their tables intact."""
        # First boot in write_enabled: tables created.
        db_path = self._run_init_db_with_mode("write_enabled")
        self.assertIn("shift_locks", self._tables(db_path))
        # Second boot in analysis_only on the SAME file: must preserve.
        with mock.patch.object(app_mod, "SQLITE_PATH", db_path):
            with mock.patch.dict(
                app_mod.app.config,
                {"AUTO_PLANNING_MODE": "analysis_only"},
            ):
                app_mod.init_db()
        tables = self._tables(db_path)
        self.assertIn("shift_locks", tables)
        self.assertIn("shift_proposal_batches", tables)
        self.assertIn("shift_proposals", tables)


# ---------------------------------------------------------------------------
# Feature flag off: /plan/analyze 404 even in analysis_only mode.
# ---------------------------------------------------------------------------


class FeatureFlagOffTests(_AnalysisOnlyBase):
    def test_analyze_404_when_feature_disabled(self):
        with mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_ENABLED": False},
        ):
            resp = self.client.get("/plan/analyze")
            self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# P3 — Historical status filtering.
# ---------------------------------------------------------------------------


class HistoryStatusFilterTests(unittest.TestCase):
    def test_cancelled_rows_go_to_discarded(self):
        history = [
            (1, "w", "c", "2026-09-21", "09:00-11:00", "planned"),
            (2, "w", "c", "2026-09-28", "09:00-11:00", "cancelled"),
            (3, "w", "c", "2026-10-05", "09:00-11:00", "done"),
            (4, "w", "c", "2026-10-12", "09:00-11:00", "in_progress"),
        ]
        learning, discarded, unknown = (
            app_mod.ap_filter_history_for_analysis(history)
        )
        self.assertEqual([row[0] for row in learning], [1, 3, 4])
        self.assertEqual([row[0] for row in discarded], [2])
        self.assertEqual(unknown, [])

    def test_unknown_status_goes_to_unknown_bucket(self):
        history = [
            (1, "w", "c", "2026-09-21", "09:00-11:00", "weird_status"),
            (2, "w", "c", "2026-09-28", "09:00-11:00", ""),
            (3, "w", "c", "2026-10-05", "09:00-11:00", None),
            (4, "w", "c", "2026-10-12", "09:00-11:00", "planned"),
        ]
        learning, discarded, unknown = (
            app_mod.ap_filter_history_for_analysis(history)
        )
        self.assertEqual([row[0] for row in learning], [4])
        self.assertEqual(discarded, [])
        self.assertEqual([row[0] for row in unknown], [1, 2, 3])

    def test_four_cancelled_rows_produce_no_proposal(self):
        """P3: a recurrence rooted entirely in cancelled rows must not
        yield a 100% confident proposal."""
        history = [
            (i, "w", "c", d, "09:00-11:00", "cancelled")
            for i, d in enumerate((
                "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12",
            ), start=1)
        ]
        learning, _, _ = app_mod.ap_filter_history_for_analysis(history)
        groups = app_mod.ap_group_by_key(learning)
        self.assertEqual(groups, {})


# ---------------------------------------------------------------------------
# P4 — Client supprimé.
# ---------------------------------------------------------------------------


class ClientMissingTests(unittest.TestCase):
    def test_proposal_for_a_deleted_client_gets_client_missing_issue(self):
        proposals = [{
            "target_date": "2026-10-19",
            "time": "09:00-11:00",
            "worker": "radnik",
            "client": "deleted_client",
            "issues": [],
            "has_blocking_issues": False,
        }]
        app_mod.ap_decorate_analysis_issues(
            proposals, known_clients={"other"}, holiday_lookup={},
        )
        codes = {i["code"] for i in proposals[0]["issues"]}
        self.assertIn("client_missing", codes)
        self.assertTrue(proposals[0]["has_blocking_issues"])

    def test_proposal_for_known_client_has_no_client_missing(self):
        proposals = [{
            "target_date": "2026-10-19",
            "time": "09:00-11:00",
            "worker": "radnik",
            "client": "known_client",
            "issues": [],
            "has_blocking_issues": False,
        }]
        app_mod.ap_decorate_analysis_issues(
            proposals, known_clients={"known_client"}, holiday_lookup={},
        )
        codes = {i["code"] for i in proposals[0]["issues"]}
        self.assertNotIn("client_missing", codes)


# ---------------------------------------------------------------------------
# P5 — Legal Luxembourg holidays.
# ---------------------------------------------------------------------------


class LegalHolidayTests(unittest.TestCase):
    def test_legal_lu_holiday_is_flagged_even_without_db_entry(self):
        """2026-05-01 (May Day) is a legal LU holiday."""
        proposals = [{
            "target_date": "2026-05-01",
            "time": "09:00-11:00",
            "worker": "radnik",
            "client": "c",
            "issues": [],
            "has_blocking_issues": False,
        }]
        # Build the lookup via the same business function the main
        # calendar uses, pointed at an empty custom-holidays DB.
        tmp = tempfile.mkdtemp(prefix="luxmann-hol-")
        db_path = os.path.join(tmp, "planer.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE holidays (date TEXT PRIMARY KEY, name TEXT)"
            )
        with sqlite3.connect(db_path) as conn:
            holiday_lookup = app_mod.get_all_holidays(conn, {2026})
        app_mod.ap_decorate_analysis_issues(
            proposals, known_clients={"c"}, holiday_lookup=holiday_lookup,
        )
        codes = {i["code"] for i in proposals[0]["issues"]}
        self.assertIn("public_holiday", codes)


# ---------------------------------------------------------------------------
# P6 — Recurrence period labels in each language.
# ---------------------------------------------------------------------------


class RecurrenceLabelTests(unittest.TestCase):
    def _tr_for(self, lang):
        return app_mod.TRANSLATIONS[lang]

    def test_weekly_label_in_fr(self):
        self.assertEqual(
            app_mod._ap_recurrence_label(1, self._tr_for("fr")),
            "Chaque semaine",
        )

    def test_biweekly_label_in_fr(self):
        self.assertEqual(
            app_mod._ap_recurrence_label(2, self._tr_for("fr")),
            "Toutes les 2 semaines",
        )

    def test_monthly_label_in_en(self):
        self.assertEqual(
            app_mod._ap_recurrence_label(4, self._tr_for("en")),
            "Monthly",
        )

    def test_exceptional_label_in_de(self):
        self.assertEqual(
            app_mod._ap_recurrence_label(0, self._tr_for("de")),
            "Unregelmaessig",
        )

    def test_each_language_has_the_four_period_keys(self):
        required = (
            "auto_plan_period_weekly",
            "auto_plan_period_biweekly",
            "auto_plan_period_monthly",
            "auto_plan_period_exceptional",
            "auto_plan_analysis_banner",
            "auto_plan_write_refused",
            "auto_plan_issue_client_missing",
            "auto_plan_issue_public_holiday",
            "auto_plan_cancelled_discarded",
        )
        for lang in ("bos", "fr", "en", "de", "pt"):
            for key in required:
                self.assertIn(
                    key, app_mod.TRANSLATIONS[lang],
                    f"{key!r} missing in {lang!r}",
                )


# ---------------------------------------------------------------------------
# P1 — Legacy routes stay compatible when ``shift_locks`` is absent.
# ---------------------------------------------------------------------------


class LegacyRoutesWithoutShiftLocksTests(unittest.TestCase):
    """Reproduce the main-branch schema (no auto-planning tables) and
    confirm /week, /move_shift, /edit_shift and /delete_shift still work
    when the feature flag is off AND when it is in analysis_only mode.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="luxmann-main-schema-")

    def setUp(self):
        self._db_path = os.path.join(
            self._tmp, f"planer-{os.urandom(4).hex()}.db"
        )
        self.addCleanup(
            mock.patch.object(app_mod, "SQLITE_PATH", self._db_path).stop
        )
        mock.patch.object(app_mod, "SQLITE_PATH", self._db_path).start()

        env_patch = mock.patch.dict(
            os.environ,
            {
                "ADMIN_BOOTSTRAP_PASSWORD": TEST_ADMIN_PASSWORD,
                "ADMIN_BOOTSTRAP_USERNAME": "admin",
            },
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("APP_ENV", None)

        # First boot WITHOUT the auto-planning mode → init_db skips the
        # auto-planning DDL, so the resulting DB mirrors a legacy
        # production schema that never ran the auto-planning migration.
        cfg_patch_bootstrap = mock.patch.dict(
            app_mod.app.config, {"AUTO_PLANNING_MODE": ""},
        )
        cfg_patch_bootstrap.start()
        app_mod.init_db()
        cfg_patch_bootstrap.stop()

        # Confirm the "fresh main schema" preconditions.
        with sqlite3.connect(self._db_path) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertNotIn("shift_locks", tables)
        self.assertNotIn("shift_proposal_batches", tables)
        self.assertNotIn("shift_proposals", tables)

        # Then put the server in the mode under test and open a client.
        cfg_patch_mode = mock.patch.dict(
            app_mod.app.config,
            {
                "AUTO_PLANNING_ENABLED": self._enabled(),
                "AUTO_PLANNING_MODE": self._mode(),
                "TESTING": True,
            },
        )
        cfg_patch_mode.start()
        self.addCleanup(cfg_patch_mode.stop)
        self.client = app_mod.app.test_client()
        self._login()
        self._seed_shift()

    def _enabled(self):
        return False

    def _mode(self):
        return ""

    def _login(self):
        resp = self.client.post("/login", data={
            "username": "admin",
            "password": TEST_ADMIN_PASSWORD,
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def _seed_shift(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO workers (name, address) VALUES (?, ?)",
                ("w1", ""),
            )
            conn.execute(
                "INSERT INTO clients (name, address) VALUES (?, ?)",
                ("c1", "Rue"),
            )
            conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (?, ?, ?, ?, ?)",
                ("w1", "c1", "2026-10-19", "09:00-11:00", "planned"),
            )
            conn.commit()

    def _shift_id(self):
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute(
                "SELECT id FROM shifts WHERE worker = 'w1' LIMIT 1",
            ).fetchone()[0]

    # ------------------------------------------------------------------

    def test_week_view_works(self):
        resp = self.client.get("/week")
        self.assertEqual(resp.status_code, 200)

    def test_move_shift_works(self):
        resp = self.client.post("/move_shift", data={
            "shift_id": str(self._shift_id()),
            "date": "2026-10-20",
        })
        self.assertEqual(resp.status_code, 200)

    def test_delete_shift_works(self):
        resp = self.client.post(
            f"/delete_shift/{self._shift_id()}",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with sqlite3.connect(self._db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM shifts",
                ).fetchone()[0],
                0,
            )

    def test_edit_shift_get_works(self):
        resp = self.client.get(f"/edit_shift/{self._shift_id()}")
        self.assertEqual(resp.status_code, 200)

    def test_no_auto_plan_ddl_was_executed_by_the_requests(self):
        self.client.get("/week")
        self.client.post("/move_shift", data={
            "shift_id": str(self._shift_id()),
            "date": "2026-10-20",
        })
        with sqlite3.connect(self._db_path) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertNotIn("shift_locks", tables)
        self.assertNotIn("shift_proposal_batches", tables)
        self.assertNotIn("shift_proposals", tables)


class LegacyRoutesWithoutShiftLocksAnalysisOnlyTests(
    LegacyRoutesWithoutShiftLocksTests
):
    """Same scenario, feature flag ON + mode analysis_only."""

    def _enabled(self):
        return True

    def _mode(self):
        return app_mod.AUTO_PLANNING_MODE_ANALYSIS_ONLY


class PreExistingLockRespectedTests(unittest.TestCase):
    """P1 defense: an existing lock row must still block a mutation."""

    def test_locked_shift_blocks_delete(self):
        tmp = tempfile.mkdtemp(prefix="luxmann-lock-respected-")
        db_path = os.path.join(tmp, "planer.db")
        with mock.patch.object(app_mod, "SQLITE_PATH", db_path):
            with mock.patch.dict(
                app_mod.app.config,
                {"AUTO_PLANNING_MODE": "write_enabled",
                 "AUTO_PLANNING_ENABLED": True,
                 "TESTING": True},
            ):
                with mock.patch.dict(
                    os.environ,
                    {"ADMIN_BOOTSTRAP_PASSWORD": TEST_ADMIN_PASSWORD,
                     "ADMIN_BOOTSTRAP_USERNAME": "admin"},
                    clear=False,
                ):
                    os.environ.pop("APP_ENV", None)
                    app_mod.init_db()
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        "INSERT INTO shifts "
                        "(worker, client, date, time, status) "
                        "VALUES ('w','c','2026-10-19','09:00-11:00','planned')"
                    )
                    conn.commit()
                    shift_id = conn.execute(
                        "SELECT id FROM shifts"
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO shift_locks "
                        "(shift_id, locked_at, locked_by) "
                        "VALUES (?, '2026-10-19T09:00', 'admin')",
                        (shift_id,),
                    )
                    conn.commit()
                client = app_mod.app.test_client()
                client.post("/login", data={
                    "username": "admin",
                    "password": TEST_ADMIN_PASSWORD,
                })
                resp = client.post(
                    f"/delete_shift/{shift_id}", follow_redirects=False,
                )
                # Redirect back with a flash — the shift must survive.
                self.assertEqual(resp.status_code, 302)
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM shifts",
                        ).fetchone()[0], 1,
                    )


# ---------------------------------------------------------------------------
# P2 — PG READ ONLY must fail-closed.
# ---------------------------------------------------------------------------


class ReadOnlyTransactionFailClosedTests(unittest.TestCase):
    def test_unknown_connection_type_raises_fail_closed(self):
        """Codex scenario D: an unknown connection class must NOT be
        treated as 'application-level discipline is enough' — raise."""

        class _MysteryConn:
            def cursor(self_inner):
                raise AssertionError("cursor must not be opened")

        with self.assertRaises(app_mod.ReadOnlyTransactionUnavailable):
            app_mod._ap_begin_readonly_tx(_MysteryConn())

    def test_pg_set_transaction_read_only_failure_raises(self):
        """A psycopg2-like connection that errors on SET TRANSACTION
        READ ONLY must raise ``ReadOnlyTransactionUnavailable``. The
        wrapper subclasses the real ``_PgConn`` so isinstance accepts
        it as PostgreSQL."""

        class _FailingCursor:
            def execute(self_inner, sql):
                raise RuntimeError("simulated PG failure")

            def close(self_inner):
                pass

        class _TestPgConn(app_mod._PgConn):
            def __init__(self_inner):
                pass  # bypass real __init__ — no real socket is opened

            def cursor(self_inner):
                return _FailingCursor()

            def rollback(self_inner):
                pass

        with self.assertRaises(app_mod.ReadOnlyTransactionUnavailable):
            app_mod._ap_begin_readonly_tx(_TestPgConn())

    def test_sqlite_connection_is_a_no_op(self):
        """SQLite does not support SET TRANSACTION READ ONLY; the
        helper must silently skip rather than raise."""
        tmp = tempfile.mkdtemp(prefix="luxmann-ro-sqlite-")
        db_path = os.path.join(tmp, "x.db")
        conn = sqlite3.connect(db_path)
        try:
            app_mod._ap_begin_readonly_tx(conn)  # must not raise
        finally:
            conn.close()


class AnalyzeAbortsOnReadOnlyFailureTests(_AnalysisOnlyBase):
    """End-to-end: when SET TRANSACTION READ ONLY fails, the analyze
    POST must return 503 without reaching the engine."""

    def test_engine_is_not_called_when_readonly_fails(self):
        token = self._csrf_token()
        self._seed_four_mondays()

        engine_called = []
        real_engine = app_mod.ap_generate_recurrence_proposals

        def _spy_engine(*args, **kwargs):
            engine_called.append(args)
            return real_engine(*args, **kwargs)

        def _raising_readonly(conn):
            raise app_mod.ReadOnlyTransactionUnavailable("test")

        with mock.patch.object(
            app_mod, "ap_generate_recurrence_proposals", _spy_engine,
        ), mock.patch.object(
            app_mod, "_ap_begin_readonly_tx", _raising_readonly,
        ):
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(engine_called, [])


# ---------------------------------------------------------------------------
# P3 — /plan/review refuses access in analysis_only.
# ---------------------------------------------------------------------------


class ReviewRouteBlockedInAnalysisOnlyTests(_AnalysisOnlyBase):
    def test_review_get_redirects_to_analyze_in_analysis_only(self):
        resp = self.client.get("/plan/review", follow_redirects=False)
        # In analysis_only we redirect to the pure read page.
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            resp.headers.get("Location", "").endswith("/plan/analyze")
        )

    def test_review_get_with_batch_id_also_redirects(self):
        resp = self.client.get(
            "/plan/review?batch=42", follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            resp.headers.get("Location", "").endswith("/plan/analyze")
        )

    def test_review_write_post_is_403(self):
        """Phase-5 writes are still 403 — the review redirect does not
        slip a write path through."""
        resp = self.client.post("/plan/review/1/apply", data={})
        self.assertEqual(resp.status_code, 403)


class ReviewRouteMissingModeIs404Tests(_AnalysisOnlyBase):
    def auto_planning_mode(self):
        return ""

    def test_review_get_404_when_mode_missing(self):
        resp = self.client.get("/plan/review")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# P4 — Menu entry hidden on misconfigured deployments.
# ---------------------------------------------------------------------------


class MenuEntryVisibilityTests(_AnalysisOnlyBase):
    """The quick-action entry is bound to ``auto_planning_feature_active``
    and ``auto_plan_entry_url``, not the raw ``auto_planning_enabled`` flag.
    """

    def test_entry_url_in_analysis_only_points_to_analyze(self):
        with app_mod.app.test_request_context("/"):
            ctx = next(iter(
                app_mod.app.template_context_processors[None]
            ))()
        # Scan every registered processor; one of them exposes the var.
        with app_mod.app.test_request_context("/"):
            merged = {}
            for proc in app_mod.app.template_context_processors[None]:
                merged.update(proc())
            self.assertTrue(merged["auto_planning_feature_active"])
            self.assertEqual(
                merged["auto_plan_entry_url"], "/plan/analyze",
            )


class MenuEntryHiddenWhenModeMissingTests(_AnalysisOnlyBase):
    def auto_planning_mode(self):
        return ""

    def test_entry_url_empty_when_feature_inactive(self):
        with app_mod.app.test_request_context("/"):
            merged = {}
            for proc in app_mod.app.template_context_processors[None]:
                merged.update(proc())
            self.assertFalse(merged["auto_planning_feature_active"])
            self.assertEqual(merged["auto_plan_entry_url"], "")


class MenuEntryWriteEnabledPointsToGenerateTests(_AnalysisOnlyBase):
    def auto_planning_mode(self):
        return app_mod.AUTO_PLANNING_MODE_WRITE_ENABLED

    def test_entry_url_in_write_enabled_points_to_generate(self):
        with app_mod.app.test_request_context("/"):
            merged = {}
            for proc in app_mod.app.template_context_processors[None]:
                merged.update(proc())
            self.assertTrue(merged["auto_planning_feature_active"])
            self.assertEqual(
                merged["auto_plan_entry_url"], "/plan/generate",
            )


# ---------------------------------------------------------------------------
# Codex regression tests: _shift_locks_table_exists must not disturb the
# ambient transaction (A/B/C/H), unknown connections (D) covered above,
# write_enabled refusal (G), and the SQL-only analysis invariant (I).
# ---------------------------------------------------------------------------


class _RecordingSqliteConnection(sqlite3.Connection):
    """Real ``sqlite3.Connection`` subclass that records every TX event
    and every cursor's first-word SQL verb.

    Using a real subclass is required: ``_shift_locks_table_exists``
    now dispatches via ``isinstance(conn, sqlite3.Connection)`` — a
    bare mock gets refused.
    """

    def _init_recorder(self):
        self._events: list[tuple[str, str]] = []

    def cursor(self, *args, **kwargs):
        real = sqlite3.Connection.cursor(self, *args, **kwargs)
        events = self._events

        class _RecCur:
            def __init__(self_, inner):
                self_._inner = inner

            def execute(self_, sql, params=()):
                events.append(("sql", sql.strip().split()[0].upper()))
                return self_._inner.execute(sql, params)

            def fetchone(self_):
                return self_._inner.fetchone()

            def fetchall(self_):
                return self_._inner.fetchall()

            def close(self_):
                self_._inner.close()

        return _RecCur(real)

    def commit(self):
        self._events.append(("tx", "COMMIT"))
        return sqlite3.Connection.commit(self)

    def rollback(self):
        self._events.append(("tx", "ROLLBACK"))
        return sqlite3.Connection.rollback(self)


def _open_recording_sqlite(db_path):
    conn = sqlite3.connect(db_path, factory=_RecordingSqliteConnection)
    conn._init_recorder()
    return conn


class ShiftLocksDetectionNoRollbackTests(unittest.TestCase):
    """A/B/C/H: metadata-based detection — zero COMMIT/ROLLBACK."""

    def test_absent_table_detection_issues_no_rollback_or_commit(self):
        """A: on a fresh schema without shift_locks, the helper queries
        sqlite_master only and never touches transaction state."""
        tmp = tempfile.mkdtemp(prefix="luxmann-sl-norollback-")
        db_path = os.path.join(tmp, "x.db")
        inner = _open_recording_sqlite(db_path)
        try:
            inner.execute("CREATE TABLE shifts (id INTEGER)")
            inner.commit()
            inner._events.clear()  # ignore setup noise
            self.assertFalse(app_mod._shift_locks_table_exists(inner))
            tx_events = [e for e in inner._events if e[0] == "tx"]
            self.assertEqual(tx_events, [], inner._events)
        finally:
            inner.close()

    def test_present_table_detection_issues_no_rollback_or_commit(self):
        tmp = tempfile.mkdtemp(prefix="luxmann-sl-present-")
        db_path = os.path.join(tmp, "x.db")
        inner = _open_recording_sqlite(db_path)
        try:
            inner.execute(
                "CREATE TABLE shift_locks (shift_id INTEGER PRIMARY KEY)"
            )
            inner.commit()
            inner._events.clear()
            self.assertTrue(app_mod._shift_locks_table_exists(inner))
            tx_events = [e for e in inner._events if e[0] == "tx"]
            self.assertEqual(tx_events, [])
        finally:
            inner.close()

    def test_pg_detection_uses_to_regclass_not_a_probe_select(self):
        """B: the PG path issues ``SELECT to_regclass('shift_locks')``
        — a metadata query that returns NULL on absence."""
        emitted: list[str] = []

        class _Cur:
            def execute(self_, sql):
                emitted.append(sql.strip())
                if "to_regclass" not in sql:
                    raise AssertionError(
                        "PG path must query to_regclass, not probe"
                    )

            def fetchone(self_):
                return (None,)

            def close(self_):
                pass

        # Must subclass the real wrapper so isinstance accepts it.
        class _TestPgConn(app_mod._PgConn):
            def __init__(self_):
                pass  # bypass the real __init__

            def cursor(self_):
                return _Cur()

            def rollback(self_):
                raise AssertionError("rollback must not be called")

            def commit(self_):
                raise AssertionError("commit must not be called")

        self.assertFalse(app_mod._shift_locks_table_exists(_TestPgConn()))
        self.assertEqual(len(emitted), 1)
        self.assertIn("to_regclass", emitted[0])
        self.assertIn("shift_locks", emitted[0])

    def test_pg_detection_treats_non_null_regclass_as_present(self):
        class _Cur:
            def execute(self_, sql):
                pass

            def fetchone(self_):
                return ("public.shift_locks",)

            def close(self_):
                pass

        class _TestPgConn(app_mod._PgConn):
            def __init__(self_):
                pass

            def cursor(self_):
                return _Cur()

        self.assertTrue(app_mod._shift_locks_table_exists(_TestPgConn()))

    def test_unexpected_permission_error_propagates(self):
        """H: an OperationalError that is NOT 'table missing' propagates."""

        class _FailingCur:
            def execute(self_, sql, params=()):
                raise sqlite3.OperationalError("database is locked")

            def close(self_):
                pass

        class _FailingSqliteConn(sqlite3.Connection):
            def cursor(self_, *a, **k):
                return _FailingCur()

            def rollback(self_):
                raise AssertionError("must not rollback on unknown error")

        tmp = tempfile.mkdtemp(prefix="luxmann-sl-fail-")
        db_path = os.path.join(tmp, "x.db")
        conn = sqlite3.connect(db_path, factory=_FailingSqliteConn)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                app_mod._shift_locks_table_exists(conn)
        finally:
            conn.close()

    def test_unknown_connection_type_is_refused(self):
        """New P1 scope: a bare mock that is neither _PgConn nor
        sqlite3.Connection must be refused outright."""

        class _MysteryConn:
            def cursor(self_):
                raise AssertionError("cursor must not be opened")

        with self.assertRaises(app_mod.ShiftLocksUnavailable):
            app_mod._shift_locks_table_exists(_MysteryConn())


class LockTableSurvivesDetectionTests(unittest.TestCase):
    """C: a transactional lock already acquired before the detection
    MUST still be in force after the detection returns.

    SQLite has no ``LOCK TABLE`` statement, but ``BEGIN IMMEDIATE``
    gives it an equivalent write-reservation. We prove the write
    reservation survives by observing that a second connection
    cannot acquire it while the first still holds it after the
    detection call.
    """

    def test_begin_immediate_reservation_survives_detection(self):
        tmp = tempfile.mkdtemp(prefix="luxmann-sl-lock-")
        db_path = os.path.join(tmp, "x.db")
        setup = sqlite3.connect(db_path)
        setup.execute("CREATE TABLE shifts (id INTEGER)")
        setup.commit()
        setup.close()

        holder = sqlite3.connect(db_path, isolation_level=None, timeout=0.2)
        holder.execute("BEGIN IMMEDIATE")
        try:
            # Detection on a schema without ``shift_locks``.
            self.assertFalse(app_mod._shift_locks_table_exists(holder))

            # Prove the write reservation is still held: a second
            # connection trying BEGIN IMMEDIATE must time out.
            other = sqlite3.connect(db_path, isolation_level=None, timeout=0.1)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    other.execute("BEGIN IMMEDIATE")
            finally:
                other.close()
        finally:
            holder.rollback()
            holder.close()


class WriteEnabledMissingLocksRespondsWithControlledErrorTests(unittest.TestCase):
    """G: in write_enabled mode without the ``shift_locks`` table, each
    write route returns a controlled 503 (or redirect-with-flash) and
    leaves the shift intact. Never a 500."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="luxmann-sl-refuse-")
        self._db_path = os.path.join(self._tmp, "planer.db")
        self.addCleanup(
            mock.patch.object(app_mod, "SQLITE_PATH", self._db_path).stop
        )
        mock.patch.object(app_mod, "SQLITE_PATH", self._db_path).start()
        env_patch = mock.patch.dict(
            os.environ,
            {"ADMIN_BOOTSTRAP_PASSWORD": TEST_ADMIN_PASSWORD,
             "ADMIN_BOOTSTRAP_USERNAME": "admin"},
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("APP_ENV", None)
        with mock.patch.dict(app_mod.app.config, {"AUTO_PLANNING_MODE": ""}):
            app_mod.init_db()  # legacy schema: no shift_locks
        cfg_patch = mock.patch.dict(
            app_mod.app.config,
            {"AUTO_PLANNING_ENABLED": True,
             "AUTO_PLANNING_MODE": "write_enabled",
             "TESTING": True},
        )
        cfg_patch.start()
        self.addCleanup(cfg_patch.stop)
        self.client = app_mod.app.test_client()
        self.client.post("/login", data={
            "username": "admin", "password": TEST_ADMIN_PASSWORD,
        })
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES ('w','c','2026-10-19','09:00-11:00','planned')"
            )
            conn.commit()

    def _shift_id(self):
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute("SELECT id FROM shifts").fetchone()[0]

    def test_move_shift_returns_503_and_leaves_shift_alone(self):
        resp = self.client.post("/move_shift", data={
            "shift_id": str(self._shift_id()),
            "date": "2026-10-26",
        })
        self.assertEqual(resp.status_code, 503)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT date FROM shifts WHERE id = ?",
                (self._shift_id(),),
            ).fetchone()
        self.assertEqual(row[0], "2026-10-19")

    def test_edit_shift_post_returns_503_and_leaves_shift_alone(self):
        resp = self.client.post(f"/edit_shift/{self._shift_id()}", data={
            "workers": "w",
            "client": "c",
            "date": "2026-10-26",
            "start_hour": "08", "start_minute": "00",
            "end_hour": "10", "end_minute": "00",
            "status": "planned",
        })
        self.assertEqual(resp.status_code, 503)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT date FROM shifts WHERE id = ?",
                (self._shift_id(),),
            ).fetchone()
        self.assertEqual(row[0], "2026-10-19")

    def test_delete_shift_returns_controlled_response_and_shift_survives(self):
        resp = self.client.post(
            f"/delete_shift/{self._shift_id()}", follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 503))
        self.assertNotEqual(resp.status_code, 500)
        with sqlite3.connect(self._db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],
                1,
            )


_SECRETS_PAYLOAD = {
    "client": "SecretClientNameAlphaOmega",
    "worker": "SecretWorkerBetaGamma",
    "sql": "SELECT secret FROM shifts WHERE password = 'leaky-sql-literal'",
    "dsn": (
        "postgresql://leaky_user:leaky_password@leaky-host.example/leaky_db"
    ),
    "traceback_marker": "LEAKY_TRACEBACK_MARKER_1234",
}


def _secret_strings():
    return (
        _SECRETS_PAYLOAD["client"],
        _SECRETS_PAYLOAD["worker"],
        _SECRETS_PAYLOAD["sql"],
        _SECRETS_PAYLOAD["dsn"],
        _SECRETS_PAYLOAD["traceback_marker"],
        "leaky_password",
        "leaky-sql-literal",
    )


class _LeakyError(RuntimeError):
    pass


def _raise_blowing_exception(*a, **kw):
    """Side-effect helper: ALWAYS raises an exception whose message
    carries every secret marker, so a leak in any log channel
    produces an unambiguous assertion failure."""
    raise _LeakyError(
        f"{_SECRETS_PAYLOAD['sql']} client={_SECRETS_PAYLOAD['client']} "
        f"worker={_SECRETS_PAYLOAD['worker']} "
        f"dsn={_SECRETS_PAYLOAD['dsn']} "
        f"{_SECRETS_PAYLOAD['traceback_marker']}"
    )


import contextlib  # noqa: E402
import logging  # noqa: E402


class _LogCaptureHandler(logging.Handler):
    def __init__(self_):
        super().__init__(level=logging.ERROR)
        self_.records = []

    def emit(self_, record):
        self_.records.append(record)


@contextlib.contextmanager
def _capture_app_errors():
    """Collect every ERROR+ record emitted on the app logger.

    Unlike ``assertLogs``, zero records is a success state here —
    "no log line at all" is the strongest confidentiality guarantee.
    """
    handler = _LogCaptureHandler()
    app_mod.app.logger.addHandler(handler)
    previous_level = app_mod.app.logger.level
    app_mod.app.logger.setLevel(logging.ERROR)
    try:
        yield handler
    finally:
        app_mod.app.logger.removeHandler(handler)
        app_mod.app.logger.setLevel(previous_level)


class AnalyzeConfidentialityTests(_AnalysisOnlyBase):
    """P1/P2/P3: no sensitive data appears in response body OR app
    logs on any of the five failure modes Codex listed."""

    def _assert_no_leaks(self, body, log_records):
        joined_log = "\n".join(
            [f"{r.name} {r.levelname} {r.getMessage()}" for r in log_records]
        )
        for secret in _secret_strings():
            self.assertNotIn(secret, body, f"body leaked {secret!r}")
            self.assertNotIn(
                secret, joined_log, f"log leaked {secret!r}"
            )
        # No traceback artefacts.
        for marker in ("Traceback", "RuntimeError", "_LeakyError",
                       "File \"", "File '"):
            self.assertNotIn(marker, joined_log,
                             f"log leaked traceback {marker!r}")

    # ------------------------------------------------------------------
    # 1. get_conn() raises → 503, zero leak.
    # ------------------------------------------------------------------
    def test_get_conn_failure_returns_503_without_leak(self):
        token = self._csrf_token()
        with mock.patch.object(
            app_mod, "get_conn", side_effect=_raise_blowing_exception,
        ), _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 503)
        self._assert_no_leaks(resp.get_data(as_text=True), cap.records)
        # Fixed sentinel present so operators still see SOMETHING.
        self.assertTrue(
            any("AUTO_PLAN_CONNECT_FAILED" in r.getMessage()
                for r in cap.records),
            cap.records,
        )

    # ------------------------------------------------------------------
    # 2. SET TRANSACTION READ ONLY fails → 500 or 503, zero leak.
    # ------------------------------------------------------------------
    def test_readonly_failure_returns_controlled_response_without_leak(self):
        token = self._csrf_token()
        self._seed_four_mondays(
            client=_SECRETS_PAYLOAD["client"],
            worker=_SECRETS_PAYLOAD["worker"],
        )
        with mock.patch.object(
            app_mod, "_ap_begin_readonly_tx",
            side_effect=_raise_blowing_exception,
        ), _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertIn(resp.status_code, (500, 503))
        self._assert_no_leaks(resp.get_data(as_text=True), cap.records)

    def test_readonly_classified_unavailable_returns_503_without_leak(self):
        """A ``ReadOnlyTransactionUnavailable`` with a leaky message
        must not propagate that message to logs or body."""
        token = self._csrf_token()

        def _raise_readonly(conn):
            raise app_mod.ReadOnlyTransactionUnavailable(
                _SECRETS_PAYLOAD["dsn"]
            )

        with mock.patch.object(
            app_mod, "_ap_begin_readonly_tx", _raise_readonly,
        ), _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 503)
        self._assert_no_leaks(resp.get_data(as_text=True), cap.records)

    # ------------------------------------------------------------------
    # 3. ap_fetch_history fails → 500, zero leak.
    # ------------------------------------------------------------------
    def test_fetch_history_failure_returns_500_without_leak(self):
        token = self._csrf_token()
        self._seed_four_mondays(
            client=_SECRETS_PAYLOAD["client"],
            worker=_SECRETS_PAYLOAD["worker"],
        )
        with mock.patch.object(
            app_mod, "ap_fetch_history",
            side_effect=_raise_blowing_exception,
        ), _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 500)
        self._assert_no_leaks(resp.get_data(as_text=True), cap.records)
        self.assertTrue(
            any("AUTO_PLAN_ANALYSIS_FAILED" in r.getMessage()
                for r in cap.records),
        )

    # ------------------------------------------------------------------
    # 4. ap_run_checks fails → 500, zero leak.
    # ------------------------------------------------------------------
    def test_run_checks_failure_returns_500_without_leak(self):
        token = self._csrf_token()
        self._seed_four_mondays(
            client=_SECRETS_PAYLOAD["client"],
            worker=_SECRETS_PAYLOAD["worker"],
        )
        with mock.patch.object(
            app_mod, "ap_run_checks",
            side_effect=_raise_blowing_exception,
        ), _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 500)
        self._assert_no_leaks(resp.get_data(as_text=True), cap.records)

    # ------------------------------------------------------------------
    # 5. Cursor/connection close() fails → 500 with zero leak.
    # ------------------------------------------------------------------
    def test_close_failure_does_not_mask_original_exception_leaks(self):
        token = self._csrf_token()
        self._seed_four_mondays(
            client=_SECRETS_PAYLOAD["client"],
            worker=_SECRETS_PAYLOAD["worker"],
        )

        class _CloseExplodingConn(sqlite3.Connection):
            def close(self_):
                raise RuntimeError(_SECRETS_PAYLOAD["dsn"])

            def rollback(self_):
                raise RuntimeError(_SECRETS_PAYLOAD["dsn"])

        with mock.patch.object(
            app_mod, "get_conn",
            lambda: sqlite3.connect(
                app_mod.SQLITE_PATH, factory=_CloseExplodingConn,
            ),
        ), mock.patch.object(
            app_mod, "ap_fetch_history",
            side_effect=_raise_blowing_exception,
        ), _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 500)
        self._assert_no_leaks(resp.get_data(as_text=True), cap.records)

    # ------------------------------------------------------------------
    # 6. Normal success path emits NO error log line.
    #    (The response body legitimately contains the client/worker
    #    names of the proposals — that is the feature's output. We
    #    verify only that NOTHING leaks into the error log.)
    # ------------------------------------------------------------------
    def test_normal_success_emits_no_error_log_leak(self):
        token = self._csrf_token()
        self._seed_four_mondays(
            client=_SECRETS_PAYLOAD["client"],
            worker=_SECRETS_PAYLOAD["worker"],
        )
        with _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 200)
        # Zero ERROR records on the happy path is the invariant.
        self.assertEqual(cap.records, [], cap.records)

    # ------------------------------------------------------------------
    # 7. Phase-5 write routes stay 403 in analysis_only.
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # P2-1: a failing logger must not leak the ambient exception.
    # ------------------------------------------------------------------
    def test_logger_failure_does_not_leak_original_exception(self):
        """When ``app.logger.error`` itself raises inside the sentinel
        helper (handler misconfigured, disk full, serializer crash on
        a structured logger), the surrounding exception's message —
        which carries the leaky payload — must NOT reach the response
        body or any other log channel. The HTTP response must stay
        the generic one."""
        token = self._csrf_token()
        self._seed_four_mondays(
            client=_SECRETS_PAYLOAD["client"],
            worker=_SECRETS_PAYLOAD["worker"],
        )

        # Inject a logger whose ``error`` method raises.
        import logging
        broken = logging.Logger("broken")

        def _blowing_log(*a, **kw):
            raise RuntimeError(_SECRETS_PAYLOAD["dsn"])
        broken.error = _blowing_log  # type: ignore[assignment]

        import io
        captured_stderr = io.StringIO()
        with mock.patch.object(
            app_mod, "ap_fetch_history",
            side_effect=_raise_blowing_exception,
        ), mock.patch.object(app_mod.app, "logger", broken), \
             contextlib.redirect_stderr(captured_stderr):
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 500)
        body = resp.get_data(as_text=True)
        stderr_dump = captured_stderr.getvalue()
        for secret in _secret_strings():
            self.assertNotIn(secret, body, f"body leaked {secret!r}")
            self.assertNotIn(
                secret, stderr_dump, f"stderr leaked {secret!r}"
            )
        for marker in ("Traceback", "_LeakyError", "RuntimeError"):
            self.assertNotIn(marker, body, f"body leaked {marker!r}")

    # ------------------------------------------------------------------
    # P2-2: a failing Jinja render on the SUCCESS path must not leak.
    # ------------------------------------------------------------------
    def test_success_render_failure_does_not_leak(self):
        """Replace the template with one that crashes while rendering —
        a Jinja ``UndefinedError`` or similar must NOT propagate to
        the Flask default handler. The response falls back to the
        hard-coded plain-text generic reply, and no secret appears
        in body or logs."""
        token = self._csrf_token()
        self._seed_four_mondays(
            client=_SECRETS_PAYLOAD["client"],
            worker=_SECRETS_PAYLOAD["worker"],
        )

        def _blowing_render(template, **context):
            raise RuntimeError(
                f"jinja2.UndefinedError client={_SECRETS_PAYLOAD['client']} "
                f"worker={_SECRETS_PAYLOAD['worker']} "
                f"dsn={_SECRETS_PAYLOAD['dsn']}"
            )

        with mock.patch.object(
            app_mod, "render_template_string", _blowing_render,
        ), _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        # 200 route tried to render → fallback hit → still returns 200
        # with the plain-text sentinel.
        self.assertIn(resp.status_code, (200, 400, 500))
        body = resp.get_data(as_text=True)
        self._assert_no_leaks(body, cap.records)
        # Fallback payload is a hard-coded string.
        self.assertIn("AUTO_PLAN_ANALYSIS_FAILED", body)

    def test_invalid_week_render_failure_does_not_leak(self):
        """The invalid-week early-return render is also wrapped —
        a crash there must fall back to the generic response."""
        token = self._csrf_token()

        def _blowing_render(template, **context):
            raise RuntimeError(
                f"jinja2 crash client={_SECRETS_PAYLOAD['client']} "
                f"worker={_SECRETS_PAYLOAD['worker']} "
                f"dsn={_SECRETS_PAYLOAD['dsn']}"
            )

        with mock.patch.object(
            app_mod, "render_template_string", _blowing_render,
        ), _capture_app_errors() as cap:
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-20",  # Tuesday → invalid
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 400)
        body = resp.get_data(as_text=True)
        self._assert_no_leaks(body, cap.records)
        self.assertIn("AUTO_PLAN_ANALYSIS_FAILED", body)

    def test_apply_route_remains_blocked(self):
        resp = self.client.post("/plan/review/1/apply", data={})
        self.assertEqual(resp.status_code, 403)

    def test_bulk_accept_route_remains_blocked(self):
        resp = self.client.post("/plan/review/1/bulk-accept", data={})
        self.assertEqual(resp.status_code, 403)

    def test_update_proposal_route_remains_blocked(self):
        resp = self.client.post(
            "/plan/review/1/proposal/1/update", data={"action": "accept"},
        )
        self.assertEqual(resp.status_code, 403)


class PgCursorCloseTests(unittest.TestCase):
    """P3: ``_PgCursor.close()`` delegates to psycopg2 and swallows
    close-on-closed errors without leaking exception data."""

    def test_close_delegates(self):
        closed = []

        class _Inner:
            def close(self_):
                closed.append(True)

        wrapper = app_mod._PgCursor(_Inner())
        wrapper.close()
        self.assertEqual(closed, [True])

    def test_close_swallows_double_close(self):
        class _Inner:
            def close(self_):
                raise RuntimeError(_SECRETS_PAYLOAD["dsn"])

        wrapper = app_mod._PgCursor(_Inner())
        # Must not raise; must not leak the DSN in a message.
        wrapper.close()

    def test_close_on_missing_cursor_is_safe(self):
        wrapper = app_mod._PgCursor.__new__(app_mod._PgCursor)
        # No attributes set; close() must be a tolerant no-op.
        wrapper.close()


class AnalyzeOnlyRunsSelectTests(_AnalysisOnlyBase):
    """I: the analyze POST path must emit SELECT only — no DDL,
    no INSERT, UPDATE, DELETE or TX control statement."""

    def test_analyze_path_sql_verbs(self):
        self._seed_four_mondays()
        token = self._csrf_token()

        verbs: list[str] = []

        class _VerbConn(sqlite3.Connection):
            def cursor(self, *a, **k):
                inner = sqlite3.Connection.cursor(self, *a, **k)

                class _Cur:
                    def __init__(self_, c):
                        self_._c = c

                    def execute(self_, sql, params=()):
                        verbs.append(sql.strip().split()[0].upper())
                        return self_._c.execute(sql, params)

                    def fetchone(self_):
                        return self_._c.fetchone()

                    def fetchall(self_):
                        return self_._c.fetchall()

                    def close(self_):
                        self_._c.close()

                    def __getattr__(self_, n):
                        return getattr(self_._c, n)

                return _Cur(inner)

            def commit(self):
                verbs.append("COMMIT")
                return sqlite3.Connection.commit(self)

            def rollback(self):
                verbs.append("ROLLBACK")
                return sqlite3.Connection.rollback(self)

        with mock.patch.object(
            app_mod, "get_conn",
            lambda: sqlite3.connect(
                app_mod.SQLITE_PATH, factory=_VerbConn,
            ),
        ):
            resp = self.client.post("/plan/analyze", data={
                "csrf_token": token,
                "target_week_start": "2026-10-19",
                "lookback_weeks": "4",
            })
        self.assertEqual(resp.status_code, 200)
        forbidden = {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER",
                     "DROP", "TRUNCATE", "COMMIT"}
        hit = forbidden.intersection(verbs)
        self.assertFalse(
            hit, f"analyze POST emitted forbidden SQL: {hit}; log: {verbs}"
        )
        self.assertIn("SELECT", verbs)


if __name__ == "__main__":
    unittest.main()
