"""Unit tests for ``tests/seed_staging_via_http.py``.

The tests NEVER open a real socket. For the guard logic they use plain
dicts in place of ``os.environ``. For the end-to-end seed workflow they
drive the real Flask app in-process via ``flask.Flask.test_client`` on
an isolated temporary SQLite database, through a small adapter that
exposes the same ``get`` / ``post`` interface as the real HttpClient.

This gives the strongest signal we can get without connecting to
staging: the exact admin routes the HTTP seed script posts to are the
same ones executing here.
"""

from __future__ import annotations

import os
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


# Keep this suite isolated from the normal auto-planning suite, same
# precautions as tests/test_admin_bootstrap.py.
TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp_http_seed")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-http-seed-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("APP_ENV", None)
os.environ.pop("ADMIN_BOOTSTRAP_PASSWORD", None)
os.environ.pop("ADMIN_BOOTSTRAP_USERNAME", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "http-seed-tests-only")


import seed_staging_via_http as http_seed  # noqa: E402


def _good_env(**overrides):
    base = {
        http_seed.CONFIRM_ENV: http_seed.CONFIRM_VALUE,
        http_seed.ADMIN_USERNAME_ENV: "admin",
        http_seed.ADMIN_PASSWORD_ENV: "unit-test-admin-password-9b2x",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Pure guard tests — never touch Flask or sockets.
# ---------------------------------------------------------------------------


class EnvGuardTests(unittest.TestCase):
    def test_confirm_token_required(self):
        env = _good_env()
        env[http_seed.CONFIRM_ENV] = "wrong"
        with self.assertRaises(http_seed.HttpSeedEnvRefused) as cm:
            http_seed.assert_http_seed_environment(env)
        self.assertIn(http_seed.CONFIRM_ENV, str(cm.exception))

    def test_admin_username_required(self):
        env = _good_env()
        env.pop(http_seed.ADMIN_USERNAME_ENV)
        with self.assertRaises(http_seed.HttpSeedEnvRefused):
            http_seed.assert_http_seed_environment(env)

    def test_admin_password_required(self):
        env = _good_env()
        env.pop(http_seed.ADMIN_PASSWORD_ENV)
        with self.assertRaises(http_seed.HttpSeedEnvRefused):
            http_seed.assert_http_seed_environment(env)

    def test_good_env_returns_pinned_url(self):
        self.assertEqual(
            http_seed.assert_http_seed_environment(_good_env()),
            http_seed.STAGING_BASE_URL,
        )

    def test_refusal_message_never_leaks_credentials(self):
        env = _good_env(STAGING_HTTP_SEED_CONFIRM="wrong-token")
        try:
            http_seed.assert_http_seed_environment(env)
        except http_seed.HttpSeedEnvRefused as exc:
            self.assertNotIn(env[http_seed.ADMIN_PASSWORD_ENV], str(exc))
            self.assertNotIn("wrong-token", str(exc))


class TargetUrlGuardTests(unittest.TestCase):
    def test_non_https_url_is_refused(self):
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "http://luxmann-planner-staging.onrender.com/"
            )

    def test_wrong_host_is_refused(self):
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://evil.example.com/"
            )

    def test_production_marker_in_url_is_refused(self):
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://luxmann-planner-staging.onrender.com/prod/anything"
            )

    def test_override_env_must_match_pinned_constant(self):
        env = _good_env(STAGING_BASE_URL="https://other.example.com/")
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed.resolve_target_base_url(env)

    def test_override_env_equal_to_pinned_is_accepted(self):
        env = _good_env(STAGING_BASE_URL=http_seed.STAGING_BASE_URL)
        self.assertEqual(
            http_seed.resolve_target_base_url(env),
            http_seed.STAGING_BASE_URL,
        )

    def test_default_pinned_url_is_https_staging_only(self):
        # Documented invariant: the hardcoded constant always passes.
        http_seed._assert_url_is_staging(http_seed.STAGING_BASE_URL)


class RedirectHandlerTests(unittest.TestCase):
    def test_redirect_to_other_host_refused(self):
        handler = http_seed._PinnedRedirectHandler(http_seed.STAGING_HOST)
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            handler.redirect_request(
                req=mock.Mock(), fp=mock.Mock(), code=302,
                msg="Found", headers={},
                newurl="https://evil.example.com/path",
            )


class NameListingDetectionTests(unittest.TestCase):
    def test_substring_match_rejected(self):
        body = "<td>APSTG_TEST_Client_AX</td>"
        self.assertFalse(
            http_seed._name_in_listing(body, "APSTG_TEST_Client_A")
        )

    def test_exact_match_in_td_detected(self):
        body = "<td>APSTG_TEST_Client_A</td>"
        self.assertTrue(
            http_seed._name_in_listing(body, "APSTG_TEST_Client_A")
        )

    def test_exact_match_in_option_label_detected(self):
        body = "<option value='x'>APSTG_TEST_Radnik_A</option>"
        self.assertTrue(
            http_seed._name_in_listing(body, "APSTG_TEST_Radnik_A")
        )


# ---------------------------------------------------------------------------
# End-to-end tests: real admin routes, in-process Flask, SQLite temp DB.
# ---------------------------------------------------------------------------


import importlib  # noqa: E402

app_mod = importlib.import_module("app")

TEST_ADMIN_PASSWORD = "integration-test-admin-9b2x"


class FlaskClientAdapter:
    """Expose ``get`` / ``post`` with the same shape as HttpClient.

    Delegates to ``flask.Flask.test_client``. follow_redirects=True
    mirrors the real urllib opener's behaviour, so route-handler
    redirects (/add_client → /clients, /login → /) land us on the
    final page just like the production flow.
    """

    def __init__(self, flask_client):
        self._c = flask_client

    def get(self, path):
        resp = self._c.get(path, follow_redirects=True)
        return http_seed.HttpResponse(
            status=resp.status_code,
            final_url=path,
            body=resp.get_data(as_text=True),
            headers={},
        )

    def post(self, path, form):
        data = {}
        for key, value in form.items():
            data[key] = list(value) if isinstance(value, (list, tuple)) else value
        resp = self._c.post(path, data=data, follow_redirects=True)
        return http_seed.HttpResponse(
            status=resp.status_code,
            final_url=path,
            body=resp.get_data(as_text=True),
            headers={},
        )


class _FlaskBackedBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="luxmann-http-seed-fx-")

    def setUp(self):
        # One unique file per test so Windows-held sqlite connections
        # from the previous test don't block the next one's open.
        self._db_path = os.path.join(
            self._tmp, f"planer-{os.urandom(4).hex()}.db"
        )
        patcher = mock.patch.object(app_mod, "SQLITE_PATH", self._db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Bootstrap a deterministic admin credential (non-staging path).
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
        for key in ("APP_ENV",):
            os.environ.pop(key, None)

        app_mod.init_db()

        app_mod.app.config["TESTING"] = True
        app_mod.app.config["WTF_CSRF_ENABLED"] = False
        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)

    def _env(self):
        return {
            http_seed.CONFIRM_ENV: http_seed.CONFIRM_VALUE,
            http_seed.ADMIN_USERNAME_ENV: "admin",
            http_seed.ADMIN_PASSWORD_ENV: TEST_ADMIN_PASSWORD,
        }

    def _rows(self, sql, params=()):
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class LoginTests(_FlaskBackedBase):
    def test_login_succeeds_with_the_bootstrap_credentials(self):
        http_seed.login_as_admin(self.adapter, self._env())
        # Markers present on an authenticated page.
        probe = self.adapter.get("/clients")
        self.assertEqual(probe.status, 200)
        self.assertIn("/logout", probe.body)

    def test_login_fails_with_wrong_password(self):
        env = self._env()
        env[http_seed.ADMIN_PASSWORD_ENV] = "not-the-right-one"
        with self.assertRaises(http_seed.HttpSeedAuthFailed):
            http_seed.login_as_admin(self.adapter, env)

    def test_login_exception_does_not_quote_the_password(self):
        env = self._env()
        env[http_seed.ADMIN_PASSWORD_ENV] = "sentinel-leak-check-abc123"
        try:
            http_seed.login_as_admin(self.adapter, env)
        except http_seed.HttpSeedAuthFailed as exc:
            self.assertNotIn("sentinel-leak-check-abc123", str(exc))


class SeedEndToEndTests(_FlaskBackedBase):
    def test_dry_run_posts_nothing(self):
        report = http_seed.run_seed(self.adapter, self._env(), dry_run=True)
        self.assertFalse(report.client_posted)
        self.assertFalse(report.worker_posted)
        self.assertEqual(report.shifts_posted, 0)

        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM clients WHERE name = ?",
                       (http_seed.CLIENT_NAME,))[0][0], 0,
        )
        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM workers WHERE name = ?",
                       (http_seed.WORKER_NAME,))[0][0], 0,
        )
        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM shifts WHERE worker = ? AND client = ?",
                       (http_seed.WORKER_NAME, http_seed.CLIENT_NAME))[0][0], 0,
        )

    def test_seed_creates_client_worker_and_four_shifts(self):
        report = http_seed.run_seed(self.adapter, self._env())
        self.assertTrue(report.client_posted)
        self.assertTrue(report.worker_posted)
        self.assertEqual(report.shifts_posted, 4)

        clients = self._rows(
            "SELECT name, address FROM clients WHERE name = ?",
            (http_seed.CLIENT_NAME,),
        )
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0][0], http_seed.CLIENT_NAME)

        workers = self._rows(
            "SELECT name FROM workers WHERE name = ?",
            (http_seed.WORKER_NAME,),
        )
        self.assertEqual(len(workers), 1)

        shifts = self._rows(
            "SELECT date, time, worker, client, status "
            "FROM shifts WHERE worker = ? AND client = ? ORDER BY date",
            (http_seed.WORKER_NAME, http_seed.CLIENT_NAME),
        )
        self.assertEqual(
            [row[0] for row in shifts],
            list(http_seed.SHIFT_DATES),
        )
        for row in shifts:
            self.assertEqual(row[1], "09:00-11:00")
            self.assertEqual(row[2], http_seed.WORKER_NAME)
            self.assertEqual(row[3], http_seed.CLIENT_NAME)
            self.assertEqual(row[4], "planned")

    def test_second_run_adds_no_rows_and_skips_add_posts(self):
        http_seed.run_seed(self.adapter, self._env())
        # Fresh client to clear the session cookie — otherwise the first
        # /logout already dropped it, but we re-create to be explicit.
        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)

        report = http_seed.run_seed(self.adapter, self._env())
        self.assertFalse(report.client_posted)
        self.assertFalse(report.worker_posted)
        # Shifts are POSTed again (the server guards them), but the
        # duplicate_shift_exists check means no new row appears.
        self.assertEqual(report.shifts_posted, 4)
        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM shifts WHERE worker = ? AND client = ?",
                       (http_seed.WORKER_NAME, http_seed.CLIENT_NAME))[0][0], 4,
        )
        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM clients WHERE name = ?",
                       (http_seed.CLIENT_NAME,))[0][0], 1,
        )
        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM workers WHERE name = ?",
                       (http_seed.WORKER_NAME,))[0][0], 1,
        )

    def test_seed_never_touches_non_prefixed_rows(self):
        # Pre-create a legitimate client, worker and shift.
        import sqlite3
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("INSERT INTO clients (name, address) VALUES (?, ?)",
                         ("Legit_Client", "123 Rue Legit"))
            conn.execute(
                "INSERT INTO workers (name, address) VALUES (?, ?)",
                ("Legit_Worker", ""),
            )
            conn.execute(
                "INSERT INTO shifts (worker, client, date, time, status) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Legit_Worker", "Legit_Client", "2026-09-21",
                 "08:00-09:00", "planned"),
            )
            conn.commit()

        http_seed.run_seed(self.adapter, self._env())

        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM clients WHERE name = ?",
                       ("Legit_Client",))[0][0], 1,
        )
        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM workers WHERE name = ?",
                       ("Legit_Worker",))[0][0], 1,
        )
        self.assertEqual(
            self._rows(
                "SELECT COUNT(*) FROM shifts WHERE worker = ? AND client = ? "
                "AND time = ?",
                ("Legit_Worker", "Legit_Client", "08:00-09:00"),
            )[0][0], 1,
        )

    def test_verify_only_logs_in_reads_and_logs_out(self):
        # Pre-seed one entity; verify-only must detect it, write nothing.
        http_seed.run_seed(self.adapter, self._env())

        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)

        report = http_seed.run_verify_only(self.adapter, self._env())
        self.assertTrue(report.client_already_present)
        self.assertTrue(report.worker_already_present)
        self.assertFalse(report.client_posted)
        self.assertFalse(report.worker_posted)
        self.assertEqual(report.shifts_posted, 0)

    def test_seeded_history_is_detectable_by_the_recurrence_engine(self):
        """The 4 shifts /add_shift created must group as weekly recurrence."""
        http_seed.run_seed(self.adapter, self._env())
        shifts = self._rows(
            "SELECT id, worker, client, date, time, status FROM shifts "
            "WHERE worker = ? AND client = ? ORDER BY date",
            (http_seed.WORKER_NAME, http_seed.CLIENT_NAME),
        )
        grouped = app_mod.ap_group_by_key(shifts)
        self.assertEqual(len(grouped), 1, grouped)
        (key, occurrences), = grouped.items()
        client, _workers, weekday, start, end = key
        self.assertEqual(client, http_seed.CLIENT_NAME)
        self.assertEqual(weekday, 0)
        self.assertEqual(start, "09:00")
        self.assertEqual(end, "11:00")
        self.assertEqual(len(occurrences), 4)
        self.assertEqual(app_mod.ap_period_weeks(occurrences), 1)


def tearDownModule():
    # Mirror the test_admin_bootstrap.py precaution: do not shutil.rmtree
    # TEST_STORAGE; other test modules sharing this process's `app` import
    # may still depend on it. OS tempdir cleanup handles it later.
    pass


if __name__ == "__main__":
    unittest.main()
