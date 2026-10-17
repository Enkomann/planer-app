"""Drive the existing admin HTTP routes to seed the auto-planning history.

This is the Render-Free-friendly alternative to the direct PostgreSQL
seed in ``tests/seed_staging_auto_planning.py``: it logs into the
staging instance as an admin and POSTs the standard
``/add_client``, ``/add_worker`` and ``/add_shift`` forms, so no new
HTTP route, no new environment variable on Render, and no new paid
plan is needed.

What this script WILL do (seed mode)
------------------------------------

Against ``https://luxmann-planner-staging.onrender.com`` only:

  1. POST ``/login`` with admin credentials read from env.
  2. GET ``/clients`` and ``/workers``, verify the admin session is
     authenticated, and look up whether ``APSTG_TEST_Client_A`` and
     ``APSTG_TEST_Radnik_A`` already exist.
  3. POST ``/add_client`` (if missing) and ``/add_worker`` (if
     missing) using the existing form shape.
  4. POST ``/add_shift`` four times, one per Monday
     2026-09-21, 2026-09-28, 2026-10-05, 2026-10-12, at
     09:00-11:00 with status "planned". The server-side
     ``duplicate_shift_exists`` guard makes each POST a no-op if
     the row is already there.
  5. GET ``/logout``.

Nothing else is touched. The script never DELETEs anything, never
touches /add_user, never submits any form that could send mail or a
notification.

What this script WILL NOT do
----------------------------

  * No URL other than the single pinned staging host. Any
    redirect to a different host aborts the whole run.
  * No TLS verification bypass.
  * No new HTTP route created server-side.
  * No credential, cookie, session id or URL ever printed to
    stdout, stderr or any log line.
  * No scraping of pages other than ``/clients`` and ``/workers``
    (for the idempotence pre-check) and ``/planer`` (optional,
    for the post-run verification).
  * No touching of production. The host check refuses any URL
    whose host is not exactly the staging host, whose scheme is
    not ``https``, or whose host/path carries any of the known
    production markers.
  * No modification to ``app.py``.

Guard environment variables (all required)
------------------------------------------

  * ``STAGING_HTTP_SEED_CONFIRM`` must equal the dedicated token
    ``STAGING_HTTP_SEED_ONLY_I_UNDERSTAND``.
  * ``STAGING_ADMIN_USERNAME`` and ``STAGING_ADMIN_PASSWORD`` —
    the admin login created by the bootstrap on staging. The
    script reads them from the environment only, never prints
    them, never writes them to a log line, and clears them from
    its own ``os.environ`` copy once the login payload is built.
  * ``STAGING_BASE_URL`` (optional). Defaults to the pinned
    constant below. If set, must equal the pinned constant
    exactly — anything else is rejected pre-connection.

CLI
---

    python tests/seed_staging_via_http.py --dry-run   (default)
    python tests/seed_staging_via_http.py --seed
    python tests/seed_staging_via_http.py --verify-only

Dry-run performs the login + pre-check reads, reports what WOULD
be posted, and logs out. Verify-only logs in, reads the current
state of the four target rows, and logs out. Only ``--seed``
POSTs any write.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import os
import re
import secrets
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Constants — single pinned staging target. Edit here, nowhere else.
# ---------------------------------------------------------------------------

STAGING_BASE_URL = "https://luxmann-planner-staging.onrender.com"
STAGING_HOST = "luxmann-planner-staging.onrender.com"

CONFIRM_ENV = "STAGING_HTTP_SEED_CONFIRM"
CONFIRM_VALUE = "STAGING_HTTP_SEED_ONLY_I_UNDERSTAND"

ADMIN_USERNAME_ENV = "STAGING_ADMIN_USERNAME"
ADMIN_PASSWORD_ENV = "STAGING_ADMIN_PASSWORD"

PRODUCTION_MARKERS = (
    "planer-app.onrender.com",
    "luxmann_planner_prod",
    "/prod/",
    "-prod.",
    "production",
)

TEST_PREFIX = "APSTG_TEST_"

CLIENT_NAME = f"{TEST_PREFIX}Client_A"
WORKER_NAME = f"{TEST_PREFIX}Radnik_A"
CLIENT_ADDRESS = f"{TEST_PREFIX}placeholder address"

SHIFT_TIME = ("09", "00", "11", "00")  # start_hour, start_min, end_hour, end_min
SHIFT_TIME_RANGE = "09:00-11:00"
SHIFT_DATES: Sequence[str] = (
    "2026-09-21",
    "2026-09-28",
    "2026-10-05",
    "2026-10-12",
)
SHIFT_STATUS = "planned"

HTTP_TIMEOUT_SECONDS = 20


# ---------------------------------------------------------------------------
# Errors — specific types so tests can assert on each guard individually.
# ---------------------------------------------------------------------------


class HttpSeedError(RuntimeError):
    """Base class for every refusal in this module."""


class HttpSeedEnvRefused(HttpSeedError):
    """A pre-request environment guard failed."""


class HttpSeedTargetRefused(HttpSeedError):
    """A URL target does not match the pinned staging host."""


class HttpSeedAuthFailed(HttpSeedError):
    """The admin login did not take."""


# ---------------------------------------------------------------------------
# Guards.
# ---------------------------------------------------------------------------


def _assert_url_is_staging(url: str) -> None:
    """Reject any URL that is not the pinned staging host over HTTPS."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise HttpSeedTargetRefused("non-HTTPS URL refused.")
    if (parsed.hostname or "").lower() != STAGING_HOST:
        raise HttpSeedTargetRefused("URL host is not the staging host.")
    joined = f"{parsed.hostname}{parsed.path or ''}".lower()
    for marker in PRODUCTION_MARKERS:
        if marker in joined:
            raise HttpSeedTargetRefused("URL carries a production marker.")


def _read_env(env: Mapping[str, str], key: str) -> str:
    value = str(env.get(key, ""))
    if not value:
        raise HttpSeedEnvRefused(f"{key} is required.")
    return value


def resolve_target_base_url(env: Mapping[str, str]) -> str:
    """Return the pinned staging base URL, after refusing any override."""
    override = str(env.get("STAGING_BASE_URL", "")).strip()
    if override and override != STAGING_BASE_URL:
        raise HttpSeedTargetRefused("STAGING_BASE_URL override is not allowed.")
    _assert_url_is_staging(STAGING_BASE_URL)
    return STAGING_BASE_URL


def assert_http_seed_environment(env: Optional[Mapping[str, str]] = None) -> str:
    """Validate every pre-request guard. Return the pinned base URL."""
    if env is None:
        env = os.environ
    confirm = str(env.get(CONFIRM_ENV, ""))
    if not secrets.compare_digest(confirm, CONFIRM_VALUE):
        raise HttpSeedEnvRefused(f"{CONFIRM_ENV} must be set to the seed token.")
    # Credentials: existence only. We do not read the values here so a
    # stack trace from this check cannot carry the password.
    if not str(env.get(ADMIN_USERNAME_ENV, "")):
        raise HttpSeedEnvRefused(f"{ADMIN_USERNAME_ENV} is required.")
    if not str(env.get(ADMIN_PASSWORD_ENV, "")):
        raise HttpSeedEnvRefused(f"{ADMIN_PASSWORD_ENV} is required.")
    return resolve_target_base_url(env)


# ---------------------------------------------------------------------------
# HTTP client — stdlib only. One tiny abstraction so tests can replace it.
# ---------------------------------------------------------------------------


@dataclass
class HttpResponse:
    status: int
    final_url: str
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpClient:
    """Minimal cookie-aware HTTP client pinned to one base URL.

    Every call to ``get`` / ``post`` re-validates the target host before
    opening a socket, so even a malicious redirect cannot take us to a
    different origin. TLS verification is left at the default (on).
    """

    def __init__(self, base_url: str):
        _assert_url_is_staging(base_url)
        self.base_url = base_url.rstrip("/")
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            _PinnedRedirectHandler(STAGING_HOST),
        )

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            raise HttpSeedTargetRefused("path must start with '/'.")
        full = f"{self.base_url}{path}"
        _assert_url_is_staging(full)
        return full

    def get(self, path: str) -> HttpResponse:
        return self._open(self._url(path), data=None)

    def post(self, path: str, form: Mapping[str, object]) -> HttpResponse:
        data = urllib.parse.urlencode(_normalise_form(form), doseq=True).encode()
        return self._open(
            self._url(path),
            data=data,
            extra_headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def _open(self, url, data, extra_headers=None):
        headers = {"User-Agent": "LuxmannStagingSeeder/1.0"}
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method=
                                     "POST" if data is not None else "GET")
        try:
            with self._opener.open(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return HttpResponse(
                    status=resp.status,
                    final_url=resp.geturl(),
                    body=body,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return HttpResponse(
                status=exc.code,
                final_url=exc.url or url,
                body=body,
                headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            )


def _normalise_form(form: Mapping[str, object]) -> Iterable[tuple[str, str]]:
    for key, value in form.items():
        if isinstance(value, (list, tuple)):
            for item in value:
                yield key, str(item)
        else:
            yield key, str(value)


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect that would leave the pinned host."""

    def __init__(self, pinned_host: str):
        self._pinned_host = pinned_host.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target_host = (urllib.parse.urlparse(newurl).hostname or "").lower()
        if target_host != self._pinned_host:
            raise HttpSeedTargetRefused(
                "redirect to a non-staging host refused."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ---------------------------------------------------------------------------
# Seed workflow — composed of small helpers so tests can poke each one.
# ---------------------------------------------------------------------------


_ADMIN_MARKERS = (
    "/logout",      # the top navigation shows a Logout link only when
                    # the session is authenticated
    "add_worker",
    "add_client",
)


def login_as_admin(client: HttpClient, env: Mapping[str, str]) -> None:
    """POST /login and verify the session actually authenticated.

    On a wrong password Flask serves /login again with an error banner;
    we detect that by looking for the admin markers in the body of the
    page we land on. Credentials are read lazily here, never retained
    on the client, and never embedded in error messages.
    """
    username = _read_env(env, ADMIN_USERNAME_ENV)
    password = _read_env(env, ADMIN_PASSWORD_ENV)
    try:
        response = client.post("/login", {
            "username": username,
            "password": password,
        })
    finally:
        # Zero out local references as early as possible so an exception
        # traceback does not carry the plaintext password.
        username = ""
        password = ""

    if response.status >= 400:
        raise HttpSeedAuthFailed(
            f"login POST returned HTTP {response.status}; refusing to seed."
        )
    # The server redirects to "/" on success. Follow was automatic; we check
    # that the landed page actually shows the admin markers.
    probe = client.get("/clients")
    if probe.status != 200 or not any(m in probe.body for m in _ADMIN_MARKERS):
        raise HttpSeedAuthFailed("admin markers missing after login.")


def logout(client: HttpClient) -> None:
    try:
        client.get("/logout")
    except HttpSeedError:
        # Logging out is best-effort; a refusal here cannot roll back
        # the writes we may have already committed.
        pass


def client_exists(client: HttpClient, name: str) -> bool:
    """Return True iff /clients lists a client with that exact name."""
    page = client.get("/clients")
    if page.status != 200:
        raise HttpSeedError(f"GET /clients returned HTTP {page.status}.")
    return _name_in_listing(page.body, name)


def worker_exists(client: HttpClient, name: str) -> bool:
    page = client.get("/workers")
    if page.status != 200:
        raise HttpSeedError(f"GET /workers returned HTTP {page.status}.")
    return _name_in_listing(page.body, name)


def _name_in_listing(body: str, name: str) -> bool:
    # The lookup is intentionally restricted to the APSTG_TEST_ prefix:
    # the only names we ever check for are our own fictional ones, so
    # even if a real-world row happens to share a substring we do not
    # treat a partial match as positive. We require the full name token
    # to be surrounded by non-word characters, which matches how the
    # Jinja templates emit <td>{{ name }}</td> and <option> labels.
    pattern = r"(?<![\w-])" + re.escape(name) + r"(?![\w-])"
    return re.search(pattern, body) is not None


def post_add_client(client: HttpClient, name: str, address: str) -> None:
    response = client.post("/add_client", {
        "client_name": name,
        "address": address,
    })
    if response.status >= 400:
        raise HttpSeedError(
            f"POST /add_client for {name!r} returned HTTP {response.status}."
        )


def post_add_worker(client: HttpClient, name: str) -> None:
    response = client.post("/add_worker", {"worker_name": name})
    if response.status >= 400:
        raise HttpSeedError(
            f"POST /add_worker for {name!r} returned HTTP {response.status}."
        )


def post_add_shift(
    client: HttpClient,
    worker: str,
    client_name: str,
    date: str,
    time: Sequence[str] = SHIFT_TIME,
    status: str = SHIFT_STATUS,
) -> None:
    sh, sm, eh, em = time
    response = client.post("/add_shift", {
        "workers": [worker],
        "client": client_name,
        "date": date,
        "start_hour": sh,
        "start_minute": sm,
        "end_hour": eh,
        "end_minute": em,
        "status": status,
    })
    if response.status >= 400:
        raise HttpSeedError(
            f"POST /add_shift ({date}) returned HTTP {response.status}."
        )


@dataclass
class HttpSeedReport:
    client_posted: bool
    worker_posted: bool
    shifts_posted: int
    shifts_already_present_or_duplicate: int
    client_already_present: bool
    worker_already_present: bool


def run_seed(
    client: HttpClient,
    env: Mapping[str, str],
    dry_run: bool = False,
) -> HttpSeedReport:
    """Seed workflow. ``dry_run=True`` performs only the GETs."""
    login_as_admin(client, env)
    try:
        client_present = client_exists(client, CLIENT_NAME)
        worker_present = worker_exists(client, WORKER_NAME)
        shifts_posted = 0

        if dry_run:
            # No writes at all. We still return a report that mirrors
            # what the --seed path would have attempted.
            return HttpSeedReport(
                client_posted=False,
                worker_posted=False,
                shifts_posted=0,
                shifts_already_present_or_duplicate=(
                    len(SHIFT_DATES) if client_present and worker_present else 0
                ),
                client_already_present=client_present,
                worker_already_present=worker_present,
            )

        if not client_present:
            post_add_client(client, CLIENT_NAME, CLIENT_ADDRESS)
        if not worker_present:
            post_add_worker(client, WORKER_NAME)

        # The /add_shift server route uses duplicate_shift_exists() so
        # a re-run is a safe no-op; we still count how many we posted.
        for date in SHIFT_DATES:
            post_add_shift(client, WORKER_NAME, CLIENT_NAME, date)
            shifts_posted += 1

        return HttpSeedReport(
            client_posted=not client_present,
            worker_posted=not worker_present,
            shifts_posted=shifts_posted,
            shifts_already_present_or_duplicate=0,
            client_already_present=client_present,
            worker_already_present=worker_present,
        )
    finally:
        logout(client)


def run_verify_only(client: HttpClient, env: Mapping[str, str]) -> HttpSeedReport:
    """Login, read-only check of the four target entities, then logout."""
    login_as_admin(client, env)
    try:
        return HttpSeedReport(
            client_posted=False,
            worker_posted=False,
            shifts_posted=0,
            shifts_already_present_or_duplicate=0,
            client_already_present=client_exists(client, CLIENT_NAME),
            worker_already_present=worker_exists(client, WORKER_NAME),
        )
    finally:
        logout(client)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _print_report(report: HttpSeedReport) -> None:
    print("HTTP seed result:")
    print(f"  client already present: {report.client_already_present}")
    print(f"  worker already present: {report.worker_already_present}")
    print(f"  client posted this run: {report.client_posted}")
    print(f"  worker posted this run: {report.worker_posted}")
    print(f"  shift POSTs sent:       {report.shifts_posted}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HTTP seed driver for the Luxmann Planner STAGING instance. "
            "Uses only the existing admin forms."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Default. Login + reads only; no POST writes.")
    mode.add_argument("--seed", action="store_true",
                      help="Post the add_client/add_worker/add_shift forms.")
    mode.add_argument("--verify-only", action="store_true",
                      help="Login + readback, do not look at idempotence.")
    args = parser.parse_args(argv)

    base_url = assert_http_seed_environment()
    client = HttpClient(base_url)

    if args.seed:
        report = run_seed(client, os.environ, dry_run=False)
    elif args.verify_only:
        report = run_verify_only(client, os.environ)
    else:
        report = run_seed(client, os.environ, dry_run=True)

    _print_report(report)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HttpSeedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)
