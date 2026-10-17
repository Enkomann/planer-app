"""Tests for the admin bootstrap credential resolver + init_db seed path.

These tests must stay isolated from the normal auto-planning suite because
they manipulate APP_ENV and ADMIN_BOOTSTRAP_* globals that would otherwise
leak between modules. The suite:

1. unit-tests the pure resolver (`_bootstrap_admin_credentials`) across the
   combinations of APP_ENV and ADMIN_BOOTSTRAP_PASSWORD;
2. drives `init_db()` against a freshly-created temporary SQLite file so a
   fresh install exercises the whole seed branch without touching the
   developer's own database.

Nothing in this file reads or writes PostgreSQL.
"""

import logging
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock


# Keep the import side effects (which run `init_db()` once at module load
# against the normal developer database) isolated to one dedicated storage
# dir, exactly like tests/test_auto_planning.py already does.
TEST_TEMP_ROOT = os.path.join(os.path.dirname(__file__), ".tmp_admin")
os.makedirs(TEST_TEMP_ROOT, exist_ok=True)
TEST_STORAGE = tempfile.mkdtemp(
    prefix="luxmann-admin-bootstrap-", dir=TEST_TEMP_ROOT,
)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("APP_ENV", None)
os.environ.pop("ADMIN_BOOTSTRAP_PASSWORD", None)
os.environ.pop("ADMIN_BOOTSTRAP_USERNAME", None)
os.environ["STORAGE_ROOT"] = TEST_STORAGE
os.environ.setdefault("SECRET_KEY", "admin-bootstrap-tests-only")

import app  # noqa: E402


def tearDownModule():
    # Intentionally no shutil.rmtree(TEST_STORAGE): other test modules
    # imported this process's `app` and still point at this storage dir
    # through app.SQLITE_PATH. Wiping it here would delete their only
    # DB and make unrelated tests fail with "no such table". The OS
    # tempdir cleanup takes care of the directory later.
    pass


class BootstrapResolverTests(unittest.TestCase):
    """Pure-function tests on the env-driven credential resolver."""

    def _clear(self):
        for key in (
            "ADMIN_BOOTSTRAP_PASSWORD", "ADMIN_BOOTSTRAP_USERNAME", "APP_ENV",
        ):
            os.environ.pop(key, None)

    def setUp(self):
        self._clear()
        self.addCleanup(self._clear)

    def test_env_password_is_the_source_outside_staging(self):
        os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "env-strong-secret-123"
        username, password, source = app._bootstrap_admin_credentials()
        self.assertEqual(username, "admin")
        self.assertEqual(password, "env-strong-secret-123")
        self.assertEqual(source, "env")

    def test_env_password_wins_in_staging_mode_too(self):
        os.environ["APP_ENV"] = "staging"
        os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "env-strong-secret-123"
        username, password, source = app._bootstrap_admin_credentials()
        self.assertEqual(username, "admin")
        self.assertEqual(password, "env-strong-secret-123")
        self.assertEqual(source, "env")

    def test_custom_username_env_is_honoured_when_non_empty(self):
        os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "env-strong-secret-123"
        os.environ["ADMIN_BOOTSTRAP_USERNAME"] = "  patron  "
        username, _, source = app._bootstrap_admin_credentials()
        self.assertEqual(username, "patron")
        self.assertEqual(source, "env")

    def test_staging_without_password_refuses_to_boot(self):
        os.environ["APP_ENV"] = "staging"
        with self.assertRaises(app.BootstrapAdminMissingSecret) as ctx:
            app._bootstrap_admin_credentials()
        message = str(ctx.exception)
        self.assertIn("ADMIN_BOOTSTRAP_PASSWORD", message)
        # The error message MUST NOT contain the historical plaintext
        # fallback — the whole point of staging mode is to never hint
        # at the legacy value.
        self.assertNotIn("admin123", message)

    def test_staging_with_empty_password_also_refuses(self):
        os.environ["APP_ENV"] = "staging"
        os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = ""
        with self.assertRaises(app.BootstrapAdminMissingSecret):
            app._bootstrap_admin_credentials()

    def test_staging_detector_is_case_insensitive_and_strips(self):
        os.environ["APP_ENV"] = "  STAGING  "
        with self.assertRaises(app.BootstrapAdminMissingSecret):
            app._bootstrap_admin_credentials()
        os.environ["APP_ENV"] = "production"
        os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = ""
        # production path falls through to legacy with a warning
        username, password, source = app._bootstrap_admin_credentials()
        self.assertEqual(username, "admin")
        self.assertEqual(password, app.BOOTSTRAP_LEGACY_PASSWORD)
        self.assertEqual(source, "legacy")

    def test_dev_fallback_logs_warning_without_the_password(self):
        with self.assertLogs(app.app.logger, level="WARNING") as captured:
            username, password, source = app._bootstrap_admin_credentials()
        self.assertEqual(source, "legacy")
        self.assertEqual(password, app.BOOTSTRAP_LEGACY_PASSWORD)
        # The warning line must NEVER include the plaintext password.
        warning_text = "\n".join(captured.output)
        self.assertNotIn(app.BOOTSTRAP_LEGACY_PASSWORD, warning_text)
        # It should still name the env var the operator needs to set.
        self.assertIn("ADMIN_BOOTSTRAP_PASSWORD", warning_text)


class BootstrapInitDbTests(unittest.TestCase):
    """init_db() end-to-end on a brand-new SQLite file (fresh install)."""

    def _fresh_sqlite_path(self):
        fd, path = tempfile.mkstemp(
            prefix="bootstrap-", suffix=".sqlite", dir=TEST_STORAGE,
        )
        os.close(fd)
        os.unlink(path)  # init_db must find no existing DB
        # Windows holds the sqlite file open for a short grace after
        # the last connection closes. Leave physical cleanup to
        # tearDownModule's rmtree(ignore_errors=True) rather than
        # unlinking per-test, which otherwise flakes with WinError 32.
        return path

    def _run_init_db_on(self, sqlite_path, env=None):
        env = env or {}
        preserved = {}
        for key in (
            "APP_ENV", "ADMIN_BOOTSTRAP_PASSWORD", "ADMIN_BOOTSTRAP_USERNAME",
        ):
            preserved[key] = os.environ.get(key)
        try:
            for key in (
                "APP_ENV", "ADMIN_BOOTSTRAP_PASSWORD",
                "ADMIN_BOOTSTRAP_USERNAME",
            ):
                os.environ.pop(key, None)
            for key, value in env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            with mock.patch.object(app, "SQLITE_PATH", sqlite_path):
                app.init_db()
        finally:
            for key, previous in preserved.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous

    def _fetch_admin(self, sqlite_path, username="admin"):
        with sqlite3.connect(sqlite_path) as conn:
            return conn.execute(
                "SELECT username, password, role FROM users "
                "WHERE username = ?", (username,),
            ).fetchone()

    def test_fresh_install_in_staging_without_secret_refuses_to_boot(self):
        path = self._fresh_sqlite_path()
        with self.assertRaises(app.BootstrapAdminMissingSecret):
            self._run_init_db_on(path, env={"APP_ENV": "staging"})
        # The failing boot must not have persisted a legacy admin row.
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT username FROM users WHERE role='admin'"
            ).fetchall()
        self.assertEqual(rows, [])

    def test_fresh_install_in_staging_with_secret_hashes_and_seeds_admin(self):
        path = self._fresh_sqlite_path()
        self._run_init_db_on(path, env={
            "APP_ENV": "staging",
            "ADMIN_BOOTSTRAP_PASSWORD": "env-strong-secret-456",
        })
        row = self._fetch_admin(path)
        self.assertIsNotNone(row)
        username, password_field, role = row
        self.assertEqual(username, "admin")
        self.assertEqual(role, "admin")
        # The password column must hold a hash — never the plaintext.
        self.assertTrue(app.is_password_hash(password_field))
        self.assertNotIn("env-strong-secret-456", password_field)
        self.assertTrue(
            app.verify_password(password_field, "env-strong-secret-456"),
        )
        # Legacy pair must not open the account on a staging seed.
        self.assertFalse(app.verify_password(password_field, "admin123"))

    def test_fresh_install_in_staging_with_custom_username_env(self):
        path = self._fresh_sqlite_path()
        self._run_init_db_on(path, env={
            "APP_ENV": "staging",
            "ADMIN_BOOTSTRAP_PASSWORD": "env-strong-secret-789",
            "ADMIN_BOOTSTRAP_USERNAME": "patron",
        })
        row = self._fetch_admin(path, username="patron")
        self.assertIsNotNone(row)
        username, password_field, role = row
        self.assertEqual(username, "patron")
        self.assertEqual(role, "admin")
        self.assertTrue(app.verify_password(
            password_field, "env-strong-secret-789",
        ))
        # No legacy "admin" row should be created on top of the custom one.
        self.assertIsNone(self._fetch_admin(path, username="admin"))

    def test_existing_admin_row_is_never_rewritten_by_bootstrap(self):
        """Simulates a production install: admin exists before init_db() runs."""
        path = self._fresh_sqlite_path()
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password TEXT,
                    role TEXT
                );
            """)
            production_hash = app.hash_password("prod-strong-secret-321")
            conn.execute(
                "INSERT INTO users (username, password, role) "
                "VALUES ('admin', ?, 'admin')",
                (production_hash,),
            )
        # Staging + no secret would normally raise, but with an admin
        # already present the branch is skipped entirely → safe boot.
        self._run_init_db_on(path, env={"APP_ENV": "staging"})
        row = self._fetch_admin(path)
        self.assertIsNotNone(row)
        _, password_field, role = row
        self.assertEqual(role, "admin")
        self.assertEqual(password_field, production_hash)
        self.assertTrue(app.verify_password(
            password_field, "prod-strong-secret-321",
        ))
        self.assertFalse(app.verify_password(password_field, "admin123"))

    def test_fresh_install_outside_staging_falls_back_to_dev_default(self):
        path = self._fresh_sqlite_path()
        with self.assertLogs(app.app.logger, level="WARNING"):
            self._run_init_db_on(path)
        row = self._fetch_admin(path)
        self.assertIsNotNone(row)
        _, password_field, _ = row
        self.assertTrue(
            app.verify_password(password_field, app.BOOTSTRAP_LEGACY_PASSWORD),
        )

    def test_init_db_info_log_omits_plaintext_password(self):
        path = self._fresh_sqlite_path()
        with self.assertLogs(app.app.logger, level="INFO") as captured:
            self._run_init_db_on(path, env={
                "APP_ENV": "staging",
                "ADMIN_BOOTSTRAP_PASSWORD": "do-not-log-this-value-999",
            })
        combined = "\n".join(captured.output)
        self.assertIn("admin", combined)
        self.assertIn("source=env", combined)
        self.assertNotIn("do-not-log-this-value-999", combined)
        self.assertNotIn("admin123", combined)


if __name__ == "__main__":
    unittest.main()
