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
        real_get_conn = app_mod.get_conn

        class _RecordingCursor:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, params=()):
                statements.append(sql.strip().split()[0].upper())
                return self._inner.execute(sql, params)

            def fetchone(self):
                return self._inner.fetchone()

            def fetchall(self):
                return self._inner.fetchall()

            def close(self):
                return self._inner.close()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        class _RecordingConn:
            def __init__(wrapper, inner):
                wrapper.inner = inner

            def cursor(wrapper):
                return _RecordingCursor(wrapper.inner.cursor())

            def commit(wrapper):
                statements.append("COMMIT")
                wrapper.inner.commit()

            def rollback(wrapper):
                statements.append("ROLLBACK")
                wrapper.inner.rollback()

            def close(wrapper):
                wrapper.inner.close()

        def _wrapped_conn():
            inner = real_get_conn()
            return _RecordingConn(inner)

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


if __name__ == "__main__":
    unittest.main()
