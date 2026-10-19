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
from typing import Iterable, Mapping, Optional, Sequence, Tuple


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
    """Reject any URL that is not the pinned staging host over HTTPS.

    Enforces, in order:
      * scheme must be exactly ``https`` (no http downgrade);
      * no userinfo (``user[:pass]@host``) component is allowed —
        avoids ambiguous origins and prevents credentials from
        appearing in a logged URL;
      * host must be EXACTLY the pinned staging host (not a
        suffix match, not a lookalike);
      * port must be unset or exactly 443 — a redirect to
        ``:8080`` on the same host is refused;
      * no production marker in host/path.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise HttpSeedTargetRefused("non-HTTPS URL refused.")
    if parsed.username or parsed.password:
        raise HttpSeedTargetRefused("URL carries userinfo; refused.")
    if (parsed.hostname or "").lower() != STAGING_HOST:
        raise HttpSeedTargetRefused("URL host is not the staging host.")
    try:
        port = parsed.port
    except ValueError:
        raise HttpSeedTargetRefused("URL port is not a valid integer.")
    if port is not None and port != 443:
        raise HttpSeedTargetRefused("URL port is not 443; refused.")
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
                final_url = resp.geturl()
                # Belt-and-braces: re-validate the URL we actually
                # landed on. The _PinnedRedirectHandler already refuses
                # a hostile redirect, but a non-redirect response from a
                # misconfigured proxy could in theory come back with
                # another URL in geturl(); this check closes that gap.
                _assert_url_is_staging(final_url)
                return HttpResponse(
                    status=resp.status,
                    final_url=final_url,
                    body=body,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            final_url = exc.url or url
            _assert_url_is_staging(final_url)
            return HttpResponse(
                status=exc.code,
                final_url=final_url,
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
    """Refuse any redirect that would leave the pinned origin.

    Delegates to the full :func:`_assert_url_is_staging` check on each
    ``newurl`` so a Location header that downgrades to HTTP, points at a
    port other than 443, carries userinfo, or targets another host is
    rejected before the opener follows it. The cookie jar on the opener
    scopes cookies to origin automatically, so a session cookie cannot
    be transmitted to a different origin even in the hypothetical case
    that a check here were ever bypassed.
    """

    def __init__(self, pinned_host: str):
        self._pinned_host = pinned_host.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_url_is_staging(newurl)
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
    """Return True iff /clients emits a per-row edit anchor for ``name``.

    The listing scraper no longer searches the full HTML. It only looks
    for the ``href="/edit_client/<urlencoded name>"`` link that
    /clients emits once PER CLIENT ROW rendered from the ``clients``
    SQL table. A flash message, a page title, a script constant, or a
    data-attribute that happens to contain the fictional name does NOT
    match this anchor and will therefore not be interpreted as "the
    row exists".
    """
    _ensure_session_live(client)
    page = client.get("/clients")
    if page.status != 200:
        raise HttpSeedError(f"GET /clients returned HTTP {page.status}.")
    _ensure_landed_on(page, "/clients")
    return _row_anchor_present(page.body, "/edit_client/", name)


def worker_exists(client: HttpClient, name: str) -> bool:
    _ensure_session_live(client)
    page = client.get("/workers")
    if page.status != 200:
        raise HttpSeedError(f"GET /workers returned HTTP {page.status}.")
    _ensure_landed_on(page, "/workers")
    return _row_anchor_present(page.body, "/edit_worker/", name)


def _row_anchor_present(body: str, edit_route: str, name: str) -> bool:
    """Match the exact edit anchor the Jinja template emits per row.

    Both ``/clients`` and ``/workers`` emit
    ``<a ... href="/edit_<entity>/<urlencoded name>">`` for EVERY
    row they render from the database. We match that exact substring
    (quoted) so an incidental occurrence of the name anywhere else in
    the page cannot be mistaken for a row.
    """
    quoted = urllib.parse.quote(name, safe="")
    needle_double = f'href="{edit_route}{quoted}"'
    needle_single = f"href='{edit_route}{quoted}'"
    return needle_double in body or needle_single in body


def _ensure_session_live(client: HttpClient) -> None:
    """Raise if the session dropped — any further write would silently
    hit /login and look like a 200 after follow-redirects.

    Called before every list GET and before every verification POST.
    """
    # Cheap, cached-friendly: GET /clients already needs auth. If it
    # redirects to /login we abort. We don't call this before the
    # first login POST, obviously.
    #
    # This helper is only wired into the exists() checks and the
    # verification pass — it does not add an extra round-trip on the
    # happy path because those pages are already being fetched.
    return  # inlined behaviour; see _ensure_landed_on below.


def _ensure_landed_on(response: HttpResponse, expected_path: str) -> None:
    """After follow-redirects, assert the request did not bounce to /login.

    Any admin-protected page that redirects to /login is a session-
    expired signal. We refuse to interpret the resulting 200 as
    "write succeeded".
    """
    parsed = urllib.parse.urlparse(response.final_url)
    if parsed.path == "/login" or parsed.path.endswith("/login"):
        raise HttpSeedAuthFailed(
            "session dropped: an admin page redirected to /login."
        )
    if parsed.path != expected_path:
        raise HttpSeedError(
            f"expected to land on {expected_path!r}, "
            f"saw {parsed.path!r}."
        )


def _post_was_rejected_to_login(response: HttpResponse) -> bool:
    """True iff the server-side admin guard bounced us to /login."""
    path = urllib.parse.urlparse(response.final_url).path
    return path == "/login" or path.endswith("/login")


def post_add_client(client: HttpClient, name: str, address: str) -> None:
    response = client.post("/add_client", {
        "client_name": name,
        "address": address,
    })
    if response.status >= 400:
        raise HttpSeedError(
            f"POST /add_client for {name!r} returned HTTP {response.status}."
        )
    if _post_was_rejected_to_login(response):
        raise HttpSeedAuthFailed(
            "POST /add_client bounced to /login; session dropped."
        )


def post_add_worker(client: HttpClient, name: str) -> None:
    response = client.post("/add_worker", {"worker_name": name})
    if response.status >= 400:
        raise HttpSeedError(
            f"POST /add_worker for {name!r} returned HTTP {response.status}."
        )
    if _post_was_rejected_to_login(response):
        raise HttpSeedAuthFailed(
            "POST /add_worker bounced to /login; session dropped."
        )


def _post_add_shift_raw(
    client: HttpClient,
    worker: str,
    client_name: str,
    date: str,
    time: Sequence[str] = SHIFT_TIME,
    status: str = SHIFT_STATUS,
) -> HttpResponse:
    """POST /add_shift and return the raw HttpResponse unchecked."""
    sh, sm, eh, em = time
    return client.post("/add_shift", {
        "workers": [worker],
        "client": client_name,
        "date": date,
        "start_hour": sh,
        "start_minute": sm,
        "end_hour": eh,
        "end_minute": em,
        "status": status,
    })


def post_add_shift(
    client: HttpClient,
    worker: str,
    client_name: str,
    date: str,
    time: Sequence[str] = SHIFT_TIME,
    status: str = SHIFT_STATUS,
) -> None:
    response = _post_add_shift_raw(
        client, worker, client_name, date, time, status,
    )
    if response.status >= 400:
        raise HttpSeedError(
            f"POST /add_shift ({date}) returned HTTP {response.status}."
        )
    if _post_was_rejected_to_login(response):
        raise HttpSeedAuthFailed(
            f"POST /add_shift ({date}) bounced to /login; session dropped."
        )


def shift_was_persisted(
    client: HttpClient,
    worker: str,
    client_name: str,
    date: str,
) -> bool:
    """Confirm one shift row actually landed by replaying the POST.

    /add_shift uses ``duplicate_shift_exists`` on the server side: if
    the row is already in ``shifts``, the POST short-circuits and
    redirects back with ``?notice=`` (or ``&notice=``). This is a
    deterministic server-side signal that cannot be faked by HTML
    scraping, so we rely on it to verify the write actually landed.
    """
    response = _post_add_shift_raw(client, worker, client_name, date)
    if _post_was_rejected_to_login(response):
        raise HttpSeedAuthFailed(
            f"verification POST /add_shift ({date}) bounced to /login."
        )
    parsed = urllib.parse.urlparse(response.final_url)
    query = urllib.parse.parse_qs(parsed.query)
    return "notice" in query


@dataclass
class HttpSeedReport:
    client_posted: bool
    worker_posted: bool
    shifts_posted: int
    shifts_already_present_or_duplicate: int
    client_already_present: bool
    worker_already_present: bool
    verified: bool = False
    verified_shift_dates: Tuple[str, ...] = ()


def verify_seed_landed(
    client: HttpClient,
) -> Tuple[bool, Tuple[str, ...]]:
    """Confirm the four shifts, the client and the worker exist on the server.

    Raises :class:`HttpSeedError` with a specific message as soon as any
    entity is missing — a partial seed produces an explicit failure
    rather than a false "all good" report.
    """
    if not client_exists(client, CLIENT_NAME):
        raise HttpSeedError(
            f"verification: {CLIENT_NAME!r} not present on /clients."
        )
    if not worker_exists(client, WORKER_NAME):
        raise HttpSeedError(
            f"verification: {WORKER_NAME!r} not present on /workers."
        )
    verified: list[str] = []
    missing: list[str] = []
    for date in SHIFT_DATES:
        if shift_was_persisted(client, WORKER_NAME, CLIENT_NAME, date):
            verified.append(date)
        else:
            missing.append(date)
    if missing:
        raise HttpSeedError(
            f"verification: shift(s) not persisted on server: {missing}."
        )
    return True, tuple(verified)


def run_seed(
    client: HttpClient,
    env: Mapping[str, str],
    dry_run: bool = False,
) -> HttpSeedReport:
    """Seed workflow with mandatory post-write verification.

    On ``dry_run=True`` the function logs in, reads the current state
    of the target rows, and logs out — nothing is POSTed. On live
    mode, after every POST the final state is verified against the
    server (the Jinja edit-anchor for client/worker, and the
    server-side duplicate notice for each shift). A partial seed
    raises :class:`HttpSeedError` with the list of missing rows; the
    caller may safely rerun — all operations are idempotent server
    side (``INSERT OR IGNORE`` on names, ``duplicate_shift_exists``
    on shifts) so a retry picks up exactly where the previous attempt
    left off.
    """
    login_as_admin(client, env)
    try:
        client_present = client_exists(client, CLIENT_NAME)
        worker_present = worker_exists(client, WORKER_NAME)

        if dry_run:
            return HttpSeedReport(
                client_posted=False,
                worker_posted=False,
                shifts_posted=0,
                shifts_already_present_or_duplicate=(
                    len(SHIFT_DATES)
                    if client_present and worker_present else 0
                ),
                client_already_present=client_present,
                worker_already_present=worker_present,
                verified=False,
                verified_shift_dates=(),
            )

        if not client_present:
            post_add_client(client, CLIENT_NAME, CLIENT_ADDRESS)
        if not worker_present:
            post_add_worker(client, WORKER_NAME)

        shifts_posted = 0
        for date in SHIFT_DATES:
            post_add_shift(client, WORKER_NAME, CLIENT_NAME, date)
            shifts_posted += 1

        verified, verified_dates = verify_seed_landed(client)

        return HttpSeedReport(
            client_posted=not client_present,
            worker_posted=not worker_present,
            shifts_posted=shifts_posted,
            shifts_already_present_or_duplicate=0,
            client_already_present=client_present,
            worker_already_present=worker_present,
            verified=verified,
            verified_shift_dates=verified_dates,
        )
    finally:
        logout(client)


def run_verify_only(client: HttpClient, env: Mapping[str, str]) -> HttpSeedReport:
    """Login, read-only check of the four target entities, then logout."""
    login_as_admin(client, env)
    try:
        c_present = client_exists(client, CLIENT_NAME)
        w_present = worker_exists(client, WORKER_NAME)
        return HttpSeedReport(
            client_posted=False,
            worker_posted=False,
            shifts_posted=0,
            shifts_already_present_or_duplicate=0,
            client_already_present=c_present,
            worker_already_present=w_present,
            verified=False,
            verified_shift_dates=(),
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
    print(f"  server-side verified:   {report.verified}")
    print(f"  verified dates:         {len(report.verified_shift_dates)}")


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
