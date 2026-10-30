"""Tests for shared entry-cache behavior and cache sync across app features."""

from datetime import date, datetime, timedelta, timezone
import json

import carryover
import hours_csv_export
import pytest
import toggl_data


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        base = datetime(2026, 4, 11, 16, 0, 0, tzinfo=timezone.utc)
        if tz is not None:
            return base.astimezone(tz)
        return base.astimezone()


class _ProjectionDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        base = datetime(2026, 4, 20, 16, 0, 0, tzinfo=timezone.utc)
        if tz is not None:
            return base.astimezone(tz)
        return base.astimezone()


def _day_bounds(day):
    return (
        datetime.combine(day, datetime.min.time()).astimezone(),
        datetime.combine(day, datetime.max.time()).astimezone(),
    )


def _entry(entry_id, project_id, start_iso, duration_seconds, description):
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    stop_dt = start_dt + timedelta(seconds=duration_seconds)
    return {
        "id": entry_id,
        "project_id": project_id,
        "start": start_iso,
        "stop": stop_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration": duration_seconds,
        "description": description,
    }


def _configure_cache_env(monkeypatch, tmp_path, *, prefs=None):
    if prefs is None:
        prefs = {
            "cache_ttl_today": 1800,
            "cache_ttl_projects": 86400,
            "projects": {},
            "retainer_hourly_rates": {},
        }

    cache_dir = tmp_path / "cache"
    entry_cache_dir = cache_dir / "entries" / "by_day"
    entry_cache_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(toggl_data, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(toggl_data, "ENTRY_CACHE_DIR", entry_cache_dir)
    monkeypatch.setattr(toggl_data, "load_preferences", lambda: prefs)
    monkeypatch.setattr(toggl_data, "log_api_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(toggl_data, "_get_api_token", lambda: "token")
    monkeypatch.setattr(toggl_data, "datetime", _FrozenDateTime)

    carryover_file = tmp_path / "carryover.json"
    monkeypatch.setattr(carryover, "CARRYOVER_FILE", carryover_file)

    return cache_dir, entry_cache_dir


def test_get_entries_for_range_populates_day_shards_and_reuses_cache(monkeypatch, tmp_path):
    _configure_cache_env(monkeypatch, tmp_path)

    calls = []
    fetched_entries = [
        _entry(1, 101, "2026-04-10T14:00:00Z", 3600, "Apr 10"),
        _entry(2, 101, "2026-04-11T15:30:00Z", 1800, "Apr 11"),
    ]

    def fake_get(url, auth=None, params=None, timeout=None):
        calls.append((url, params))
        return _FakeResponse(fetched_entries)

    monkeypatch.setattr(toggl_data.requests, "get", fake_get)

    start_dt = datetime(2026, 4, 10, 0, 0, tzinfo=timezone.utc).astimezone()
    end_dt = datetime(2026, 4, 11, 23, 59, tzinfo=timezone.utc).astimezone()

    first = toggl_data.get_entries_for_range(start_dt, end_dt)
    second = toggl_data.get_entries_for_range(start_dt, end_dt)

    assert [entry["description"] for entry in first] == ["Apr 10", "Apr 11"]
    assert [entry["description"] for entry in second] == ["Apr 10", "Apr 11"]
    assert len(calls) == 1
    assert (tmp_path / "cache" / "entries" / "by_day" / "2026-04-10.json").exists()
    assert (tmp_path / "cache" / "entries" / "by_day" / "2026-04-11.json").exists()


def test_refresh_then_export_uses_updated_entry_data(monkeypatch, tmp_path):
    cache_dir, _ = _configure_cache_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        toggl_data,
        "get_projects",
        lambda: {"101": {"name": "Client A", "rate": 150, "billable": True}},
    )

    stale_entry = _entry(1, 101, "2026-04-11T14:00:00Z", 1800, "stale value")
    updated_entry = _entry(1, 101, "2026-04-11T14:00:00Z", 7200, "updated value")
    day = date(2026, 4, 11)
    start_dt, end_dt = _day_bounds(day)

    toggl_data.cache_entries(f"daily_{day.isoformat()}", [stale_entry], start_dt, end_dt)
    toggl_data.cache_entries(
        f"export_{day.isoformat()}_to_{day.isoformat()}",
        [stale_entry],
        start_dt,
        end_dt,
    )

    def fake_get(url, auth=None, params=None, timeout=None):
        return _FakeResponse([updated_entry])

    monkeypatch.setattr(toggl_data.requests, "get", fake_get)

    toggl_data.force_refresh_entries()
    daily = toggl_data.get_daily_earnings()
    exported = hours_csv_export.get_project_entries_for_range(101, "Client A", day, day)

    assert daily["all_projects"][0]["hours"] == 2.0
    assert daily["all_projects"][0]["earnings"] == 300.0
    assert exported[0]["description"] == "updated value"
    assert exported[0]["duration"] == 7200
    assert not (cache_dir / f"export_{day.isoformat()}_to_{day.isoformat()}.json").exists()


def test_auto_carryover_recomputes_from_shared_entry_cache(monkeypatch, tmp_path):
    _configure_cache_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        toggl_data,
        "get_projects",
        lambda: {"101": {"name": "Client A", "rate": None, "billable": False}},
    )

    march_entry = _entry(1, 101, "2026-03-10T15:00:00Z", 21600, "March work")

    def fake_get(url, auth=None, params=None, timeout=None):
        return _FakeResponse([march_entry])

    monkeypatch.setattr(toggl_data.requests, "get", fake_get)

    toggl_data._try_calculate_last_month_carryover({
        "Client A": {
            "billing_type": "fixed_monthly",
            "monthly_amount": 2000,
            "hour_tracking": "required",
            "target_hours": 10,
        }
    })

    record = carryover.get_balance_record("Client A", "2026-03")
    assert record["source"] == "auto"
    assert record["hours"] == -4.0


def test_manual_carryover_override_is_preserved(monkeypatch, tmp_path):
    _configure_cache_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        toggl_data,
        "get_projects",
        lambda: {"101": {"name": "Client A", "rate": None, "billable": False}},
    )

    carryover.set_balance("Client A", "2026-03", 5.0, source="manual")

    march_entry = _entry(1, 101, "2026-03-10T15:00:00Z", 21600, "March work")

    def fake_get(url, auth=None, params=None, timeout=None):
        return _FakeResponse([march_entry])

    monkeypatch.setattr(toggl_data.requests, "get", fake_get)

    toggl_data._try_calculate_last_month_carryover({
        "Client A": {
            "billing_type": "fixed_monthly",
            "monthly_amount": 2000,
            "hour_tracking": "required",
            "target_hours": 10,
        }
    })

    record = carryover.get_balance_record("Client A", "2026-03")
    assert record["source"] == "manual"
    assert record["hours"] == 5.0


def test_clear_all_caches_removes_shared_and_legacy_cache_files(monkeypatch, tmp_path):
    cache_dir, entry_cache_dir = _configure_cache_env(monkeypatch, tmp_path)

    shared_day = entry_cache_dir / "2026-04-11.json"
    shared_day.write_text(json.dumps({"entries": []}))
    (cache_dir / "projects.json").write_text("{}")
    (cache_dir / "export_2026-04-01_to_2026-04-11.json").write_text("{}")
    (cache_dir / "daily_2026-04-11.json").write_text("{}")

    toggl_data.clear_all_caches()

    assert entry_cache_dir.exists()
    assert list(entry_cache_dir.iterdir()) == []
    assert not (cache_dir / "projects.json").exists()
    assert not (cache_dir / "export_2026-04-01_to_2026-04-11.json").exists()
    assert not (cache_dir / "daily_2026-04-11.json").exists()


def test_lbd_billing_cycle_bounds_and_progress_cover_following_month():
    cycle_start, cycle_end = toggl_data.get_lbd_billing_cycle_bounds(date(2026, 4, 10))

    assert cycle_start == date(2026, 4, 11)
    assert cycle_end == date(2026, 5, 31)
    assert toggl_data.get_lbd_cycle_progress(
        date(2026, 4, 10),
        today=date(2026, 4, 20),
    ) == pytest.approx(19.607843137254903)


def test_lbd_projection_uses_remaining_business_days_in_billing_cycle(monkeypatch, tmp_path):
    _configure_cache_env(
        monkeypatch,
        tmp_path,
        prefs={
            "cache_ttl_today": 1800,
            "cache_ttl_projects": 86400,
            "projects": {
                "Client A": {
                    "billing_type": "hourly_with_cap",
                    "hourly_rate": 100,
                    "cap_hours": 200,
                    "last_billed_date": "2026-04-10",
                },
            },
            "retainer_hourly_rates": {},
            "vacation_days_per_month": 0,
        },
    )

    monkeypatch.setattr(toggl_data, "datetime", _ProjectionDateTime)
    monkeypatch.setattr(toggl_data, "get_projects", lambda: {"101": {"name": "Client A"}})
    monkeypatch.setattr(
        toggl_data,
        "calculate_period_earnings",
        lambda period: {"total": 1000, "fixed_earnings": 0} if period == "monthly" else {},
    )
    monkeypatch.setattr(toggl_data, "calculate_business_days", lambda year, month: 22)
    monkeypatch.setattr(toggl_data, "get_worked_days_this_month", lambda: [])
    monkeypatch.setattr(
        toggl_data,
        "get_entries_since_date",
        lambda start_date: [
            _entry(1, 101, "2026-04-11T14:00:00Z", 18000, "Apr 11"),
            _entry(2, 101, "2026-04-14T14:00:00Z", 18000, "Apr 14"),
        ],
    )

    projection = toggl_data.calculate_monthly_projection()
    remaining_days = toggl_data.get_lbd_remaining_business_days(
        date(2026, 4, 10),
        today=date(2026, 4, 20),
    )
    expected_projected_variable = (10 + (10 / 2) * remaining_days) * 100

    assert remaining_days == 29
    assert projection["projected_variable"] == expected_projected_variable
    assert projection["projected_earnings"] == expected_projected_variable


def _frozen_at(year, month, day, hour=16):
    class _At(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
            return base.astimezone(tz) if tz is not None else base.astimezone()
    return _At


def _projection_env(monkeypatch, tmp_path, *, now, variable_earnings, worked_days, days_off):
    import calendar_days_off

    _configure_cache_env(
        monkeypatch,
        tmp_path,
        prefs={
            "cache_ttl_today": 1800,
            "cache_ttl_projects": 86400,
            "projects": {
                "Client F": {"billing_type": "fixed_monthly", "monthly_amount": 1000,
                             "hour_tracking": "none"},
            },
            "retainer_hourly_rates": {},
            "monthly_rev_share": {f"{now.year:04d}-{now.month:02d}": 500},
        },
    )
    monkeypatch.setattr(toggl_data, "datetime", _frozen_at(now.year, now.month, now.day))
    monkeypatch.setattr(toggl_data, "get_projects", lambda: {})
    monkeypatch.setattr(
        toggl_data,
        "calculate_period_earnings",
        lambda period: {"total": 1000 + variable_earnings, "fixed_earnings": 1000, "projects": []},
    )
    monkeypatch.setattr(toggl_data, "get_worked_days_this_month", lambda: set(worked_days))
    monkeypatch.setattr(calendar_days_off, "get_month_days_off", lambda y, m, prefs=None: days_off)


def test_projection_at_month_end_equals_earned_even_when_worked_days_exceed_workable(monkeypatch, tmp_path):
    # Worked a weekend day and days marked off, so worked days (19) exceed
    # workable days (22 - 5 = 17). Nothing is left to pace: projection == earned.
    worked = ["2026-10-%02d" % d for d in
              (5, 6, 7, 8, 9, 12, 13, 14, 19, 20, 21, 22, 23, 24, 26, 27, 28, 29, 30)]
    _projection_env(
        monkeypatch, tmp_path, now=date(2026, 10, 30), variable_earnings=8317.5,
        worked_days=worked,
        days_off={"source": "calendar", "vacation_days": 5,
                  "dates": ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-19", "2026-10-27"]},
    )

    projection = toggl_data.calculate_monthly_projection()

    assert projection["remaining_workable_days"] == 0
    assert projection["projected_variable"] == pytest.approx(8317.5)
    assert projection["projected_earnings"] == pytest.approx(1000 + 500 + 8317.5)


def test_projection_paces_only_remaining_days_excluding_calendar_days_off(monkeypatch, tmp_path):
    # Tue Oct 20, already worked today. Business days ahead: Oct 21-23, 26-30
    # (8), minus the Oct 27 day off = 7.
    worked = ["2026-10-%02d" % d for d in (6, 7, 8, 9, 12, 13, 14, 15, 16, 20)]
    _projection_env(
        monkeypatch, tmp_path, now=date(2026, 10, 20), variable_earnings=1000,
        worked_days=worked,
        days_off={"source": "calendar", "vacation_days": 2, "dates": ["2026-10-19", "2026-10-27"]},
    )

    projection = toggl_data.calculate_monthly_projection()

    assert projection["remaining_workable_days"] == 7
    assert projection["projected_variable"] == pytest.approx(1000 + 100 * 7)


def test_projection_counts_today_until_work_is_logged_and_spreads_static_days_off(monkeypatch, tmp_path):
    # Fri Oct 30 with nothing logged today: 1 business day ahead, scaled by the
    # flat estimate's workable share (22 - 4) / 22.
    _projection_env(
        monkeypatch, tmp_path, now=date(2026, 10, 30), variable_earnings=900,
        worked_days=["2026-10-28", "2026-10-29"],
        days_off={"source": "static", "vacation_days": 4, "dates": []},
    )

    projection = toggl_data.calculate_monthly_projection()

    assert projection["remaining_workable_days"] == pytest.approx(18 / 22)
    assert projection["projected_variable"] == pytest.approx(900 + 450 * 18 / 22)


def test_required_fixed_monthly_earns_monthly_amount_regardless_of_hours(monkeypatch, tmp_path):
    _configure_cache_env(
        monkeypatch,
        tmp_path,
        prefs={
            "cache_ttl_today": 1800,
            "cache_ttl_projects": 86400,
            "projects": {
                "Client R": {"billing_type": "fixed_monthly", "monthly_amount": 1500,
                             "hour_tracking": "required", "target_hours": 10},
            },
            "retainer_hourly_rates": {},
        },
    )
    monkeypatch.setattr(toggl_data, "get_projects", lambda: {"101": {"name": "Client R"}})
    over = [_entry(1, 101, "2026-04-06T14:00:00Z", 12 * 3600, "over target")]
    under = [_entry(2, 101, "2026-04-06T14:00:00Z", 6 * 3600, "under target")]

    monkeypatch.setattr(toggl_data, "get_entries_with_cache", lambda period: over)
    assert toggl_data.calculate_period_earnings("monthly")["total"] == pytest.approx(1500)

    monkeypatch.setattr(toggl_data, "get_entries_with_cache", lambda period: under)
    assert toggl_data.calculate_period_earnings("monthly")["total"] == pytest.approx(1500)

    # Daily/weekly: hours beyond the monthly target earn nothing, so the
    # periods add up to the fixed fee.
    monkeypatch.setattr(toggl_data, "get_entries_with_cache", lambda period: over)
    monkeypatch.setattr(toggl_data, "_get_monthly_project_hours", lambda *a, **k: 12.0)
    assert toggl_data.calculate_period_earnings("weekly")["total"] == pytest.approx(1500)


# --- non-billable entries --------------------------------------------------


def _nb(entry):
    return {**entry, "billable": False}


def _non_billable_env(monkeypatch, tmp_path, projects):
    _configure_cache_env(
        monkeypatch,
        tmp_path,
        prefs={
            "cache_ttl_today": 1800,
            "cache_ttl_projects": 86400,
            "projects": projects,
            "retainer_hourly_rates": {},
        },
    )


def test_is_billable_entry_only_excludes_explicit_false():
    assert toggl_data.is_billable_entry({"billable": True})
    assert toggl_data.is_billable_entry({})
    assert not toggl_data.is_billable_entry({"billable": False})


def test_non_billable_time_earns_nothing_and_is_reported_separately(monkeypatch, tmp_path):
    _non_billable_env(monkeypatch, tmp_path, {
        "Client S": {"billing_type": "fixed_monthly", "monthly_amount": 3000,
                     "hour_tracking": "soft", "target_hours": 30},
        "Client C": {"billing_type": "hourly_with_cap", "hourly_rate": 100, "cap_hours": 5},
    })
    monkeypatch.setattr(toggl_data, "get_projects", lambda: {
        "101": {"name": "Client S"},
        "102": {"name": "Client C"},
        "103": {"name": "Client H", "billable": True, "rate": 100},
    })
    entries = [
        _entry(1, 101, "2026-04-06T14:00:00Z", 10 * 3600, "billable"),
        _nb(_entry(2, 101, "2026-04-07T14:00:00Z", 5 * 3600, "own bug")),
        _entry(3, 102, "2026-04-06T14:00:00Z", 4 * 3600, "billable"),
        _nb(_entry(4, 102, "2026-04-07T14:00:00Z", 3 * 3600, "own bug")),
        _entry(5, 103, "2026-04-06T14:00:00Z", 2 * 3600, "billable"),
        _nb(_entry(6, 103, "2026-04-07T14:00:00Z", 1 * 3600, "own bug")),
    ]
    monkeypatch.setattr(toggl_data, "get_entries_with_cache", lambda period: entries)

    data = toggl_data.calculate_period_earnings("monthly")
    by_name = {p["name"]: p for p in data["all_projects"]}

    # Soft retainer: 10 billable h x $100, not 15h; the 5h is only shown.
    assert by_name["Client S"]["hours"] == pytest.approx(10)
    assert by_name["Client S"]["non_billable_hours"] == pytest.approx(5)
    assert by_name["Client S"]["total_hours"] == pytest.approx(15)
    assert by_name["Client S"]["earnings"] == pytest.approx(1000)
    # Capped: 4 billable h stay under the 5h cap; non-billable does not fill it.
    assert by_name["Client C"]["hours"] == pytest.approx(4)
    assert by_name["Client C"]["earnings"] == pytest.approx(400)
    assert by_name["Client C"]["cap_fill_date"] is None
    # Toggl-rate hourly: only billable hours earn.
    assert by_name["Client H"]["earnings"] == pytest.approx(200)

    assert data["total"] == pytest.approx(1600)
    assert data["hours"] == pytest.approx(16)
    assert data["non_billable_hours"] == pytest.approx(9)
    assert data["total_hours"] == pytest.approx(25)


def test_project_with_only_non_billable_time_is_listed_with_zero_hours(monkeypatch, tmp_path):
    _non_billable_env(monkeypatch, tmp_path, {})
    monkeypatch.setattr(toggl_data, "get_projects",
                        lambda: {"103": {"name": "Client H", "billable": True, "rate": 100}})
    entries = [_nb(_entry(1, 103, "2026-04-11T14:00:00Z", 1800, "own bug"))]
    monkeypatch.setattr(toggl_data, "get_entries_with_cache", lambda period: entries)

    data = toggl_data.calculate_period_earnings("daily")

    (project,) = data["all_projects"]
    assert project["hours"] == 0
    assert project["non_billable_hours"] == pytest.approx(0.5)
    assert project["earnings"] == 0
    # The day's timeline still shows the block, marked non-billable.
    assert [b["billable"] for b in project["time_blocks"]] == [False]


def test_daily_soft_retainer_counts_only_billable_month_hours_toward_target(monkeypatch, tmp_path):
    _non_billable_env(monkeypatch, tmp_path, {
        "Client S": {"billing_type": "fixed_monthly", "monthly_amount": 1000,
                     "hour_tracking": "soft", "target_hours": 10},
    })
    monkeypatch.setattr(toggl_data, "get_projects", lambda: {"101": {"name": "Client S"}})
    month = [
        _entry(1, 101, "2026-04-06T14:00:00Z", 8 * 3600, "billable"),
        _nb(_entry(2, 101, "2026-04-07T14:00:00Z", 6 * 3600, "own bug")),
        _entry(3, 101, "2026-04-11T14:00:00Z", 1 * 3600, "today"),
    ]
    monkeypatch.setattr(toggl_data, "get_entries_with_cache",
                        lambda period: month if period == "monthly" else month[2:])

    # 8 billable h before today leave 2h of target; the 6 non-billable h do
    # not use it up, so today's hour still earns.
    assert toggl_data.calculate_period_earnings("daily")["total"] == pytest.approx(100)


def test_lbd_unbilled_hours_and_cap_fill_skip_non_billable(monkeypatch, tmp_path):
    _non_billable_env(monkeypatch, tmp_path, {
        "Client A": {"billing_type": "hourly_with_cap", "hourly_rate": 100,
                     "cap_hours": 6, "last_billed_date": "2026-03-31"},
    })
    projects = {"101": {"name": "Client A"}}
    monkeypatch.setattr(toggl_data, "get_projects", lambda: projects)
    entries = [
        _entry(1, 101, "2026-04-06T14:00:00Z", 4 * 3600, "billable"),
        _nb(_entry(2, 101, "2026-04-07T14:00:00Z", 4 * 3600, "own bug")),
        _entry(3, 101, "2026-04-08T14:00:00Z", 1 * 3600, "billable"),
    ]
    monkeypatch.setattr(toggl_data, "get_entries_since_date", lambda start: entries)
    monkeypatch.setattr(toggl_data, "get_entries_with_cache", lambda period: entries)

    data = toggl_data.calculate_period_earnings("monthly")
    (project,) = data["all_projects"]

    assert project["hours"] == pytest.approx(5)
    assert project["non_billable_hours"] == pytest.approx(4)
    assert project["earnings"] == pytest.approx(500)
    assert project["cap_fill_date"] is None
    assert toggl_data.compute_lbd_cap_fill_date(
        "Client A", date(2026, 3, 31), 6, projects) is None


def test_auto_carryover_ignores_non_billable_hours(monkeypatch, tmp_path):
    _configure_cache_env(monkeypatch, tmp_path)
    monkeypatch.setattr(toggl_data, "get_projects", lambda: {"101": {"name": "Client A"}})
    march = [
        _entry(1, 101, "2026-03-10T15:00:00Z", 9 * 3600, "billable"),
        _nb(_entry(2, 101, "2026-03-11T15:00:00Z", 3 * 3600, "own bug")),
    ]
    monkeypatch.setattr(toggl_data, "get_entries_for_range", lambda start, end: march)

    toggl_data._try_calculate_last_month_carryover({
        "Client A": {"billing_type": "fixed_monthly", "monthly_amount": 2000,
                     "hour_tracking": "required", "target_hours": 10},
    })

    # 9 billable h against 10h: 1h owed, not 2h over.
    assert carryover.get_balance("Client A", "2026-03") == pytest.approx(-1.0)


def test_days_with_only_non_billable_time_are_not_worked_days(monkeypatch, tmp_path):
    _non_billable_env(monkeypatch, tmp_path, {})
    monkeypatch.setattr(toggl_data, "get_projects",
                        lambda: {"103": {"name": "Client H", "billable": True, "rate": 100}})
    entries = [
        _entry(1, 103, "2026-04-06T14:00:00Z", 3600, "billable"),
        _nb(_entry(2, 103, "2026-04-07T14:00:00Z", 3600, "own bug")),
    ]
    monkeypatch.setattr(toggl_data, "get_entries_with_cache", lambda period: entries)

    assert toggl_data.get_worked_days_this_month() == {"2026-04-06"}


def test_csv_export_bills_only_billable_entries():
    rows = hours_csv_export.build_rows([
        _entry(1, 101, "2026-04-06T14:00:00Z", 3600, "billable"),
        _nb(_entry(2, 101, "2026-04-07T14:00:00Z", 3600, "own bug")),
    ], 150)

    assert [r["Description"] for r in rows] == ["billable"]
    assert rows[0]["Money Billed (USD)"] == "150.0"
