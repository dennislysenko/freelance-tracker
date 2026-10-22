"""Google Calendar days-off integration.

Reads the user's Google Calendar through its secret iCal URL
(Google Calendar → Settings → "Secret address in iCal format", stored as
GOOGLE_CALENDAR_ICS_URL in the app-local .env) and finds the business days
in a month that are blocked off by events whose titles match the configured
`days_off_keywords`. Recurring events (e.g. a weekly "CEO Day") are expanded
via RRULE, so they count once per occurrence.

The month projection uses this actual per-month count instead of the flat
`vacation_days_per_month` estimate; the static value remains the fallback
when no calendar URL is configured or the feed can't be read at all.

API notes: the ICS feed is fetched at most once per CALENDAR_TTL_SECONDS
(6h) and cached under ~/Library/Caches/TogglMenuBar/. When a fetch fails,
the stale cached copy is reused indefinitely. 0 Toggl API calls.
"""

import calendar as _calendar
import json
import time
from datetime import date, datetime, timedelta
from datetime import time as _time

import requests

from integrations import load_integration_settings
from preferences import CACHE_DIR, DEFAULT_PREFERENCES, load_preferences

CALENDAR_TTL_SECONDS = 6 * 3600

ICS_CACHE_FILE = CACHE_DIR / "days_off_calendar.ics"
ICS_META_FILE = CACHE_DIR / "days_off_calendar_meta.json"


def _load_ics_text(url, force=False):
    """Return the ICS feed text, honoring the TTL cache. None if unavailable.

    A cached copy is invalidated when the configured URL changes. On fetch
    failure the stale cache (if any) is returned rather than nothing.
    """
    meta = {}
    if ICS_META_FILE.exists():
        try:
            meta = json.loads(ICS_META_FILE.read_text())
        except (ValueError, OSError):
            meta = {}

    cache_valid_for_url = ICS_CACHE_FILE.exists() and meta.get("url") == url
    fresh = (
        cache_valid_for_url
        and not force
        and (time.time() - meta.get("fetched_at", 0)) < CALENDAR_TTL_SECONDS
    )
    if fresh:
        try:
            return ICS_CACHE_FILE.read_text()
        except OSError:
            pass

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        text = resp.text
        ICS_CACHE_FILE.write_text(text)
        ICS_META_FILE.write_text(json.dumps({"url": url, "fetched_at": time.time()}))
        return text
    except Exception:
        # Network/HTTP failure: fall back to a stale cache for the same URL.
        if cache_valid_for_url:
            try:
                return ICS_CACHE_FILE.read_text()
            except OSError:
                return None
        return None


def _covered_dates(start, end):
    """Yield each calendar date an event occurrence covers.

    All-day events use exclusive DTEND per RFC 5545; timed events ending at
    midnight are treated as ending the previous day.
    """
    if isinstance(start, datetime):
        start_d = start.date()
    else:
        start_d = start

    if isinstance(end, datetime):
        end_d = end.date()
        if end.time() == _time(0, 0) and end_d > start_d:
            end_d -= timedelta(days=1)
    elif isinstance(end, date):
        # DATE-valued DTEND is exclusive
        end_d = end - timedelta(days=1)
        if end_d < start_d:
            end_d = start_d
    else:
        end_d = start_d

    d = start_d
    while d <= end_d:
        yield d
        d += timedelta(days=1)


def _matches_keywords(summary, keywords):
    text = (summary or "").lower()
    return any(kw in text for kw in keywords)


def get_days_off_dates(range_start, range_end, keywords, ics_text):
    """Return the set of dates in [range_start, range_end] covered by
    keyword-matching events in the ICS text. None if the feed can't be parsed.
    """
    try:
        import icalendar
        import recurring_ical_events

        cal = icalendar.Calendar.from_ical(ics_text)
        occurrences = recurring_ical_events.of(cal).between(
            range_start, range_end + timedelta(days=1)
        )
    except Exception:
        return None

    days_off = set()
    for event in occurrences:
        summary = str(event.get("SUMMARY", ""))
        if not _matches_keywords(summary, keywords):
            continue
        start = event.get("DTSTART")
        end = event.get("DTEND", start)
        start = start.dt if start is not None else None
        end = end.dt if end is not None else start
        if start is None:
            continue
        for d in _covered_dates(start, end):
            if range_start <= d <= range_end:
                days_off.add(d)
    return days_off


def get_month_days_off(year, month, prefs=None):
    """Resolve days off for a month, preferring the calendar over the flat pref.

    Returns {"source": "calendar"|"static", "vacation_days": int,
    "dates": [iso strings]} where vacation_days counts only business days
    (weekends never reduce workable days). Falls back to the static
    `vacation_days_per_month` preference when no calendar is configured or
    the feed is unreadable.
    """
    if prefs is None:
        prefs = load_preferences()

    static_result = {
        "source": "static",
        "vacation_days": prefs.get("vacation_days_per_month", 4),
        "dates": [],
    }

    url = (load_integration_settings().get("GOOGLE_CALENDAR_ICS_URL") or "").strip()
    if not url:
        return static_result

    ics_text = _load_ics_text(url)
    if not ics_text:
        return static_result

    keywords = [
        str(kw).strip().lower()
        for kw in (prefs.get("days_off_keywords") or DEFAULT_PREFERENCES["days_off_keywords"])
        if str(kw).strip()
    ]
    if not keywords:
        return static_result

    first = date(year, month, 1)
    last = date(year, month, _calendar.monthrange(year, month)[1])
    dates = get_days_off_dates(first, last, keywords, ics_text)
    if dates is None:
        return static_result

    business_days_off = sorted(d for d in dates if d.weekday() < 5)
    return {
        "source": "calendar",
        "vacation_days": len(business_days_off),
        "dates": [d.isoformat() for d in business_days_off],
    }
