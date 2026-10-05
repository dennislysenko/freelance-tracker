"""Cache-only mode: the MCP server must never spend Toggl API calls."""

from datetime import date, datetime, timedelta, timezone
import json
import os
import time

import pytest

import toggl_data


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        base = datetime(2026, 4, 11, 16, 0, 0, tzinfo=timezone.utc)
        if tz is not None:
            return base.astimezone(tz)
        return base.astimezone()


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


@pytest.fixture
def cache_only_env(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    entry_cache_dir = cache_dir / "entries" / "by_day"
    entry_cache_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(toggl_data, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(toggl_data, "ENTRY_CACHE_DIR", entry_cache_dir)
    monkeypatch.setattr(toggl_data, "CACHE_ONLY", True)
    monkeypatch.setattr(toggl_data, "load_preferences",
                        lambda: {"cache_ttl_today": 1, "cache_ttl_projects": 1})
    monkeypatch.setattr(toggl_data, "log_api_request", lambda *a, **k: None)
    monkeypatch.setattr(toggl_data, "datetime", _FrozenDateTime)

    def explode(*args, **kwargs):
        raise AssertionError("cache-only mode must never call Toggl")

    monkeypatch.setattr(toggl_data.requests, "get", explode)
    monkeypatch.setattr(toggl_data.requests, "post", explode)
    return cache_dir, entry_cache_dir


def _write_shard(entry_cache_dir, day, entries, age_seconds=0):
    path = entry_cache_dir / f"{day.isoformat()}.json"
    path.write_text(json.dumps({"version": 1, "day": day.isoformat(), "entries": entries}))
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


def test_stale_today_shard_is_served_not_refetched(cache_only_env):
    _, entry_cache_dir = cache_only_env
    today = date(2026, 4, 11)
    # TTL is 1s and the shard is an hour old: normally a refetch, here served.
    _write_shard(entry_cache_dir, today,
                 [_entry(1, 101, "2026-04-11T14:00:00Z", 3600, "today")],
                 age_seconds=3600)

    start_dt = datetime.combine(today, datetime.min.time()).astimezone()
    end_dt = datetime.combine(today, datetime.max.time()).astimezone()
    entries = toggl_data.get_entries_for_range(start_dt, end_dt)
    assert [e["description"] for e in entries] == ["today"]


def test_absent_shard_raises_cache_miss(cache_only_env):
    _, entry_cache_dir = cache_only_env
    _write_shard(entry_cache_dir, date(2026, 4, 10), [])
    start_dt = datetime(2026, 4, 10, 0, 0).astimezone()
    end_dt = datetime(2026, 4, 12, 23, 59).astimezone()

    with pytest.raises(toggl_data.CacheMissError) as info:
        toggl_data.get_entries_for_range(start_dt, end_dt)
    assert info.value.days == [date(2026, 4, 11), date(2026, 4, 12)]
    assert "2026-04-11 to 2026-04-12" in str(info.value)
    assert "Refresh Now" in str(info.value)


def test_force_refresh_is_ignored_in_cache_only(cache_only_env):
    _, entry_cache_dir = cache_only_env
    day = date(2026, 4, 10)
    _write_shard(entry_cache_dir, day, [_entry(1, 101, "2026-04-10T14:00:00Z", 60, "x")])
    start_dt = datetime.combine(day, datetime.min.time()).astimezone()
    end_dt = datetime.combine(day, datetime.max.time()).astimezone()
    entries = toggl_data.get_entries_for_range(start_dt, end_dt, force_refresh=True)
    assert len(entries) == 1


def test_projects_cache_served_past_ttl(cache_only_env):
    cache_dir, _ = cache_only_env
    projects_file = cache_dir / "projects.json"
    projects_file.write_text(json.dumps({"101": {"name": "Client A", "rate": 100, "billable": True}}))
    old = time.time() - 10 * 86400
    os.utime(projects_file, (old, old))

    assert toggl_data.get_projects()["101"]["name"] == "Client A"


def test_projects_missing_raises_cache_miss(cache_only_env):
    with pytest.raises(toggl_data.CacheMissError) as info:
        toggl_data.get_projects()
    assert "projects are not cached" in str(info.value)


def test_flag_reads_env_var(monkeypatch):
    import importlib
    monkeypatch.setenv("FREELANCE_TRACKER_CACHE_ONLY", "1")
    reloaded = importlib.reload(toggl_data)
    try:
        assert reloaded.CACHE_ONLY is True
    finally:
        monkeypatch.delenv("FREELANCE_TRACKER_CACHE_ONLY")
        importlib.reload(toggl_data)
