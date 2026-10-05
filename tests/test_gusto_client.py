"""GustoSession request/response handling against a fake page. No Chrome."""

from datetime import datetime, timezone

import pytest

import gusto_client
from gusto_client import GustoError, GustoLoginRequired, GustoSession, company_slug_from_url


class FakePage:
    """Answers page.evaluate(fetch...) from a queue of canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def evaluate(self, script, arg=None):
        self.calls.append(arg)
        return self.responses.pop(0)


def _session(responses):
    page = FakePage(responses)
    return GustoSession(page, {"x-csrf-token": "t", "x-role-id": "r"}, "acme-inc"), page


def _shifts_response(*ids):
    return {"status": 200, "json": {"data": {"tracker": {"groupedShiftsWithTotals": [
        {"date": "2026-10-05", "shifts": [
            {"id": i, "clockInDate": "2026-10-05", "durationInMinutes": 30,
             "reviewerClockInTimestamp": "2026-10-05T15:00:00-04:00",
             "reviewerClockOutTimestamp": "2026-10-05T15:30:00-04:00",
             "notes": [{"text": "design review"}]}
            for i in ids
        ]},
    ]}}}}


def test_company_slug_from_url():
    assert company_slug_from_url("https://app.gusto.com/acme-inc/time_tracking") == "acme-inc"
    assert company_slug_from_url("https://app.gusto.com/acme-inc") == "acme-inc"
    assert company_slug_from_url("https://app.gusto.com/user/auth") is None
    assert company_slug_from_url("https://login.gusto.com/acme") is None


def test_add_shift_accepts_single_object_shifts_for_day():
    clock_in = datetime(2026, 10, 5, 19, 0, tzinfo=timezone.utc)
    clock_out = datetime(2026, 10, 5, 19, 30, tzinfo=timezone.utc)
    add_reply = {"status": 200, "json": {"data": {"addHours": {
        # Gusto returns one ShiftsForDay here, not a list.
        "shiftsForDay": {"date": "2026-10-05", "shifts": [{"id": "1"}, {"id": "2"}]},
        "userErrorPayload": None,
    }}}}
    session, page = _session([_shifts_response("1"), add_reply])
    assert session.add_shift("12345", clock_in, clock_out, "design review", "America/New_York") == "2"
    sent = page.calls[1]["body"]
    assert sent["operationName"] == "FreelanceTrackerAddHours"
    assert sent["variables"]["clockInTimestamp"] == "2026-10-05T19:00:00+00:00"
    assert sent["variables"]["noteText"] == "design review"
    assert page.calls[1]["headers"]["x-role-id"] == "r"


def test_add_shift_surfaces_user_errors():
    clock = datetime(2026, 10, 5, 19, 0, tzinfo=timezone.utc)
    reply = {"status": 200, "json": {"data": {"addHours": {
        "shiftsForDay": None,
        "userErrorPayload": {"userErrors": [{"message": "You can't edit days in the future"}]},
    }}}}
    session, _ = _session([_shifts_response(), reply])
    with pytest.raises(GustoError, match="future"):
        session.add_shift("1", clock, clock, "x", "UTC")


def test_identity_failure_means_login_required():
    session, _ = _session([{"status": 403, "json": {"errors": [{"message": "GRAPHQL_IDENTITY_FAILURE"}]}}])
    with pytest.raises(GustoLoginRequired):
        session.graphql("Op", "query { x }", {})


def test_graphql_errors_raise_gusto_error():
    session, _ = _session([{"status": 200, "json": {"errors": [{"message": "Cannot query field"}]}}])
    with pytest.raises(GustoError, match="Cannot query field"):
        session.graphql("Op", "query { x }", {})


def test_list_shifts_flattens_days():
    session, _ = _session([_shifts_response("1", "2")])
    shifts = session.list_shifts("1", datetime(2026, 10, 5).date(), datetime(2026, 10, 5).date())
    assert [s["id"] for s in shifts] == ["1", "2"]
    assert shifts[0]["notes"] == ["design review"]
    assert shifts[0]["clock_in"] == "2026-10-05T15:00:00-04:00"


def test_chrome_is_never_headless():
    import inspect
    source = inspect.getsource(gusto_client)
    assert "headless=True" not in source
