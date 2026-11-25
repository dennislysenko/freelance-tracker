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
    _next_id = 9000

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
        # Unique across sessions, like Gusto's own ids.
        FakeSession._next_id += 1
        shift_id = str(FakeSession._next_id)
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


def test_manual_window_runs_through_today():
    # Today's finished entries go too, so a click can fill the month's cap.
    start, end = g.manual_window(datetime(2026, 10, 8, 15, 0), {"first_day": "2026-10-05"})
    assert (start, end) == (date(2026, 10, 5), date(2026, 10, 8))


# --- month-end deadline and payday -------------------------------------------


@pytest.mark.parametrize("year,month,cutoff,payday", [
    (2026, 10, date(2026, 10, 30), date(2026, 11, 2)),  # Oct 31 Sat; Nov 1 Sun
    (2027, 12, date(2027, 12, 30), date(2028, 1, 3)),  # Dec 31 observed New Year's
    (2025, 8, date(2025, 8, 29), date(2025, 9, 2)),  # Aug 31 Sun; Sep 1 Labor Day
    (2026, 11, date(2026, 11, 30), date(2026, 12, 1)),  # plain weekdays
])
def test_cutoff_is_last_business_day_and_payday_the_next_one(year, month, cutoff, payday):
    assert g.last_business_day(year, month) == cutoff
    assert g.payday_after(year, month) == payday


def test_next_deadline_rolls_over_at_the_cutoff_hour():
    assert g.next_deadline(datetime(2026, 10, 13, 10)) == (date(2026, 10, 30), date(2026, 11, 2))
    assert g.next_deadline(datetime(2026, 10, 30, 14, 59)) == (date(2026, 10, 30), date(2026, 11, 2))
    assert g.next_deadline(datetime(2026, 10, 30, 15, 0)) == (date(2026, 11, 30), date(2026, 12, 1))


def test_deadline_push_window_and_retries():
    assert g.deadline_push_due(datetime(2026, 10, 30, 13, 0), {})
    assert not g.deadline_push_due(datetime(2026, 10, 30, 12, 59), {})
    assert not g.deadline_push_due(datetime(2026, 10, 30, 15, 0), {})
    assert not g.deadline_push_due(datetime(2026, 10, 28, 13, 30), {})  # neither cutoff nor the day before
    assert not g.deadline_push_due(datetime(2026, 10, 30, 13, 0), {}, enabled=False)
    assert not g.deadline_push_due(datetime(2026, 10, 30, 14, 0), {"deadline_pushed": "2026-10-30"})
    # A pending login does not stop the month-end push (unlike the weekly one).
    assert g.deadline_push_due(datetime(2026, 10, 30, 13, 0), {"login_required": True})
    failed = {"last_error": "passkey", "last_attempt": "2026-10-30T13:00:00"}
    assert not g.deadline_push_due(datetime(2026, 10, 30, 13, 15), failed)
    assert g.deadline_push_due(datetime(2026, 10, 30, 13, 21), failed)


def test_deadline_and_manual_windows_include_the_cutoff_day():
    state = {"first_day": "2026-10-05"}
    assert g.deadline_window(datetime(2026, 10, 30, 13), state) == (date(2026, 10, 5), date(2026, 10, 30))
    assert g.manual_window(datetime(2026, 10, 30, 11), state)[1] == date(2026, 10, 30)
    assert g.manual_window(datetime(2026, 10, 29, 11), state)[1] == date(2026, 10, 29)


def test_run_on_cutoff_day_marks_month_pushed_and_clears_stale():
    now = datetime(2026, 10, 30, 13, 5)
    entries = [_entry(1, _local(2026, 10, 30, 9), 120, "morning")]
    session, (state, summary) = _run(entries, now=now,
                                     state={"pushed": {}, "synced_through": "2026-10-25"},
                                     window=(date(2026, 10, 26), date(2026, 10, 30)))
    assert summary["pushed"] == 1 and session.added[0]["note"] == "morning"
    assert state["deadline_pushed"] == "2026-10-30"
    assert not g.deadline_push_due(datetime(2026, 10, 30, 13, 30), state)
    view = g.status_view(PREFS, state, now=datetime(2026, 10, 30, 13, 30))
    assert not view["stale"]
    assert view["next_deadline"] == "Hours due Fri Oct 30 by 3 PM \u00b7 payday Mon Nov 2"


def test_cutoff_day_is_stale_until_the_month_is_pushed():
    state = {"synced_through": "2026-10-25", "pushed": {}}
    view = g.status_view(PREFS, state, now=datetime(2026, 10, 30, 9))
    assert view["stale"] and view["meta"] == "Month's hours due today by 3 PM"


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


def test_cutoff_missed_reads_as_missed_not_a_week_label():
    state = {"synced_through": "2026-10-25", "pushed": {}}
    view = g.status_view(PREFS, state, now=datetime(2026, 10, 30, 15, 30))
    assert view["stale"] and view["meta"] == "Month's hours missed the 3 PM cutoff"


def test_early_redundancy_push_the_business_day_before_the_cutoff():
    assert g.month_end_push_kind(date(2026, 10, 29)) == "early"
    assert g.month_end_push_kind(date(2026, 10, 30)) == "cutoff"
    assert g.month_end_push_kind(date(2026, 10, 28)) is None
    # Cutoff Fri Aug 29 2025 (Aug 31 is a Sunday): early is Thu Aug 28.
    assert g.month_end_push_kind(date(2025, 8, 28)) == "early"
    assert g.deadline_push_due(datetime(2026, 10, 29, 13, 0), {})
    assert not g.deadline_push_due(datetime(2026, 10, 29, 14, 0), {"deadline_pushed": "2026-10-29"})
    # The early push does not stand in for the cutoff-day push.
    assert g.deadline_push_due(datetime(2026, 10, 30, 13, 0), {"deadline_pushed": "2026-10-29"})


def test_early_push_marks_its_day_but_cutoff_day_stays_due():
    now = datetime(2026, 10, 29, 13, 5)
    entries = [_entry(1, _local(2026, 10, 29, 9), 60, "thursday")]
    session, (state, summary) = _run(entries, now=now, state={"pushed": {}, "synced_through": "2026-10-25"},
                                     window=(date(2026, 10, 26), date(2026, 10, 29)))
    assert summary["pushed"] == 1
    assert state["deadline_pushed"] == "2026-10-29"
    assert not g.status_view(PREFS, state, now=datetime(2026, 10, 29, 16))["stale"]
    assert g.status_view(PREFS, state, now=datetime(2026, 10, 30, 9))["stale"]


# --- non-billable entries ----------------------------------------------------


def _non_billable(entry):
    return {**entry, "billable": False}


def test_plan_never_pushes_non_billable_entries():
    entries = [
        _entry(1, _local(2026, 10, 6, 9), 60, "billable"),
        _non_billable(_entry(2, _local(2026, 10, 6, 11), 30, "own bug")),
    ]
    plan = g.plan_pushes(entries, PROJECT_ID, {"pushed": {}}, date(2026, 10, 5), date(2026, 10, 11))
    assert [item["toggl_id"] for item in plan] == ["1"]


def test_run_skips_non_billable_entries():
    entries = [
        _entry(1, _local(2026, 10, 6, 9), 60, "billable"),
        _non_billable(_entry(2, _local(2026, 10, 7, 9), 120, "own bug")),
    ]
    session, (state, summary) = _run(entries)
    assert summary["pushed"] == 1
    assert summary["hours"] == 1.0
    assert list(state["pushed"]) == ["1"]
    assert len(session.added) == 1


def test_compare_hours_leaves_non_billable_out_of_toggl_side():
    start = _local(2026, 10, 6, 9)
    entries = [
        _entry(1, start, 60, "billable"),
        _non_billable(_entry(2, start + timedelta(hours=2), 30, "own bug")),
    ]
    out = g.compare_hours(entries, PROJECT_ID, [])
    assert out["toggl_hours"] == 1.0
    assert out["toggl_non_billable_hours"] == 0.5
    assert [e["toggl_id"] for e in out["toggl_only"]] == ["1"]


def test_drift_flags_pushed_entry_later_marked_non_billable():
    start = _local(2026, 10, 6, 9)
    state = {"pushed": {"1": {"day": "2026-10-06", "start": start.isoformat(timespec="seconds"),
                              "stop": (start + timedelta(hours=1)).isoformat(timespec="seconds")}}}
    entries = [_non_billable(_entry(1, start, 60))]
    issues = g.find_drift(entries, PROJECT_ID, state, date(2026, 10, 5), date(2026, 10, 11))
    assert [i["kind"] for i in issues] == ["non_billable"]
    assert "non-billable" in issues[0]["message"]


# --- monthly cap ---------------------------------------------------------------


CAPPED_PREFS = {**PREFS, "projects": {"Acme": {"billing_type": "hourly_with_cap",
                                                "hourly_rate": 100, "cap_hours": 4}}}


def _ledger(day, start_hour, hours, shift_id=None):
    start = _local(*day, start_hour)
    return {"shift_id": shift_id or f"s{day[1]}-{day[2]}-{start_hour}", "day": start.date().isoformat(),
            "start": start.isoformat(timespec="seconds"),
            "stop": (start + timedelta(hours=hours)).isoformat(timespec="seconds"),
            "pushed_at": "2026-10-05T09:00:00"}


def _run_capped(entries, state, session=None, **kwargs):
    session = session or FakeSession()
    return session, g.run_sync(
        CAPPED_PREFS, state, now=datetime(2026, 10, 12, 9, 30),
        session_factory=_factory(session),
        entries_provider=lambda start, end: entries,
        projects_provider=lambda: {PROJECT_ID: {"name": "Acme"}},
        **kwargs,
    )


def test_cap_comes_from_the_gusto_projects_capped_definition():
    assert g.cap_hours(CAPPED_PREFS) == 4.0
    assert g.cap_hours(PREFS) is None
    fixed = {**PREFS, "projects": {"Acme": {"billing_type": "fixed_monthly", "monthly_amount": 1}}}
    assert g.cap_hours(fixed) is None


def test_the_entry_crossing_the_cap_is_split_and_the_tail_held():
    # 2.5h already in Gusto for October; cap 4h. The 1h on Oct 6 fits (3.5h).
    # The 1h on Oct 7 crosses it: its first 30 min fill the cap exactly, the
    # other 30 min are held, and the 0.5h on Oct 8 is held too even though
    # it would fit: the surplus is always the month's tail.
    state = {"pushed": {"900": _ledger((2026, 10, 1), 9, 2.5)}}
    entries = [_entry(1, _local(2026, 10, 6, 9), 60, "a"),
               _entry(2, _local(2026, 10, 7, 9), 60, "b"),
               _entry(3, _local(2026, 10, 8, 9), 30, "c")]
    session, (state, summary) = _run_capped(entries, state)

    assert [a["note"] for a in session.added] == ["a", "b"]
    split = session.added[1]
    assert (split["clock_in"], split["clock_out"]) == (_local(2026, 10, 7, 9), _local(2026, 10, 7, 9, 30))
    assert summary["held"] == [{"month": "2026-10", "hours": 1.0, "entries": 2, "room": 0.0}]
    assert state["held"] == summary["held"]
    assert set(state["pushed"]) == {"900", "1", "2"}
    assert state["pushed"]["2"]["partial"] and state["pushed"]["2"]["toggl_stop"].startswith("2026-10-07T10:00")
    assert g.pushed_hours_by_month(state)["2026-10"] == 4.0
    assert "Oct: 4h cap filled, 1h over not pushed" in g.describe_result(summary)

    # The split entry is not "changed in Toggl", and the next run offers only
    # its held tail (still held: the month is full).
    assert g.find_drift(entries, PROJECT_ID, state, date(2026, 10, 2), date(2026, 10, 31)) == []
    session, (state, summary) = _run_capped(entries, state)
    assert session.added == []
    assert summary["held"] == [{"month": "2026-10", "hours": 1.0, "entries": 2, "room": 0.0}]

    # Pushing the surplus later sends the tail as its own shift.
    session, (state, summary) = _run_capped(entries, state, allow_surplus=True)
    assert [(a["note"], a["clock_in"]) for a in session.added] == [
        ("b", _local(2026, 10, 7, 9, 30)), ("c", _local(2026, 10, 8, 9))]
    assert state["pushed"]["2"]["rest"]["stop"].startswith("2026-10-07T10:00")
    assert g.find_drift(entries, PROJECT_ID, state, date(2026, 10, 2), date(2026, 10, 31)) == []
    assert g.pushed_hours_by_month(state)["2026-10"] == 5.0


def test_split_entry_edited_in_toggl_is_flagged():
    state = {"pushed": {"900": _ledger((2026, 10, 1), 9, 3.5)}}
    entries = [_entry(2, _local(2026, 10, 7, 9), 60, "b")]
    _, (state, _) = _run_capped(entries, state)
    assert state["pushed"]["2"]["partial"]
    longer = [_entry(2, _local(2026, 10, 7, 9), 75, "b")]
    issues = g.find_drift(longer, PROJECT_ID, state, date(2026, 10, 2), date(2026, 10, 31))
    assert [i["kind"] for i in issues] == ["changed"]


class OverlapSession(FakeSession):
    """Gusto refuses a shift that overlaps one it already has."""

    def list_shifts(self, tracker_id, start, end):
        return list(self.existing) + [
            {"id": a["id"], "clock_in": a["clock_in"].isoformat(), "clock_out": a["clock_out"].isoformat()}
            for a in self.added]

    def add_shift(self, tracker_id, clock_in, clock_out, note, timezone):
        for a in self.added:
            if clock_in < a["clock_out"] and a["clock_in"] < clock_out:
                raise gusto_client.GustoError("overlaps another shift")
        return super().add_shift(tracker_id, clock_in, clock_out, note, timezone)


def test_two_overlapping_entries_never_share_one_gusto_shift():
    start = _local(2026, 10, 7, 14)
    entries = [_entry(1, start, 30, "design review"), _entry(2, start, 30, "code review")]
    session, (state, summary) = _run(entries, session=OverlapSession(),
                                     window=(date(2026, 10, 5), date(2026, 10, 11)))
    assert [a["note"] for a in session.added] == ["design review"]
    assert set(state["pushed"]) == {"1"}
    assert [i["kind"] for i in summary["issues"]] == ["rejected"]


def test_hours_count_each_gusto_shift_once():
    # An older ledger recorded two overlapping entries against one shift.
    state = {"pushed": {"1": _ledger((2026, 11, 12), 14, 0.5, shift_id="488"),
                        "2": _ledger((2026, 11, 12), 14, 0.5, shift_id="488"),
                        "3": _ledger((2026, 11, 13), 9, 1)}}
    assert g.pushed_hours_by_month(state) == {"2026-11": 1.5}


def test_push_surplus_sends_the_held_hours_and_says_so():
    state = {"pushed": {"900": _ledger((2026, 10, 1), 9, 3.5)}}
    entries = [_entry(1, _local(2026, 10, 6, 9), 60, "a")]
    session, (state, summary) = _run_capped(entries, state, allow_surplus=True)

    assert [a["note"] for a in session.added] == ["a"]
    assert summary["held"] == [] and state["held"] == []
    assert summary["over_cap_hours"] == 1.0
    assert "1.00h of it over the cap" in g.describe_result(summary)


def test_each_month_has_its_own_cap_and_gusto_shifts_are_never_held():
    state = {"pushed": {"900": _ledger((2026, 10, 1), 9, 4)}}
    adopted_start = _local(2026, 10, 7, 9)
    session = FakeSession(existing=[{"id": "77", "clock_in": adopted_start.isoformat(),
                                     "clock_out": (adopted_start + timedelta(hours=1)).isoformat()}])
    entries = [_entry(1, adopted_start, 60, "already there"),
               _entry(2, _local(2026, 10, 8, 9), 60, "october surplus"),
               _entry(3, _local(2026, 11, 2, 9), 60, "november")]
    session, (state, summary) = _run_capped(entries, state, session=session,
                                            window=(date(2026, 10, 1), date(2026, 11, 2)))

    assert [a["note"] for a in session.added] == ["november"]
    assert state["pushed"]["1"]["shift_id"] == "77"
    assert summary["held"] == [{"month": "2026-10", "hours": 1.0, "entries": 1, "room": 0.0}]


def test_dry_run_reports_held_hours_without_saving_them():
    state = {"pushed": {"900": _ledger((2026, 10, 1), 9, 4)}}
    session, (new_state, summary) = _run_capped([_entry(1, _local(2026, 10, 6, 9), 60)], state,
                                                dry_run=True)
    assert session.added == [] and summary["held"][0]["hours"] == 1.0
    assert "held" not in new_state
    assert "Oct: 4h cap filled, 1h over not pushed" in g.describe_result(summary)


def test_status_alerts_on_held_hours_and_always_shows_hours_in_gusto():
    state = {"synced_through": "2026-10-11", "last_success": "2026-10-12T09:00:05",
             "pushed": {"900": _ledger((2026, 10, 1), 9, 2.5), "901": _ledger((2026, 9, 30), 9, 3)},
             "held": [{"month": "2026-10", "hours": 1.5, "entries": 2, "room": 0.5}]}
    view = g.status_view(CAPPED_PREFS, state, now=datetime(2026, 10, 12, 10))
    assert view["stale"] and view["meta"] == "1.5h over the 4h cap held back"
    assert view["held_hours"] == 1.5
    assert view["held"] == ["Oct: 1.5h over the 4h cap held back (0.5h still fits)"]
    assert view["pushed_line"] == "Gusto: 2.5h of 4h pushed for Oct · as of Mon Oct 12, 9:00 AM"

    fresh = g.status_view(PREFS, {"pushed": {}}, now=datetime(2026, 10, 12, 10))
    assert fresh["pushed_line"] == "Gusto: 0h pushed for Oct · not pushed yet"


# --- cap reached label (toggl_data) --------------------------------------------


def test_cap_reached_lands_inside_the_entry_that_crosses_it():
    import toggl_data

    spans = [(_local(2026, 11, 24, 15, 30), 27.5),   # everything before today
             (_local(2026, 11, 25, 10, 30), 1.5),
             (_local(2026, 11, 25, 13, 0), 1.25)]
    reached = toggl_data._calculate_cap_reached(spans, 30)
    assert reached == {"at": _local(2026, 11, 25, 14, 0).isoformat(timespec="minutes"),
                       "over_hours": 0.25}
    assert toggl_data._calculate_cap_reached(spans[:2], 30) is None
    exact = toggl_data._calculate_cap_reached(spans[:2] + [(_local(2026, 11, 25, 13, 0), 1.0)], 30)
    assert exact["over_hours"] == 0


def test_a_filled_cap_with_only_surplus_left_is_up_to_date():
    # Everything up to the cap is in Gusto; only the surplus is held. That is
    # covered: no orange, no dot, no nagging.
    state = {"synced_through": "2026-10-11", "last_success": "2026-10-12T09:00:05",
             "pushed": {"900": _ledger((2026, 10, 1), 9, 4)},
             "held": [{"month": "2026-10", "hours": 0.5, "entries": 1, "room": 0.0}]}
    now = datetime(2026, 10, 12, 10)
    assert not g.is_stale(now, state)
    view = g.status_view(CAPPED_PREFS, state, now=now)
    assert not view["stale"] and not view["held_alert"]
    assert view["meta"] == "4h cap filled \u00b7 up to date"
    assert view["held"] == ["Oct: 4h cap filled, 0.5h over not pushed"]
    assert g.held_needs_attention(state["held"]) == []
    # Room left under the cap still alerts.
    assert g.held_needs_attention([{"month": "2026-10", "hours": 1, "room": 0.5}])
