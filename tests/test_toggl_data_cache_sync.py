"""Tests for shared entry-cache behavior and cache sync across app features."""

from datetime import date, datetime, timedelta, timezone
import json

import carryover
import hours_csv_export
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
