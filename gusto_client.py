"""Gusto contractor timesheet access through a real Chrome session.

Gusto's public API is partner-only and its CLI/MCP need a company admin, so a
contractor cannot log their own hours through either. What a contractor *can*
do is use the web app, which talks to a GraphQL endpoint. This module drives
that endpoint from inside a logged-in page:

- A dedicated Chrome profile lives under Application Support. Chrome runs as
  its own process (started on demand with a localhost-only DevTools port) and
  each run attaches to it and detaches afterwards. The window is never closed
  by the app: the user can take over whatever a run was doing, and while the
  window stays open Gusto's session cookie (which Chrome drops on quit)
  survives between runs.
- Gusto logs in with a passkey. On the login page the app may click the
  user's remembered account; the passkey itself is approved by the user
  (Touch ID). The app never enters a password and never approves a passkey.
- Always a visible ("headed") Chrome window, never headless. Gusto sits
  behind a Cloudflare bot check that stops headless Chrome outright, and a
  real window is also the safer default: anything the automation does is on
  screen. If Cloudflare ever does challenge the window, nothing here tries to
  get past it; the run fails and the user finishes it by hand.
- Requests are issued with `fetch` from the Gusto page itself, so the session
  cookies never leave the browser. Gusto's GraphQL gateway also wants a CSRF
  token and a role id header; those are copied at runtime from the first
  request Gusto's own client sends, never stored or logged.
- When the session has expired and the user does not finish the passkey
  login in time, `GustoLoginRequired` is raised and callers surface it as
  "log in to Gusto". The window stays open on the login page.

The GraphQL operations below are our own minimal documents against Gusto's
schema (`addHours`, `tracker.groupedShiftsWithTotals`), discovered from the
web client. They are not a published API and can change without notice.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager

from preferences import APP_SUPPORT_DIR

PROFILE_DIR = APP_SUPPORT_DIR / "gusto-browser"
APP_ORIGIN = "https://app.gusto.com"
GRAPHQL_URL = "https://graphql.app.gusto.com/"
LOGIN_HOST = "login.gusto.com"

CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# DevTools port for the app's own Chrome profile. Bound to 127.0.0.1 only.
DEBUG_PORT = 9333
CDP_URL = f"http://127.0.0.1:{DEBUG_PORT}"

# Headers Gusto's own client sends that the gateway checks. Copied per run.
_FORWARDED_HEADERS = (
    "x-csrf-token",
    "x-role-id",
    "apollographql-client-name",
    "apollographql-client-version",
)

# Path segments under app.gusto.com that are not a company slug.
_NON_COMPANY_SEGMENTS = {"", "user", "users", "login", "logout", "accounts", "select_company"}

# One run at a time drives the shared window.
_profile_lock = threading.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GustoError(RuntimeError):
    """Gusto rejected a request or returned something unexpected."""


class GustoLoginRequired(GustoError):
    """The saved Gusto session is missing or expired; the user must log in."""

    def __init__(self, message="Log in to Gusto again (Settings → Integrations → Gusto)."):
        super().__init__(message)


SHIFTS_QUERY = """
query FreelanceTrackerShifts($trackerId: ID!, $startDate: Date!, $endDate: Date!) {
  tracker(id: $trackerId) {
    id
    employmentType
    groupedShiftsWithTotals(payPeriod: {startDate: $startDate, endDate: $endDate}) {
      date
      shifts {
        id
        clockInDate
        durationInMinutes
        reviewerClockInTimestamp
        reviewerClockOutTimestamp
        notes { text }
      }
    }
  }
}
"""

ADD_HOURS_MUTATION = """
mutation FreelanceTrackerAddHours(
  $trackerId: ID!
  $clockInTimestamp: ISO8601DateTime!
  $clockOutTimestamp: ISO8601DateTime!
  $noteText: String
  $timezone: String
) {
  addHours(
    input: {
      trackerId: $trackerId
      clockInTimestamp: $clockInTimestamp
      clockOutTimestamp: $clockOutTimestamp
      noteText: $noteText
      timezone: $timezone
    }
  ) {
    shiftsForDay {
      date
      shifts { id durationInMinutes reviewerClockInTimestamp reviewerClockOutTimestamp }
    }
    userErrorPayload { userErrors { argument code message } }
  }
}
"""


def company_slug_from_url(url):
    """`https://app.gusto.com/acme-inc/time_tracking` -> `acme-inc` (or None)."""
    match = re.match(r"^https://app\.gusto\.com/([A-Za-z0-9_-]+)(?:[/?#]|$)", url or "")
    if not match or match.group(1).lower() in _NON_COMPANY_SEGMENTS:
        return None
    return match.group(1)


def _chrome_running():
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1) as response:
            return bool(json.load(response).get("Browser"))
    except Exception:
        return False


def _ensure_chrome():
    """Start the app's Chrome (detached) unless it is already running.

    Started as its own session so it outlives the Python process that launched
    it: the app attaches, works, and detaches, and the window stays.
    """
    if _chrome_running():
        return
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            CHROME_BINARY,
            f"--user-data-dir={PROFILE_DIR}",
            f"--remote-debugging-port={DEBUG_PORT}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 20
    while not _chrome_running():
        if time.monotonic() > deadline:
            raise GustoError("Chrome did not start for the Gusto sync.")
        time.sleep(0.25)


def _gusto_page(browser):
    """The window's Gusto tab, reusing one if the user left it open."""
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    for page in context.pages:
        if "gusto.com" in (page.url or ""):
            page.bring_to_front()
            return page
    blank = [page for page in context.pages if page.url in ("about:blank", "")]
    page = blank[0] if blank else context.new_page()
    page.bring_to_front()
    return page


def _pick_remembered_account(page):
    """On Gusto's "Select your email address" screen, click the account if
    exactly one is remembered. Returns True if it clicked."""
    candidates = page.get_by_text(_EMAIL_RE)
    try:
        count = candidates.count()
    except Exception:
        return False
    emails = []
    for index in range(count):
        try:
            text = candidates.nth(index).inner_text().strip()
        except Exception:
            continue
        if _EMAIL_RE.match(text):
            emails.append((index, text))
    if len({text for _, text in emails}) != 1:
        return False
    try:
        candidates.nth(emails[0][0]).click()
        return True
    except Exception:
        return False


def _wait_for_app(page, timeout_seconds, on_login_needed=None):
    """Wait until the page is inside the Gusto app (not the login host).

    Picks the remembered account once if Gusto offers it, then waits for the
    user to approve the passkey. `on_login_needed()` is called once, the first
    time the login page shows, so callers can notify the user.
    """
    deadline = time.monotonic() + timeout_seconds
    picked = False
    notified = False
    while time.monotonic() < deadline:
        if page.is_closed():
            raise GustoLoginRequired("The Gusto window was closed before login finished.")
        url = page.url or ""
        if company_slug_from_url(url):
            return company_slug_from_url(url)
        if LOGIN_HOST in url:
            if not notified and on_login_needed is not None:
                notified = True
                try:
                    on_login_needed()
                except Exception:
                    pass
            if not picked:
                picked = _pick_remembered_account(page)
        page.wait_for_timeout(500)
    raise GustoLoginRequired(
        "Gusto is waiting for your passkey. Approve it in the Gusto window, then push again."
    )


def login(timeout_seconds=600):
    """Bring the Gusto window up and wait for the user to log in.

    Returns {"company_slug": ...} once the browser lands inside a company.
    Raises GustoLoginRequired if time runs out. The window stays open.
    """
    from playwright.sync_api import sync_playwright

    with _profile_lock, sync_playwright() as p:
        _ensure_chrome()
        browser = p.chromium.connect_over_cdp(CDP_URL)
        page = _gusto_page(browser)
        if not company_slug_from_url(page.url or ""):
            page.goto(APP_ORIGIN, wait_until="domcontentloaded")
        return {"company_slug": _wait_for_app(page, timeout_seconds)}


class GustoSession:
    """A logged-in Gusto page plus the gateway headers its client uses."""

    def __init__(self, page, headers, company_slug):
        self._page = page
        self._headers = headers
        self.company_slug = company_slug

    def _fetch(self, url, body=None):
        headers = dict(self._headers)
        if body is not None:
            headers["content-type"] = "application/json"
        result = self._page.evaluate(
            """async ({ url, headers, body }) => {
                const r = await fetch(url, {
                    method: body === null ? 'GET' : 'POST',
                    credentials: 'include',
                    headers,
                    body: body === null ? undefined : JSON.stringify(body),
                });
                let json = null;
                try { json = await r.json(); } catch (e) {}
                return { status: r.status, json };
            }""",
            {"url": url, "headers": headers, "body": body},
        )
        status = result.get("status")
        if status in (401, 403):
            errors = ((result.get("json") or {}).get("errors") or []) if isinstance(result.get("json"), dict) else []
            messages = " ".join(str(e.get("message", "")) for e in errors)
            if status == 401 or "IDENTITY" in messages.upper():
                raise GustoLoginRequired()
        if status is None or status >= 400:
            raise GustoError(f"Gusto returned HTTP {status} for {url.split('?')[0]}")
        return result.get("json")

    def graphql(self, operation_name, query, variables):
        payload = self._fetch(
            f"{GRAPHQL_URL}?operationName={operation_name}",
            {"operationName": operation_name, "query": query, "variables": variables},
        )
        if not isinstance(payload, dict):
            raise GustoError(f"{operation_name}: unexpected response")
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message", "")) for e in payload["errors"])
            raise GustoError(f"{operation_name}: {messages}")
        return payload.get("data") or {}

    def browser_timezone(self):
        return self._page.evaluate("() => Intl.DateTimeFormat().resolvedOptions().timeZone")

    # --- contractor timesheet operations ---------------------------------

    def tracker_id(self):
        """The logged-in user's time-tracking tracker id (one per company member)."""
        trackers = self._fetch(f"{APP_ORIGIN}/api/time_tracking/trackers")
        if not isinstance(trackers, list) or not trackers:
            raise GustoError("No Gusto time tracker found for this login.")
        active = [t for t in trackers if t.get("is_active")] or trackers
        return str(active[0]["id"])

    def list_shifts(self, tracker_id, start_date, end_date):
        """Shifts between two dates (inclusive) as plain dicts."""
        data = self.graphql(
            "FreelanceTrackerShifts",
            SHIFTS_QUERY,
            {
                "trackerId": str(tracker_id),
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        tracker = data.get("tracker") or {}
        shifts = []
        for day in tracker.get("groupedShiftsWithTotals") or []:
            for shift in day.get("shifts") or []:
                shifts.append({
                    "id": str(shift.get("id")),
                    "date": shift.get("clockInDate") or day.get("date"),
                    "minutes": shift.get("durationInMinutes"),
                    "clock_in": shift.get("reviewerClockInTimestamp"),
                    "clock_out": shift.get("reviewerClockOutTimestamp"),
                    "notes": [n.get("text") for n in shift.get("notes") or [] if n.get("text")],
                })
        return shifts

    def add_shift(self, tracker_id, clock_in, clock_out, note, timezone):
        """Create one shift. `clock_in`/`clock_out` are aware datetimes.

        Returns the new shift's id, identified as the shift on that day that
        was not there before the call.
        """
        day = clock_in.date()
        before = {s["id"] for s in self.list_shifts(tracker_id, day, day)}
        data = self.graphql(
            "FreelanceTrackerAddHours",
            ADD_HOURS_MUTATION,
            {
                "trackerId": str(tracker_id),
                "clockInTimestamp": clock_in.isoformat(timespec="seconds"),
                "clockOutTimestamp": clock_out.isoformat(timespec="seconds"),
                "noteText": note,
                "timezone": timezone,
            },
        )
        result = data.get("addHours") or {}
        user_errors = ((result.get("userErrorPayload") or {}).get("userErrors")) or []
        if user_errors:
            raise GustoError("; ".join(str(e.get("message")) for e in user_errors))
        groups = result.get("shiftsForDay") or []
        if isinstance(groups, dict):  # a single ShiftsForDay, not a list
            groups = [groups]
        after = []
        for group in groups:
            after.extend(str(s.get("id")) for s in (group or {}).get("shifts") or [])
        new_ids = [shift_id for shift_id in after if shift_id not in before]
        if not new_ids:
            raise GustoError("Gusto accepted the shift but did not return it.")
        return new_ids[0]


@contextmanager
def open_session(company_slug, wait_seconds=30, login_timeout_seconds=600, on_login_needed=None):
    """A logged-in Gusto page in the app's Chrome window.

    If Gusto asks to log in, the remembered account is picked and the user has
    `login_timeout_seconds` to approve the passkey (`on_login_needed` fires
    when that starts). Raises GustoLoginRequired when they do not. The window
    is left open either way; leaving the block only detaches.
    """
    from playwright.sync_api import sync_playwright

    with _profile_lock, sync_playwright() as p:
        _ensure_chrome()
        browser = p.chromium.connect_over_cdp(CDP_URL)
        page = _gusto_page(browser)
        captured = {}

        def on_request(request):
            if (not captured and request.method == "POST"
                    and request.url.startswith(GRAPHQL_URL)):
                headers = request.headers
                if headers.get("x-csrf-token"):
                    captured.update({k: headers[k] for k in _FORWARDED_HEADERS if k in headers})

        page.on("request", on_request)
        try:
            target = f"{APP_ORIGIN}/{company_slug}/time_tracking"
            page.goto(target, wait_until="domcontentloaded")
            if LOGIN_HOST in (page.url or ""):
                _wait_for_app(page, login_timeout_seconds, on_login_needed)
                captured.clear()
                page.goto(target, wait_until="domcontentloaded")
            deadline = time.monotonic() + wait_seconds
            while not captured:
                if LOGIN_HOST in (page.url or ""):
                    raise GustoLoginRequired()
                if time.monotonic() > deadline:
                    raise GustoError("Gusto's page loaded but never made an authenticated request.")
                page.wait_for_timeout(250)
        finally:
            page.remove_listener("request", on_request)
        yield GustoSession(page, captured, company_slug)
