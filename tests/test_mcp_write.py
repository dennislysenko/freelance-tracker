"""Tests for the MCP write path: one entry at a time, always confirmed."""

from datetime import date, datetime, timedelta, timezone
import json

import pytest

import mcp_server as S
import mcp_write
import toggl_data


TODAY = date(2026, 10, 8)


def _iso(day, hhmm, tz_hours=0):
    h, m = (int(p) for p in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m,
                    tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry(entry_id, project_id, start_iso, minutes, description="", billable=True):
    return {
        "id": entry_id,
        "project_id": project_id,
        "start": start_iso,
        "duration": minutes * 60,
        "description": description,
        "billable": billable,
    }


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Cache on disk, Toggl stubbed, writes enabled."""
    prefs = {"mcp_enabled": True, "mcp_anonymize": False, "mcp_write_enabled": True}
    monkeypatch.setattr(S, "load_preferences", lambda: prefs)

    entry_dir = tmp_path / "by_day"
    entry_dir.mkdir()
    monkeypatch.setattr(toggl_data, "ENTRY_CACHE_DIR", entry_dir)
    monkeypatch.setattr(toggl_data, "CACHE_DIR", tmp_path)

    projects = {"11": {"name": "Acme Corp"}, "22": {"name": "Globex"}}
    monkeypatch.setattr(toggl_data, "get_projects", lambda: projects)

    # Local midday so the day shard and the local date agree regardless of tz.
    existing = _entry(901, 11, _iso(TODAY, "12:00"), 60, "existing work")
    (entry_dir / f"{TODAY.isoformat()}.json").write_text(json.dumps({
        "version": toggl_data.ENTRY_CACHE_VERSION,
        "day": TODAY.isoformat(),
        "entries": [existing],
    }))

    calls = []

    def fake_create(**kwargs):
        calls.append(("create", kwargs))
        return _entry(
            555, kwargs.get("project_id"),
            kwargs["start"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            kwargs["duration_seconds"] // 60,
            kwargs["description"], kwargs["billable"],
        )

    def fake_update(entry_id, **fields):
        calls.append(("update", entry_id, fields))
        return _entry(
            entry_id, fields.get("project_id", 11),
            fields["start"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            fields["duration_seconds"] // 60,
            fields["description"], fields["billable"],
        )

    def fake_delete(entry_id):
        calls.append(("delete", entry_id))
        return True

    monkeypatch.setattr(toggl_data, "create_time_entry", fake_create)
    monkeypatch.setattr(toggl_data, "update_time_entry", fake_update)
    monkeypatch.setattr(toggl_data, "delete_time_entry", fake_delete)
    monkeypatch.setattr(mcp_write, "LOOKUP_WINDOW_DAYS", 5)
    # Writes announce themselves to a running menu bar app. Tests must not
    # poke the real one, nor touch the real cache directory's marker file.
    announced = []
    monkeypatch.setattr(mcp_write, "notify_data_changed",
                        lambda reason="": announced.append(reason))
    mcp_write.clear_pending()

    # The entry's own local day, so lookups that key off "today" find it.
    local_day = datetime.fromisoformat(
        existing["start"].replace("Z", "+00:00")
    ).astimezone().date()
    if local_day != TODAY:
        (entry_dir / f"{local_day.isoformat()}.json").write_text(json.dumps({
            "version": toggl_data.ENTRY_CACHE_VERSION,
            "day": local_day.isoformat(),
            "entries": [existing],
        }))

    return {"prefs": prefs, "calls": calls, "dir": entry_dir, "day": local_day,
            "announced": announced}


def _shard(env, day=None):
    path = env["dir"] / f"{(day or env['day']).isoformat()}.json"
    return json.loads(path.read_text())["entries"]


# --- gating ---------------------------------------------------------------

def test_writes_refused_until_enabled(env):
    env["prefs"]["mcp_write_enabled"] = False
    for out in (
        S.log_time("2026-10-08", "09:00", 60, "Acme Corp"),
        S.update_entry(901, description="x"),
        S.delete_entry(901),
    ):
        assert out["error_type"] == "WriteDisabledError"
        assert "Settings" in out["error"]
    assert env["calls"] == []


def test_writes_refused_while_anonymized(env):
    env["prefs"]["mcp_anonymize"] = True
    out = S.log_time("2026-10-08", "09:00", 60, "Acme Corp")
    assert out["error_type"] == "WriteDisabledError"
    assert "nonymi" in out["error"]
    assert env["calls"] == []


def test_server_disabled_blocks_writes_too(env):
    env["prefs"]["mcp_enabled"] = False
    assert S.log_time("2026-10-08", "09:00", 60, "Acme Corp")["error_type"] == "DisabledError"
    assert env["calls"] == []


# --- two-step confirmation ------------------------------------------------

def test_log_previews_without_writing(env):
    out = S.log_time("2026-10-08", "09:00", 90, "Acme Corp", description="planning")
    assert out["needs_confirmation"] is True
    assert out["confirm"]
    assert "1.50h" in out["summary"] and "Acme Corp" in out["summary"]
    assert out["entry"]["duration_minutes"] == 90
    assert env["calls"] == [], "preview must not reach Toggl"


def test_log_writes_once_confirmed(env):
    preview = S.log_time("2026-10-08", "09:00", 90, "Acme Corp", description="planning")
    out = S.log_time("2026-10-08", "09:00", 90, "Acme Corp", description="planning",
                     confirm=preview["confirm"])
    assert out["ok"] is True
    assert out["toggl_api_calls"] == 1
    assert [c[0] for c in env["calls"]] == ["create"]
    kwargs = env["calls"][0][1]
    assert kwargs["duration_seconds"] == 5400
    assert kwargs["project_id"] == 11
    assert kwargs["start"].tzinfo is not None


def test_confirmation_is_single_use(env):
    preview = S.log_time("2026-10-08", "09:00", 60, "Acme Corp")
    S.log_time("2026-10-08", "09:00", 60, "Acme Corp", confirm=preview["confirm"])
    again = S.log_time("2026-10-08", "09:00", 60, "Acme Corp", confirm=preview["confirm"])
    assert again["error_type"] == "ConfirmationError"
    assert len([c for c in env["calls"] if c[0] == "create"]) == 1


def test_token_cannot_be_replayed_with_different_arguments(env):
    preview = S.log_time("2026-10-08", "09:00", 60, "Acme Corp")
    out = S.log_time("2026-10-08", "09:00", 480, "Globex", confirm=preview["confirm"])
    assert out["error_type"] == "ConfirmationError"
    assert "different change" in out["error"]
    assert env["calls"] == []


def test_unknown_token_is_refused(env):
    out = S.log_time("2026-10-08", "09:00", 60, "Acme Corp", confirm="deadbeef")
    assert out["error_type"] == "ConfirmationError"
    assert env["calls"] == []


def test_token_expires(env, monkeypatch):
    preview = S.log_time("2026-10-08", "09:00", 60, "Acme Corp")
    real_time = mcp_write.time.time
    monkeypatch.setattr(mcp_write.time, "time",
                        lambda: real_time() + mcp_write.CONFIRM_TTL_SECONDS + 1)
    out = S.log_time("2026-10-08", "09:00", 60, "Acme Corp", confirm=preview["confirm"])
    assert out["error_type"] == "ConfirmationError"
    assert "expired" in out["error"]


# --- validation -----------------------------------------------------------

@pytest.mark.parametrize("kwargs, needle", [
    ({"date": "08-10-2026"}, "YYYY-MM-DD"),
    ({"start_time": "9am"}, "HH:MM"),
    ({"duration_minutes": 0}, "greater than zero"),
    ({"duration_minutes": 2000}, "one day"),
    ({"project": "Nope Inc"}, "Unknown project"),
])
def test_bad_arguments_are_reported(env, kwargs, needle):
    args = {"date": "2026-10-08", "start_time": "09:00",
            "duration_minutes": 60, "project": "Acme Corp", **kwargs}
    out = S.log_time(**args)
    assert needle in out["error"]
    assert env["calls"] == []


def test_project_matched_case_insensitively(env):
    out = S.log_time("2026-10-08", "09:00", 60, "acme corp")
    assert "Acme Corp" in out["summary"]


# --- overlaps -------------------------------------------------------------

def test_preview_flags_an_overlapping_entry(env):
    local = datetime.fromisoformat(
        _entry(0, 0, _iso(TODAY, "12:00"), 0)["start"].replace("Z", "+00:00")
    ).astimezone()
    out = S.log_time(local.date().isoformat(), local.strftime("%H:%M"), 60, "Globex")
    assert out["overlaps"][0]["entry_id"] == 901
    assert "overlaps" in out["warning"]


def test_overlap_check_is_skipped_for_uncached_days(env):
    out = S.log_time("2026-10-01", "09:00", 60, "Acme Corp")
    assert "overlaps" not in out
    assert out["needs_confirmation"] is True


# --- update ---------------------------------------------------------------

def test_update_previews_before_and_after(env):
    out = S.update_entry(901, duration_minutes=30, description="trimmed")
    assert out["needs_confirmation"] is True
    assert out["before"]["duration_minutes"] == 60
    assert out["after"]["duration_minutes"] == 30
    assert set(out["changes"]) == {"duration_minutes", "description"}
    assert env["calls"] == []


def test_update_keeps_untouched_fields(env):
    preview = S.update_entry(901, duration_minutes=30)
    out = S.update_entry(901, duration_minutes=30, confirm=preview["confirm"])
    assert out["ok"] is True
    _, entry_id, fields = env["calls"][0]
    assert entry_id == 901
    assert fields["duration_seconds"] == 1800
    assert fields["description"] == "existing work"
    assert fields["project_id"] == 11
    assert _shard(env)[0]["duration"] == 1800


def test_update_with_no_fields_is_rejected(env):
    out = S.update_entry(901)
    assert "Nothing to change" in out["error"]


def test_update_to_identical_values_is_a_no_op(env):
    out = S.update_entry(901, description="existing work")
    assert out["ok"] is True
    assert out["toggl_api_calls"] == 0
    assert env["calls"] == []


def test_update_moving_days_relocates_the_cached_entry(env):
    new_day = env["day"] + timedelta(days=1)
    (env["dir"] / f"{new_day.isoformat()}.json").write_text(json.dumps({
        "version": toggl_data.ENTRY_CACHE_VERSION,
        "day": new_day.isoformat(),
        "entries": [],
    }))
    preview = S.update_entry(901, date=new_day.isoformat())
    out = S.update_entry(901, date=new_day.isoformat(), confirm=preview["confirm"])
    assert out["ok"] is True
    assert _shard(env, env["day"]) == []
    assert [e["id"] for e in _shard(env, new_day)] == [901]


def test_unknown_entry_id_is_reported(env):
    out = S.update_entry(4242, description="x")
    assert "No cached time entry with id 4242" in out["error"]
    assert env["calls"] == []


# --- delete ---------------------------------------------------------------

def test_delete_previews_with_a_warning(env):
    out = S.delete_entry(901)
    assert out["needs_confirmation"] is True
    assert out["entry"]["entry_id"] == 901
    assert "cannot be undone" in out["warning"]
    assert "never loop" in out["warning"]
    assert env["calls"] == []


def test_delete_removes_entry_and_cached_copy(env):
    preview = S.delete_entry(901)
    out = S.delete_entry(901, confirm=preview["confirm"])
    assert out["ok"] is True
    assert out["deleted"]["entry_id"] == 901
    assert env["calls"] == [("delete", 901)]
    assert _shard(env) == []


def test_delete_token_does_not_confirm_a_different_entry(env):
    other = _entry(902, 22, _iso(TODAY, "12:00"), 30)
    toggl_data.upsert_cached_entry(other)
    preview = S.delete_entry(901)
    out = S.delete_entry(902, confirm=preview["confirm"])
    assert out["error_type"] == "ConfirmationError"
    assert env["calls"] == []


# --- failures -------------------------------------------------------------

def test_toggl_failure_is_reported_not_raised(env, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("Toggl API is rate limited (402); time entry not created.")

    monkeypatch.setattr(toggl_data, "create_time_entry", boom)
    preview = S.log_time("2026-10-08", "09:00", 60, "Acme Corp")
    out = S.log_time("2026-10-08", "09:00", 60, "Acme Corp", confirm=preview["confirm"])
    assert out["error_type"] == "RuntimeError"
    assert "rate limited" in out["error"]


# --- surface --------------------------------------------------------------

def test_write_tools_are_registered_and_annotated():
    tools = {t.name: t for t in S.mcp._tool_manager.list_tools()}
    assert {"log_time", "update_entry", "delete_entry"} <= set(tools)
    assert tools["delete_entry"].annotations.destructive_hint is True
    assert tools["log_time"].annotations.read_only_hint is False
    assert tools["get_overview"].annotations.read_only_hint is True
    for name in ("log_time", "update_entry", "delete_entry"):
        assert "One entry per call" in tools[name].description


def test_read_tool_exposes_entry_ids_for_writes(env, monkeypatch):
    monkeypatch.setattr(S, "get_entries_for_range",
                        lambda s, e: [_entry(901, 11, _iso(TODAY, "12:00"), 60, "x")])
    monkeypatch.setattr(S, "get_projects", lambda: {"11": {"name": "Acme Corp"}})
    monkeypatch.setattr(S, "_today", lambda: TODAY)
    out = S.get_time_entries(TODAY.isoformat(), TODAY.isoformat())
    assert out["days"][0]["entries"][0]["entry_id"] == 901


def test_freshness_reports_write_flag(env):
    assert S.get_data_freshness()["mcp_write_enabled"] is True


# --- menu bar refresh -----------------------------------------------------

def test_confirmed_write_announces_the_change(env):
    notices = env["announced"]
    preview = S.log_time("2026-10-08", "09:00", 60, "Acme Corp")
    S.log_time("2026-10-08", "09:00", 60, "Acme Corp", confirm=preview["confirm"])
    assert len(notices) == 1 and "log_time" in notices[0]


def test_preview_announces_nothing(env):
    notices = env["announced"]
    S.log_time("2026-10-08", "09:00", 60, "Acme Corp")
    S.update_entry(901, duration_minutes=30)
    S.delete_entry(901)
    assert notices == []


def test_update_and_delete_announce_too(env):
    notices = env["announced"]
    preview = S.update_entry(901, duration_minutes=30)
    S.update_entry(901, duration_minutes=30, confirm=preview["confirm"])
    preview = S.delete_entry(901)
    S.delete_entry(901, confirm=preview["confirm"])
    assert [n.split()[1] for n in notices] == ["update_entry", "delete_entry"]


def test_no_op_update_announces_nothing(env):
    notices = env["announced"]
    S.update_entry(901, description="existing work")
    assert notices == []


def test_failed_write_announces_nothing(env, monkeypatch):
    notices = env["announced"]
    monkeypatch.setattr(toggl_data, "create_time_entry",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("nope")))
    preview = S.log_time("2026-10-08", "09:00", 60, "Acme Corp")
    out = S.log_time("2026-10-08", "09:00", 60, "Acme Corp", confirm=preview["confirm"])
    assert out["error_type"] == "RuntimeError"
    assert notices == []
