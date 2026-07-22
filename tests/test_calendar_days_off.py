"""Unit tests for the Google Calendar days-off integration."""

from datetime import date

import calendar_days_off
from calendar_days_off import get_days_off_dates, get_month_days_off
from preferences import DEFAULT_PREFERENCES, validate_preferences

# July 2026: Wednesdays are the 1st, 8th, 15th, 22nd, 29th.
FIXTURE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:ceo-day@test
DTSTART;TZID=America/New_York:20260701T090000
DTEND;TZID=America/New_York:20260701T170000
RRULE:FREQ=WEEKLY;BYDAY=WE
SUMMARY:CEO Day
END:VEVENT
BEGIN:VEVENT
UID:vacation@test
DTSTART;VALUE=DATE:20260727
DTEND;VALUE=DATE:20260729
SUMMARY:Vacation - Beach
END:VEVENT
BEGIN:VEVENT
UID:weekend-off@test
DTSTART;VALUE=DATE:20260725
DTEND;VALUE=DATE:20260726
SUMMARY:Day off
END:VEVENT
BEGIN:VEVENT
UID:meeting@test
DTSTART;TZID=America/New_York:20260710T150000
DTEND;TZID=America/New_York:20260710T160000
SUMMARY:Client sync
END:VEVENT
END:VCALENDAR
"""

KEYWORDS = [kw.lower() for kw in DEFAULT_PREFERENCES["days_off_keywords"]]

JULY_RANGE = (date(2026, 7, 1), date(2026, 7, 31))


class TestGetDaysOffDates:
    def test_recurring_event_expands_to_all_occurrences(self):
        days = get_days_off_dates(*JULY_RANGE, KEYWORDS, FIXTURE_ICS)
        wednesdays = {date(2026, 7, d) for d in (1, 8, 15, 22, 29)}
        assert wednesdays <= days

    def test_multi_day_all_day_event_dtend_exclusive(self):
        days = get_days_off_dates(*JULY_RANGE, KEYWORDS, FIXTURE_ICS)
        assert date(2026, 7, 27) in days
        assert date(2026, 7, 28) in days
        assert date(2026, 7, 29) in days  # also a Wednesday, but not from Vacation
        # DTEND 20260729 is exclusive: the vacation itself ends the 28th
        assert date(2026, 7, 30) not in days

    def test_non_matching_events_ignored(self):
        days = get_days_off_dates(*JULY_RANGE, KEYWORDS, FIXTURE_ICS)
        assert date(2026, 7, 10) not in days  # "Client sync"

    def test_keyword_match_is_case_insensitive_substring(self):
        days = get_days_off_dates(*JULY_RANGE, ["beach"], FIXTURE_ICS)
        assert days == {date(2026, 7, 27), date(2026, 7, 28)}

    def test_range_bounds_respected(self):
        days = get_days_off_dates(date(2026, 7, 6), date(2026, 7, 12), KEYWORDS, FIXTURE_ICS)
        assert days == {date(2026, 7, 8)}

    def test_unparseable_ics_returns_none(self):
        assert get_days_off_dates(*JULY_RANGE, KEYWORDS, "not an ics feed") is None


class TestGetMonthDaysOff:
    def _prefs(self, **overrides):
        prefs = DEFAULT_PREFERENCES.copy()
        prefs.update(overrides)
        return prefs

    def test_no_url_falls_back_to_static(self, monkeypatch):
        monkeypatch.setattr(
            calendar_days_off, "load_integration_settings",
            lambda: {"GOOGLE_CALENDAR_ICS_URL": ""},
        )
        result = get_month_days_off(2026, 7, prefs=self._prefs(vacation_days_per_month=3))
        assert result == {"source": "static", "vacation_days": 3, "dates": []}

    def test_fetch_failure_without_cache_falls_back_to_static(self, monkeypatch):
        monkeypatch.setattr(
            calendar_days_off, "load_integration_settings",
            lambda: {"GOOGLE_CALENDAR_ICS_URL": "https://example.com/cal.ics"},
        )
        monkeypatch.setattr(calendar_days_off, "_load_ics_text", lambda url, force=False: None)
        result = get_month_days_off(2026, 7, prefs=self._prefs(vacation_days_per_month=2))
        assert result["source"] == "static"
        assert result["vacation_days"] == 2

    def test_calendar_counts_only_business_days(self, monkeypatch):
        monkeypatch.setattr(
            calendar_days_off, "load_integration_settings",
            lambda: {"GOOGLE_CALENDAR_ICS_URL": "https://example.com/cal.ics"},
        )
        monkeypatch.setattr(
            calendar_days_off, "_load_ics_text", lambda url, force=False: FIXTURE_ICS
        )
        result = get_month_days_off(2026, 7)
        assert result["source"] == "calendar"
        # 5 recurring CEO-day Wednesdays + Jul 27/28 vacation (Mon/Tue).
        # The Jul 25 "Day off" is a Saturday and must not reduce workable days.
        assert result["vacation_days"] == 7
        assert "2026-07-25" not in result["dates"]
        assert "2026-07-27" in result["dates"]


class TestPreferencesValidation:
    def test_default_keywords_valid(self):
        assert validate_preferences(DEFAULT_PREFERENCES.copy()) == []

    def test_keywords_must_be_list(self):
        prefs = DEFAULT_PREFERENCES.copy()
        prefs["days_off_keywords"] = "vacation"
        errors = validate_preferences(prefs)
        assert any("days_off_keywords" in e for e in errors)

    def test_keywords_entries_must_be_nonempty_strings(self):
        prefs = DEFAULT_PREFERENCES.copy()
        prefs["days_off_keywords"] = ["vacation", ""]
        errors = validate_preferences(prefs)
        assert any("days_off_keywords" in e for e in errors)
