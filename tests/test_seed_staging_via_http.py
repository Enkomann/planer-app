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

    def test_lookalike_host_is_refused(self):
        """P1: a host that merely resembles the staging host is refused."""
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://luxmann-planner-staging.onrender.com.evil.example/"
            )

    def test_production_marker_in_url_is_refused(self):
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://luxmann-planner-staging.onrender.com/prod/anything"
            )

    def test_userinfo_in_url_is_refused(self):
        """P1: ``user:pass@host`` syntax is refused — the username/
        password would leak into any logged URL."""
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://evil:secret@luxmann-planner-staging.onrender.com/"
            )

    def test_non_443_port_is_refused(self):
        """P1: a redirect to a different port on the same host is refused."""
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://luxmann-planner-staging.onrender.com:8443/"
            )

    def test_port_443_explicit_is_accepted(self):
        """P1: the standard HTTPS port is accepted when stated explicitly."""
        http_seed._assert_url_is_staging(
            "https://luxmann-planner-staging.onrender.com:443/clients"
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
        http_seed._assert_url_is_staging(http_seed.STAGING_BASE_URL)


class RedirectHandlerTests(unittest.TestCase):
    def _handler(self):
        return http_seed._PinnedRedirectHandler(http_seed.STAGING_HOST)

    def _refuse(self, newurl):
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            self._handler().redirect_request(
                req=mock.Mock(), fp=mock.Mock(), code=302,
                msg="Found", headers={}, newurl=newurl,
            )

    def test_redirect_to_other_host_refused(self):
        self._refuse("https://evil.example.com/path")

    def test_redirect_to_http_downgrade_refused(self):
        """P1: a 302 to http:// on the same host is a downgrade."""
        self._refuse(
            "http://luxmann-planner-staging.onrender.com/elsewhere"
        )

    def test_redirect_to_non_443_port_refused(self):
        """P1: a 302 to :8080 on the same host is refused."""
        self._refuse(
            "https://luxmann-planner-staging.onrender.com:8080/x"
        )

    def test_redirect_with_userinfo_refused(self):
        """P1: a 302 whose Location carries userinfo is refused."""
        self._refuse(
            "https://a:b@luxmann-planner-staging.onrender.com/x"
        )

    def test_redirect_to_lookalike_subdomain_refused(self):
        """P1: ``onrender.com.evil`` sneaking the suffix is refused."""
        self._refuse(
            "https://luxmann-planner-staging.onrender.com.evil/x"
        )


class HttpClientEncodingTests(unittest.TestCase):
    def test_post_materialises_form_and_preserves_multiple_workers(self):
        """Exercise urllib's real encoding path without opening a socket."""
        client = http_seed.HttpClient(http_seed.STAGING_BASE_URL)
        response = http_seed.HttpResponse(
            status=200,
            final_url=f"{http_seed.STAGING_BASE_URL}/add_shift",
            body="ok",
        )
        client._open = mock.Mock(return_value=response)

        result = client.post(
            "/add_shift",
            {
                "workers": ["Worker One", "Worker Two"],
                "client": "Client A",
                "status": "planned",
            },
        )

        self.assertIs(result, response)
        url, = client._open.call_args.args
        self.assertEqual(url, f"{http_seed.STAGING_BASE_URL}/add_shift")
        encoded = client._open.call_args.kwargs["data"].decode("ascii")
        self.assertEqual(
            urllib.parse.parse_qsl(encoded, keep_blank_values=True),
            [
                ("workers", "Worker One"),
                ("workers", "Worker Two"),
                ("client", "Client A"),
                ("status", "planned"),
            ],
        )
        self.assertEqual(
            client._open.call_args.kwargs["extra_headers"],
            {"Content-Type": "application/x-www-form-urlencoded"},
        )


class MonthAnchorParserTests(unittest.TestCase):
    """P1 + P2: the /month HTML parser only accepts the Jinja edit-link
    anchor, with every ``data-*`` attribute present. A flash message or
    a script line is ignored."""

    def test_full_valid_anchor_is_parsed(self):
        body = (
            '<a class="mini-link edit-link" '
            'data-eid="12" data-ew="APSTG_TEST_Radnik_A" '
            'data-ecl="APSTG_TEST_Client_A" data-edt="2026-09-21" '
            'data-etm="09:00-11:00" data-est="planned">x</a>'
        )
        parsed = http_seed._parse_shift_anchors(body)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["data-est"], "planned")
        self.assertEqual(parsed[0]["data-ew"], "APSTG_TEST_Radnik_A")
        self.assertEqual(parsed[0]["data-ecl"], "APSTG_TEST_Client_A")
        self.assertEqual(parsed[0]["data-edt"], "2026-09-21")
        self.assertEqual(parsed[0]["data-etm"], "09:00-11:00")

    def test_anchor_missing_any_data_attr_is_skipped(self):
        body = (
            '<a class="edit-link" data-ew="x" data-ecl="y">'
            'no date/time/status</a>'
        )
        self.assertEqual(http_seed._parse_shift_anchors(body), [])

    def test_script_content_matching_data_attr_shape_is_not_matched(self):
        body = (
            '<script>var a = \'data-ew="X" data-ecl="Y" data-edt="D" '
            'data-etm="T" data-est="S"\';</script>'
        )
        self.assertEqual(http_seed._parse_shift_anchors(body), [])

    def test_flash_mentioning_the_fake_name_is_not_matched(self):
        body = (
            '<div class="flash">Clé: APSTG_TEST_Client_A dodat</div>'
        )
        self.assertEqual(http_seed._parse_shift_anchors(body), [])

    def test_html_entities_in_values_are_decoded(self):
        body = (
            '<a class="edit-link" data-eid="1" data-ew="A &amp; B" '
            'data-ecl="C &lt;Z&gt;" data-edt="2026-09-21" '
            'data-etm="09:00-11:00" data-est="planned">x</a>'
        )
        parsed = http_seed._parse_shift_anchors(body)
        self.assertEqual(parsed[0]["data-ew"], "A & B")
        self.assertEqual(parsed[0]["data-ecl"], "C <Z>")


class RowAnchorDetectionTests(unittest.TestCase):
    """P2: the exists() detection must not false-positive on flashes,
    titles, scripts, or data attributes that contain the fictional name.
    It must only match the exact per-row edit anchor the Jinja template
    emits once per DB row (``href="/edit_<entity>/<urlencoded name>"``).
    """

    def test_flash_message_does_not_count_as_existing(self):
        body = (
            '<div class="flash">✓ Klijent dodat: APSTG_TEST_Client_A</div>'
        )
        self.assertFalse(
            http_seed._row_anchor_present(
                body, "/edit_client/", "APSTG_TEST_Client_A"
            )
        )

    def test_title_bar_does_not_count(self):
        body = "<title>APSTG_TEST_Client_A - Luxmann</title>"
        self.assertFalse(
            http_seed._row_anchor_present(
                body, "/edit_client/", "APSTG_TEST_Client_A"
            )
        )

    def test_script_constant_does_not_count(self):
        body = '<script>var x = "APSTG_TEST_Client_A";</script>'
        self.assertFalse(
            http_seed._row_anchor_present(
                body, "/edit_client/", "APSTG_TEST_Client_A"
            )
        )

    def test_data_attribute_does_not_count(self):
        body = '<div data-name="APSTG_TEST_Client_A">x</div>'
        self.assertFalse(
            http_seed._row_anchor_present(
                body, "/edit_client/", "APSTG_TEST_Client_A"
            )
        )

    def test_exact_row_edit_anchor_counts(self):
        body = (
            '<a href="/edit_client/APSTG_TEST_Client_A">Edit</a>'
        )
        self.assertTrue(
            http_seed._row_anchor_present(
                body, "/edit_client/", "APSTG_TEST_Client_A"
            )
        )

    def test_single_quoted_href_also_counts(self):
        body = "<a href='/edit_worker/APSTG_TEST_Radnik_A'>e</a>"
        self.assertTrue(
            http_seed._row_anchor_present(
                body, "/edit_worker/", "APSTG_TEST_Radnik_A"
            )
        )

    def test_url_encoded_name_in_anchor_counts(self):
        # Jinja applies |urlencode to the name. A name with a space
        # would come out as 'APSTG_TEST_Client%20A' in the href.
        quoted_name = "APSTG_TEST Client A"
        body = (
            '<a href="/edit_client/APSTG_TEST%20Client%20A">Edit</a>'
        )
        self.assertTrue(
            http_seed._row_anchor_present(
                body, "/edit_client/", quoted_name
            )
        )

    def test_different_edit_route_does_not_cross_match(self):
        body = '<a href="/edit_shift/APSTG_TEST_Client_A">e</a>'
        self.assertFalse(
            http_seed._row_anchor_present(
                body, "/edit_client/", "APSTG_TEST_Client_A"
            )
        )


# ---------------------------------------------------------------------------
# End-to-end tests: real admin routes, in-process Flask, SQLite temp DB.
# ---------------------------------------------------------------------------


import importlib  # noqa: E402

app_mod = importlib.import_module("app")

TEST_ADMIN_PASSWORD = "integration-test-admin-9b2x"


import urllib.parse  # noqa: E402


class FlaskClientAdapter:
    """Expose ``get`` / ``post`` with the same shape as HttpClient.

    Faithfully reports the FINAL URL after following redirects (so a
    POST that bounced to /login shows ``final_url`` ending in /login,
    not the originally posted path). This mirrors the real urllib
    opener and makes the "did we land on /login?" check actually
    testable in-process.
    """

    base_url = http_seed.STAGING_BASE_URL
    MAX_REDIRECTS = 10

    def __init__(self, flask_client):
        self._c = flask_client

    def get(self, path):
        resp = self._c.get(path, follow_redirects=False)
        return self._follow(resp, path, original_method="GET",
                            original_data=None)

    def post(self, path, form):
        data = {}
        for key, value in form.items():
            data[key] = (
                list(value) if isinstance(value, (list, tuple)) else value
            )
        resp = self._c.post(path, data=data, follow_redirects=False)
        return self._follow(resp, path, original_method="POST",
                            original_data=data)

    def _follow(self, resp, original_path, original_method, original_data):
        """Follow redirects with RFC-compliant method semantics.

        * 301 / 302 / 303: method becomes GET, body dropped — the
          classical post-redirect-get pattern Flask uses after
          ``/add_client`` → ``/clients``, ``/add_shift`` → referrer,
          ``/login`` → ``/``, etc.
        * 307 / 308: method AND body are preserved — the browser
          replays the original request. Werkzeug can emit these from
          custom code, so we handle them to stay faithful to the real
          urllib HttpClient. (Werkzeug's own test_client follow does
          301/302 as GET too; we only ever repost on 307/308.)
        Also refuses any Location that targets a different origin,
        matching the real driver's cross-origin guard.
        """
        current_path = original_path
        hops = 0
        while 300 <= resp.status_code < 400 and hops < self.MAX_REDIRECTS:
            location = resp.headers.get("Location")
            if not location:
                break
            parsed = urllib.parse.urlparse(location)
            # Reject a hostile cross-origin Location — matches the
            # production driver's _PinnedRedirectHandler behaviour.
            if parsed.netloc and parsed.netloc not in (
                "", "localhost", "127.0.0.1",
            ):
                raise http_seed.HttpSeedTargetRefused(
                    f"redirect to a non-local origin refused: {parsed.netloc}"
                )
            follow = parsed.path
            if parsed.query:
                follow = follow + "?" + parsed.query
            current_path = follow
            if resp.status_code in (307, 308) and original_method == "POST":
                resp = self._c.post(
                    follow, data=original_data, follow_redirects=False,
                )
            else:
                resp = self._c.get(follow, follow_redirects=False)
            hops += 1
        return http_seed.HttpResponse(
            status=resp.status_code,
            final_url=f"{self.base_url}{current_path}",
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

    # ------------------------------------------------------------------
    # Codex P1: no false POST success.
    # ------------------------------------------------------------------

    def test_run_seed_reports_verified_true_after_a_live_run(self):
        """P1: a report with verified=True proves ALL 4 shifts landed."""
        report = http_seed.run_seed(self.adapter, self._env())
        self.assertTrue(report.verified)
        self.assertEqual(
            report.verified_shift_dates, tuple(http_seed.SHIFT_DATES)
        )
        self.assertEqual(report.verification_failures, ())

    def test_dry_run_claims_verified_only_when_rows_really_exist(self):
        """P5: dry-run reports what the GET view actually sees."""
        # Fresh DB → nothing exists → verified=False, 4 failures.
        report = http_seed.run_seed(self.adapter, self._env(), dry_run=True)
        self.assertFalse(report.verified)
        self.assertEqual(report.verified_shift_dates, ())
        # Four shift ABSENT entries + client not present + worker not present
        self.assertEqual(len(report.verification_failures), 6)

        # Now actually seed, re-run dry-run → verified=True.
        http_seed.run_seed(self.adapter, self._env())
        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)
        report = http_seed.run_seed(self.adapter, self._env(), dry_run=True)
        self.assertTrue(report.verified)
        self.assertEqual(len(report.verified_shift_dates), 4)
        self.assertEqual(report.verification_failures, ())

    def test_dry_run_with_only_client_and_worker_does_not_claim_verified(self):
        """P5: four shift ABSENTs are reported, not hidden as a success."""
        import sqlite3
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO clients (name, address) VALUES (?, ?)",
                (http_seed.CLIENT_NAME, http_seed.CLIENT_ADDRESS),
            )
            conn.execute(
                "INSERT INTO workers (name, address) VALUES (?, ?)",
                (http_seed.WORKER_NAME, ""),
            )
            conn.commit()
        report = http_seed.run_seed(self.adapter, self._env(), dry_run=True)
        self.assertTrue(report.client_already_present)
        self.assertTrue(report.worker_already_present)
        self.assertFalse(report.verified)
        self.assertEqual(report.verified_shift_dates, ())
        # Exactly four ABSENT entries, no client/worker false flags.
        self.assertEqual(len(report.verification_failures), 4)
        for failure in report.verification_failures:
            self.assertIn("ABSENT", failure)

    def test_verify_detects_missing_shift_and_raises(self):
        """P1: if even one shift is missing, verification raises.

        We seed, then delete one shift directly in the DB, then call
        the verification function. Expectation: HttpSeedError, no
        false "seed OK".
        """
        http_seed.run_seed(self.adapter, self._env())
        import sqlite3
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM shifts WHERE worker = ? AND client = ? "
                "AND date = ?",
                (http_seed.WORKER_NAME, http_seed.CLIENT_NAME,
                 "2026-10-05"),
            )
            conn.commit()
        # Fresh adapter so a new login runs.
        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)
        http_seed.login_as_admin(self.adapter, self._env())
        with self.assertRaises(http_seed.HttpSeedError) as cm:
            http_seed.verify_seed_landed(self.adapter)
        self.assertIn("2026-10-05", str(cm.exception))

    def test_retry_after_partial_failure_fills_the_gap(self):
        """P1: a seed run is a safe retry — missing rows are completed,
        no duplication appears."""
        # Simulate a partial first run by directly inserting only
        # client+worker and 2 of the 4 shifts (as if the previous
        # run crashed mid-way).
        import sqlite3
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO clients (name, address) VALUES (?, ?)",
                (http_seed.CLIENT_NAME, http_seed.CLIENT_ADDRESS),
            )
            conn.execute(
                "INSERT INTO workers (name, address) VALUES (?, ?)",
                (http_seed.WORKER_NAME, ""),
            )
            for d in http_seed.SHIFT_DATES[:2]:
                conn.execute(
                    "INSERT INTO shifts (worker, client, date, time, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (http_seed.WORKER_NAME, http_seed.CLIENT_NAME,
                     d, "09:00-11:00", "planned"),
                )
            conn.commit()
        report = http_seed.run_seed(self.adapter, self._env())
        self.assertFalse(report.client_posted)
        self.assertFalse(report.worker_posted)
        self.assertTrue(report.verified)
        self.assertEqual(
            report.verified_shift_dates, tuple(http_seed.SHIFT_DATES)
        )
        # The 2 pre-existing shifts stayed as single rows (no dup).
        self.assertEqual(
            self._rows("SELECT COUNT(*) FROM shifts WHERE worker = ? AND client = ?",
                       (http_seed.WORKER_NAME, http_seed.CLIENT_NAME))[0][0], 4,
        )


class LoginFailureEndToEndTests(_FlaskBackedBase):
    def test_post_add_client_on_dropped_session_raises(self):
        """P1: a 200 after a /login bounce must NOT look like success."""
        # Build an adapter WITHOUT running login first.
        with self.assertRaises(http_seed.HttpSeedAuthFailed):
            http_seed.post_add_client(
                self.adapter, http_seed.CLIENT_NAME, http_seed.CLIENT_ADDRESS,
            )

    def test_post_add_shift_on_dropped_session_raises(self):
        with self.assertRaises(http_seed.HttpSeedAuthFailed):
            http_seed.post_add_shift(
                self.adapter, http_seed.WORKER_NAME,
                http_seed.CLIENT_NAME, "2026-09-21",
            )

    def test_client_exists_bails_when_session_bounces_to_login(self):
        with self.assertRaises(http_seed.HttpSeedAuthFailed):
            http_seed.client_exists(self.adapter, http_seed.CLIENT_NAME)


class FlaskAdapterRedirectSemanticsTests(unittest.TestCase):
    """P4: FlaskClientAdapter honours RFC redirect method semantics.

    We mount a tiny stub Flask that returns configurable redirects, so
    the test doesn't depend on the Planner's routes.
    """

    def setUp(self):
        from flask import Flask, redirect, request as flask_request
        stub = Flask("stub")
        stub.config["TESTING"] = True
        self._observed = []

        @stub.route("/301", methods=["GET", "POST"])
        def r301():
            self._observed.append((flask_request.method, "/301"))
            return redirect("/landing", code=301)

        @stub.route("/302", methods=["GET", "POST"])
        def r302():
            self._observed.append((flask_request.method, "/302"))
            return redirect("/landing", code=302)

        @stub.route("/303", methods=["GET", "POST"])
        def r303():
            self._observed.append((flask_request.method, "/303"))
            return redirect("/landing", code=303)

        @stub.route("/307", methods=["GET", "POST"])
        def r307():
            self._observed.append((flask_request.method, "/307"))
            return redirect("/landing", code=307)

        @stub.route("/308", methods=["GET", "POST"])
        def r308():
            self._observed.append((flask_request.method, "/308"))
            return redirect("/landing", code=308)

        @stub.route("/landing", methods=["GET", "POST"])
        def landing():
            self._observed.append((flask_request.method, "/landing"))
            return f"landed via {flask_request.method}"

        @stub.route("/hostile")
        def hostile():
            return redirect("https://evil.example.com/x", code=302)

        self.adapter = FlaskClientAdapter(stub.test_client())

    def test_302_post_becomes_get_on_redirect(self):
        resp = self.adapter.post("/302", {"x": "1"})
        self.assertEqual(resp.status, 200)
        # Last hop must be a GET.
        self.assertEqual(self._observed[-1], ("GET", "/landing"))

    def test_303_post_becomes_get_on_redirect(self):
        resp = self.adapter.post("/303", {"x": "1"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._observed[-1], ("GET", "/landing"))

    def test_307_post_preserves_method(self):
        resp = self.adapter.post("/307", {"x": "1"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._observed[-1], ("POST", "/landing"))
        self.assertIn("landed via POST", resp.body)

    def test_308_post_preserves_method(self):
        resp = self.adapter.post("/308", {"x": "1"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._observed[-1], ("POST", "/landing"))

    def test_301_get_follows_as_get(self):
        resp = self.adapter.get("/301")
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._observed[-1], ("GET", "/landing"))

    def test_hostile_redirect_is_refused_in_adapter_too(self):
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            self.adapter.get("/hostile")


class FlaskAdapterFidelityTests(_FlaskBackedBase):
    """P1: tests covering the HTTP driver's view of what REALLY happened."""

    def test_final_url_shows_login_after_admin_bounce(self):
        """A GET to /clients without a session must report final_url=/login.

        The ``_ensure_landed_on`` guard relies on this being accurate.
        """
        resp = self.adapter.get("/clients")
        parsed = urllib.parse.urlparse(resp.final_url)
        self.assertEqual(parsed.path, "/login")

    def test_month_view_verifier_sees_every_seeded_shift(self):
        """P1: the GET-only /month verifier returns 4 fully_correct rows."""
        http_seed.run_seed(self.adapter, self._env())
        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)
        http_seed.login_as_admin(self.adapter, self._env())
        rows = http_seed.verify_shifts_via_month_view(self.adapter)
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertTrue(row.fully_correct, row)
            self.assertEqual(row.observed_status, "planned")
            self.assertEqual(row.observed_time, "09:00-11:00")
            self.assertEqual(row.observed_worker, http_seed.WORKER_NAME)
            self.assertEqual(row.observed_client, http_seed.CLIENT_NAME)


class StrictStatusVerificationTests(_FlaskBackedBase):
    """P3: a row whose status is not ``planned`` is NOT a successful seed."""

    def test_done_status_in_db_is_reported_as_mismatch(self):
        http_seed.run_seed(self.adapter, self._env())
        import sqlite3
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE shifts SET status = 'done' "
                "WHERE worker = ? AND client = ? AND date = ?",
                (http_seed.WORKER_NAME, http_seed.CLIENT_NAME,
                 "2026-09-28"),
            )
            conn.commit()
        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)
        http_seed.login_as_admin(self.adapter, self._env())
        with self.assertRaises(http_seed.HttpSeedError) as cm:
            http_seed.verify_seed_landed(self.adapter)
        msg = str(cm.exception)
        self.assertIn("2026-09-28", msg)
        self.assertIn("status mismatch", msg)
        self.assertIn("'done'", msg)

    def test_cancelled_status_reported_as_mismatch(self):
        http_seed.run_seed(self.adapter, self._env())
        import sqlite3
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE shifts SET status = 'cancelled' "
                "WHERE worker = ? AND client = ? AND date = ?",
                (http_seed.WORKER_NAME, http_seed.CLIENT_NAME,
                 "2026-10-05"),
            )
            conn.commit()
        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)
        http_seed.login_as_admin(self.adapter, self._env())
        with self.assertRaises(http_seed.HttpSeedError) as cm:
            http_seed.verify_seed_landed(self.adapter)
        self.assertIn("'cancelled'", str(cm.exception))

    def test_wrong_time_reported_as_mismatch(self):
        http_seed.run_seed(self.adapter, self._env())
        import sqlite3
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE shifts SET time = '10:00-12:00' "
                "WHERE worker = ? AND client = ? AND date = ?",
                (http_seed.WORKER_NAME, http_seed.CLIENT_NAME,
                 "2026-10-12"),
            )
            conn.commit()
        self.flask_client = app_mod.app.test_client()
        self.adapter = FlaskClientAdapter(self.flask_client)
        http_seed.login_as_admin(self.adapter, self._env())
        with self.assertRaises(http_seed.HttpSeedError) as cm:
            http_seed.verify_seed_landed(self.adapter)
        self.assertIn("time mismatch", str(cm.exception))
        self.assertIn("'10:00-12:00'", str(cm.exception))


class EmptyUserinfoRefusalTests(unittest.TestCase):
    """P6: ``https://@host/`` must be refused even though the username
    component is the empty string (truthiness check would miss it)."""

    def test_empty_userinfo_still_refused(self):
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://@luxmann-planner-staging.onrender.com/"
            )

    def test_empty_password_still_refused(self):
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://user:@luxmann-planner-staging.onrender.com/"
            )

    def test_at_sign_in_netloc_without_valid_username_refused(self):
        # Edge case: a stray '@' can appear from a malformed URL even
        # when both username and password parse as empty.
        with self.assertRaises(http_seed.HttpSeedTargetRefused):
            http_seed._assert_url_is_staging(
                "https://:@luxmann-planner-staging.onrender.com/"
            )


class ConcurrentRunLockTests(unittest.TestCase):
    """P7: the file-based single-runner lock refuses a second runner."""

    def setUp(self):
        # Ensure a clean slate — the lockfile is global so a previous
        # test crash could leak it.
        try:
            os.unlink(http_seed.LOCKFILE_PATH)
        except FileNotFoundError:
            pass

    def test_second_concurrent_acquire_is_refused(self):
        with http_seed.single_runner_lock():
            with self.assertRaises(http_seed.HttpSeedConcurrentRun):
                with http_seed.single_runner_lock():
                    pass

    def test_lock_is_released_on_exit(self):
        with http_seed.single_runner_lock():
            pass
        # Must be acquirable again.
        with http_seed.single_runner_lock():
            pass

    def test_lock_is_released_even_on_exception(self):
        with self.assertRaises(ValueError):
            with http_seed.single_runner_lock():
                raise ValueError("boom")
        # Lockfile is cleaned up — a next run can acquire.
        with http_seed.single_runner_lock():
            pass


def tearDownModule():
    # Mirror the test_admin_bootstrap.py precaution: do not shutil.rmtree
    # TEST_STORAGE; other test modules sharing this process's `app` import
    # may still depend on it. OS tempdir cleanup handles it later.
    pass


if __name__ == "__main__":
    unittest.main()
