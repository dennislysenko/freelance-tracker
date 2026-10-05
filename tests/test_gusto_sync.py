"""Gusto sync: schedule, payday, planning, drift, and run_sync against a fake
Gusto session. Nothing here launches Chrome or talks to Gusto."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

import gusto_client
import gusto_sync as g


PROJECT_ID = "111"
PREFS = {
    "gusto_sync_enabled": True,
    "gusto_project": "Acme",
    "gusto_company_slug": "acme-inc",
}


def _local(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm).astimezone()


def _entry(entry_id, start, minutes, desc="work", project_id=PROJECT_ID):
    stop = start + timedelta(minutes=minutes)
    utc = timezone.utc
    return {
        "id": entry_id,
        "project_id": int(project_id),
        "start": start.astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stop": stop.astimezone(utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration": minutes * 60,
        "description": desc,
    }


class FakeSession:
    def __init__(self, existing=(), reject_days=(), login_required=False):
        self.existing = list(existing)
        self.reject_days = set(reject_days)
        self.login_required = login_required
        self.added = []

    def tracker_id(self):
        if self.login_required:
            raise gusto_client.GustoLoginRequired()
        return "12345"

    def browser_timezone(self):
        return "America/New_York"

    def list_shifts(self, tracker_id, start, end):
        return list(self.existing)

    def add_shift(self, tracker_id, clock_in, clock_out, note, timezone):
        if clock_in.date().isoformat() in self.reject_days:
            raise gusto_client.GustoError("day is locked")
        shift_id = str(9000 + len(self.added))
        self.added.append({"id": shift_id, "clock_in": clock_in, "clock_out": clock_out,
                           "note": note, "timezone": timezone})
        return shift_id


def _factory(session):
    @contextmanager
    def factory(slug):
        assert slug == "acme-inc"
        yield session
    return factory


def _run(entries, state=None, session=None, now=None, window=None, persisted=None):
    session = session or FakeSession()
    return session, g.run_sync(
        PREFS, state or {"pushed": {}}, now=now or datetime(2026, 10, 12, 9, 30),
        window=window, session_factory=_factory(session),
        entries_provider=lambda start, end: entries,
        projects_provider=lambda: {PROJECT_ID: {"name": "Acme"}, "222": {"name": "Other"}},
        persist=(persisted.append if persisted is not None else None),
    )


# --- schedule ----------------------------------------------------------------


def test_target_week_is_previous_monday_to_sunday_after_9am():
    assert g.target_week(datetime(2026, 10, 12, 9, 0)) == (date(2026, 10, 5), date(2026, 10, 11))
    # Before 09:00 on Monday the run has not happened yet: still the week before.
    assert g.target_week(datetime(2026, 10, 12, 8, 59)) == (date(2026, 9, 28), date(2026, 10, 4))
    # Mid-week it stays on the last completed week.
    assert g.target_week(datetime(2026, 10, 15, 14, 0)) == (date(2026, 10, 5), date(2026, 10, 11))


def test_sync_due_respects_synced_through_login_and_backoff():
    monday = datetime(2026, 10, 12, 9, 5)
    assert g.sync_due(monday, {"synced_through": "2026-10-05"})
    assert not g.sync_due(monday, {"synced_through": "2026-10-11"})
    assert not g.sync_due(monday, {"login_required": True})
    assert not g.sync_due(monday, {}, enabled=False)
    failed = {"last_error": "boom", "last_attempt": "2026-10-12T09:01:00"}
    assert not g.sync_due(monday, failed)
    assert g.sync_due(datetime(2026, 10, 12, 10, 2), failed)


def test_first_run_does_not_backfill_beyond_target_week():
    start, end = g.sync_window(datetime(2026, 10, 12, 9, 30), {})
    assert (start, end) == (date(2026, 10, 5), date(2026, 10, 11))
    # With a recorded first day, the lookback reaches back to it.
    start, _ = g.sync_window(datetime(2026, 10, 26, 9, 30), {"first_day": "2026-10-05"})
    assert start == date(2026, 10, 5)
    # An explicit start date preference wins.
    start, _ = g.sync_window(datetime(2026, 10, 12, 9, 30), {}, first_day=date(2026, 10, 1))
    assert start == date(2026, 10, 1)


def test_manual_window_ends_yesterday():
    start, end = g.manual_window(datetime(2026, 10, 8, 15, 0), {"first_day": "2026-10-05"})
    assert (start, end) == (date(2026, 10, 5), date(2026, 10, 7))


# --- payday ------------------------------------------------------------------


@pytest.mark.parametrize("year,month,expected", [
    (2026, 11, date(2026, 10, 30)),  # Nov 1 is a Sunday (the Slack example)
    (2026, 12, date(2026, 12, 1)),  # Tuesday
    (2028, 1, date(2027, 12, 30)),  # Jan 1 Sat, observed Fri Dec 31 -> Thu Dec 30
    (2025, 9, date(2025, 8, 29)),  # Labor Day Mon Sep 1 -> Fri Aug 29
])
def test_payday_rolls_back_over_weekends_and_holidays(year, month, expected):
    assert g.payday_for(year, month) == expected


def test_next_payday_and_deadline():
    assert g.next_payday(date(2026, 10, 5)) == (date(2026, 10, 30), date(2026, 10, 28))
    assert g.next_payday(date(2026, 10, 31)) == (date(2026, 12, 1), date(2026, 11, 29))


# --- planning ----------------------------------------------------------------


def test_plan_filters_project_running_pushed_and_out_of_window():
    entries = [
        _entry(1, _local(2026, 10, 6, 9), 60, "a"),
        _entry(2, _local(2026, 10, 6, 11), 30, "", project_id="222"),
        {**_entry(3, _local(2026, 10, 7, 9), 60), "duration": -1, "stop": None},
        _entry(4, _local(2026, 10, 8, 9), 45, "already"),
        _entry(5, _local(2026, 10, 13, 9), 60, "next week"),
        _entry(6, _local(2026, 10, 9, 9), 90, "  "),
    ]
    state = {"pushed": {"4": {"day": "2026-10-08"}}}
    plan = g.plan_pushes(entries, PROJECT_ID, state, date(2026, 10, 5), date(2026, 10, 11))
    assert [item["toggl_id"] for item in plan] == ["1", "6"]
    assert plan[1]["note"] == g.DEFAULT_NOTE
    assert plan[0]["hours"] == 1.0


def test_plan_marks_entries_already_in_gusto():
    start = _local(2026, 10, 6, 9)
    existing = [{"id": "77", "clock_in": start.isoformat(),
                 "clock_out": (start + timedelta(hours=1)).isoformat()}]
    plan = g.plan_pushes([_entry(1, start, 60)], PROJECT_ID, {"pushed": {}},
                         date(2026, 10, 5), date(2026, 10, 11), existing)
    assert plan[0]["existing_shift_id"] == "77"


def test_drift_flags_changed_and_deleted_entries_only_in_window():
    start = _local(2026, 10, 6, 9)
    record = lambda day, s, e: {"day": day, "start": s.isoformat(timespec="seconds"),
                                "stop": e.isoformat(timespec="seconds")}
    state = {"pushed": {
        "1": record("2026-10-06", start, start + timedelta(hours=1)),
        "2": record("2026-10-07", start + timedelta(days=1), start + timedelta(days=1, hours=1)),
        "3": record("2026-10-08", start + timedelta(days=2), start + timedelta(days=2, hours=1)),
        "4": record("2026-09-01", start, start),
    }}
    entries = [
        _entry(1, start, 60),  # unchanged
        _entry(2, start + timedelta(days=1), 90),  # longer now
    ]  # 3 deleted; 4 outside window
    issues = g.find_drift(entries, PROJECT_ID, state, date(2026, 10, 5), date(2026, 10, 11))
    assert [i["kind"] for i in issues] == ["changed", "deleted"]


# --- run_sync ----------------------------------------------------------------


def test_run_pushes_records_and_persists_each_write():
    entries = [_entry(1, _local(2026, 10, 6, 9), 60, "a"), _entry(2, _local(2026, 10, 7, 9), 30, "b")]
    persisted = []
    session, (state, summary) = _run(entries, persisted=persisted)
    assert summary["pushed"] == 2 and summary["hours"] == 1.5 and not summary["error"]
    assert [a["note"] for a in session.added] == ["a", "b"]
    assert session.added[0]["timezone"] == "America/New_York"
    assert set(state["pushed"]) == {"1", "2"}
    assert state["synced_through"] == "2026-10-11"
    assert len(persisted) == 2
    # A second run pushes nothing again.
    session2, (_, summary2) = _run(entries, state=state)
    assert session2.added == [] and summary2["pushed"] == 0


def test_run_adopts_matching_gusto_shift_instead_of_adding():
    start = _local(2026, 10, 6, 9)
    session = FakeSession(existing=[{"id": "77", "clock_in": start.isoformat(),
                                     "clock_out": (start + timedelta(hours=1)).isoformat()}])
    _, (state, summary) = _run([_entry(1, start, 60)], session=session)
    assert session.added == []
    assert summary["adopted"] == 1 and summary["pushed"] == 0
    assert state["pushed"]["1"]["shift_id"] == "77"


def test_dry_run_writes_nothing_and_keeps_state():
    session = FakeSession()
    _, (state, summary) = (session, g.run_sync(
        PREFS, {"pushed": {}}, now=datetime(2026, 10, 12, 9, 30), dry_run=True,
        session_factory=_factory(session),
        entries_provider=lambda s, e: [_entry(1, _local(2026, 10, 6, 9), 60)],
        projects_provider=lambda: {PROJECT_ID: {"name": "Acme"}},
    ))
    assert session.added == []
    assert len(summary["planned"]) == 1
    assert "synced_through" not in state and "last_attempt" not in state


def test_login_required_stops_retries_until_cleared():
    _, (state, summary) = _run([_entry(1, _local(2026, 10, 6, 9), 60)],
                               session=FakeSession(login_required=True))
    assert summary["login_required"] and state["login_required"]
    assert not g.sync_due(datetime(2026, 10, 12, 11, 0), state)


def test_rejected_shift_is_reported_and_others_still_push():
    entries = [_entry(1, _local(2026, 10, 6, 9), 60), _entry(2, _local(2026, 10, 7, 9), 60)]
    session, (state, summary) = _run(entries, session=FakeSession(reject_days={"2026-10-06"}))
    assert summary["pushed"] == 1
    assert [i["kind"] for i in summary["issues"]] == ["rejected"]
    assert set(state["pushed"]) == {"2"}


def test_missing_project_or_login_fails_cleanly():
    _, summary = g.run_sync({**PREFS, "gusto_project": ""}, {"pushed": {}},
                            now=datetime(2026, 10, 12, 9, 30))
    assert "Choose which Toggl project" in summary["error"]
    _, summary = g.run_sync({**PREFS, "gusto_company_slug": ""}, {"pushed": {}},
                            now=datetime(2026, 10, 12, 9, 30))
    assert summary["login_required"]


# --- status ------------------------------------------------------------------


def test_status_is_stale_from_monday_until_last_week_is_pushed():
    synced = {"synced_through": "2026-10-04", "pushed": {}}
    sunday = g.status_view(PREFS, synced, now=datetime(2026, 10, 11, 20, 0))
    monday = g.status_view(PREFS, synced, now=datetime(2026, 10, 12, 9, 1))
    assert not sunday["stale"] and sunday["meta"] == "Up to date through Oct 4"
    assert monday["stale"] and monday["meta"] == "Oct 5–Oct 11 not pushed"
    done = g.status_view(PREFS, {"synced_through": "2026-10-11"}, now=datetime(2026, 10, 12, 10))
    assert not done["stale"]


def test_status_flags_attention_and_hides_when_unconfigured():
    state = {"synced_through": "2026-10-11", "issues": [{"message": "Tue Oct 6: changed"}]}
    view = g.status_view(PREFS, state, now=datetime(2026, 10, 12, 10))
    assert view["stale"] and view["meta"] == "1 needs attention"
    assert g.status_view({**PREFS, "gusto_sync_enabled": False}, state)["configured"] is False
    assert g.status_view(PREFS, state, running=True)["stale"] is False


# --- review fixes --------------------------------------------------------------


def test_retry_backoff_doubles_and_caps():
    assert g.retry_delay({"consecutive_failures": 1}) == timedelta(hours=1)
    assert g.retry_delay({"consecutive_failures": 3}) == timedelta(hours=4)
    assert g.retry_delay({"consecutive_failures": 10}) == timedelta(hours=24)
    state = {"last_error": "schema", "last_attempt": "2026-10-12T09:00:00", "consecutive_failures": 3}
    assert not g.sync_due(datetime(2026, 10, 12, 12, 0), state)
    assert g.sync_due(datetime(2026, 10, 12, 13, 1), state)


def test_failures_count_up_and_reset_on_success():
    _, (state, _) = _run([], session=FakeSession(login_required=True))
    assert state["consecutive_failures"] == 1
    _, (state, _) = _run([], state={**state, "login_required": False, "consecutive_failures": 1},
                         session=FakeSession(login_required=True))
    assert state["consecutive_failures"] == 2
    _, (state, _) = _run([], state=state)
    assert state["consecutive_failures"] == 0


def test_future_start_date_means_nothing_to_push_and_no_browser():
    def no_browser(slug):
        raise AssertionError("must not open Gusto")
    state, summary = g.run_sync(
        PREFS, {"pushed": {}}, now=datetime(2026, 10, 8, 12),
        window=(date(2026, 10, 9), date(2026, 10, 7)), session_factory=no_browser,
    )
    assert summary["empty"] and not summary["error"]
    assert g.describe_result(summary) == "Nothing to push yet"
    assert g.manual_window(datetime(2026, 10, 8, 12), {}, first_day=date(2026, 10, 9))[0] > \
        g.manual_window(datetime(2026, 10, 8, 12), {}, first_day=date(2026, 10, 9))[1]


def test_drift_ignores_time_zone_changes():
    start = datetime(2026, 10, 6, 13, 0, tzinfo=timezone.utc)
    stop = start + timedelta(hours=1)
    # Recorded while the Mac was in another zone: same instants, different offsets.
    other = timezone(timedelta(hours=-7))
    state = {"pushed": {"1": {"day": "2026-10-06",
                              "start": start.astimezone(other).isoformat(timespec="seconds"),
                              "stop": stop.astimezone(other).isoformat(timespec="seconds")}}}
    issues = g.find_drift([_entry(1, start, 60)], PROJECT_ID, state, date(2026, 10, 5), date(2026, 10, 11))
    assert issues == []


def test_shift_that_landed_despite_an_error_is_adopted_not_retried():
    start = _local(2026, 10, 6, 9)

    class LandsButErrors(FakeSession):
        def add_shift(self, tracker_id, clock_in, clock_out, note, timezone):
            self.existing.append({"id": "555", "clock_in": clock_in.isoformat(),
                                  "clock_out": clock_out.isoformat()})
            raise gusto_client.GustoError("Gusto accepted the shift but did not return it.")

    session = LandsButErrors()
    _, (state, summary) = _run([_entry(1, start, 60)], session=session)
    assert state["pushed"]["1"]["shift_id"] == "555"
    assert summary["issues"] == []


def test_same_shift_falls_back_to_day_duration_note_without_timestamps():
    start = _local(2026, 10, 6, 9)
    hidden = {"id": "9", "date": "2026-10-06", "minutes": 60, "clock_in": None,
              "clock_out": None, "notes": ["a"]}
    assert g._same_shift(hidden, start, start + timedelta(hours=1), "a")
    assert not g._same_shift(hidden, start, start + timedelta(hours=2), "a")
    assert not g._same_shift(hidden, start, start + timedelta(hours=1), "b")


def test_run_locked_refuses_while_another_push_holds_the_lock(tmp_path):
    lock = tmp_path / "sync.lock"
    state_path = tmp_path / "state.json"
    g.save_state({"pushed": {"x": {}}}, state_path)
    with g.run_lock(lock):
        state, summary = g.run_locked(PREFS, lock_path=lock, state_path=state_path,
                                      window=(date(2026, 10, 5), date(2026, 10, 11)))
    assert summary["busy"] and "already running" in summary["error"]
    assert state == {"pushed": {"x": {}}}


def test_run_locked_saves_ledger_under_the_lock(tmp_path):
    session = FakeSession()
    state, summary = g.run_locked(
        PREFS, now=datetime(2026, 10, 12, 9, 30), session_factory=_factory(session),
        state_path=tmp_path / "s.json", lock_path=tmp_path / "l.lock",
        entries_provider=lambda s, e: [_entry(1, _local(2026, 10, 6, 9), 60)],
        projects_provider=lambda: {PROJECT_ID: {"name": "Acme"}},
    )
    assert summary["pushed"] == 1
    assert "1" in g.load_state(tmp_path / "s.json")["pushed"]


def test_fresh_fetch_raises_instead_of_returning_cached_or_empty(monkeypatch):
    import toggl_data

    class Response:
        status_code = 402

    monkeypatch.setattr(toggl_data, "CACHE_ONLY", False)
    monkeypatch.setattr(toggl_data, "_get_api_token", lambda: "token")
    monkeypatch.setattr(toggl_data.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(toggl_data, "log_api_request", lambda *a, **k: None)  # keep the real audit log clean
    monkeypatch.setattr(toggl_data, "_rate_limited", False)
    stored = []
    monkeypatch.setattr(toggl_data, "_store_entries_for_range", lambda *a: stored.append(a))
    with pytest.raises(toggl_data.TogglFetchError, match="rate limit"):
        toggl_data.fetch_entries_fresh(_local(2026, 10, 5, 0), _local(2026, 10, 11, 23))
    assert stored == []
