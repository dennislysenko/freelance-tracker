"""Upwork manual-time planning: 10-minute rounding, ledger, drift, locks, reminders."""

import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest

import upwork_bridge
import upwork_push as up

PID = "77"
CONTRACT = "12345"
PREFS = {"upwork_contracts": {"Acme": CONTRACT, "Bad": "not-digits"}}


def _local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi).astimezone()


def _entry(eid, start, minutes, desc="design review", billable=True, project=PID):
    stop = start + timedelta(minutes=minutes)
    return {
        "id": eid, "project_id": project, "description": desc, "billable": billable,
        "start": start.astimezone(timezone.utc).isoformat(),
        "stop": stop.astimezone(timezone.utc).isoformat(),
        "duration": minutes * 60,
    }


# Wednesday of a week well before its Monday-noon-UTC lock.
WED = (2026, 3, 4)
NOW = _local(2026, 3, 6, 10)  # Friday morning, same Upwork week


def _plan(entries, state=None, now=NOW):
    return up.plan(entries, PID, "Acme", CONTRACT, state or {"added": {}}, now)


def test_linked_contracts_and_step_minutes():
    assert up.linked_contracts(PREFS) == {"Acme": CONTRACT}
    assert up.step_minutes(PREFS, "Acme") == 10
    assert up.step_minutes(PREFS, "Other") == 15


def test_lone_quarter_hours_alternate_and_week_never_exceeds_toggl():
    entries = [_entry(i, _local(2026, 3, 2 + i, 9), 15) for i in range(4)]  # Mon..Thu
    result = _plan(entries)
    assert [s["minutes"] for s in result["segments"]] == [10, 20, 10, 20]
    week = result["weeks"][up.week_start(NOW).isoformat()]
    assert week["pending_minutes"] == 60 and week["toggl_minutes"] == 60

    three = _plan(entries[:3])
    assert sum(s["minutes"] for s in three["segments"]) == 40  # floor10(45)


def test_same_day_neighbours_merge_and_gaps_are_not_billed():
    day = _local(*WED, 10)
    entries = [
        _entry(1, day, 20, "api work"),
        _entry(2, day + timedelta(minutes=22), 20, "code review"),  # 2-min gap -> merged
        _entry(3, day + timedelta(hours=2), 30, "call"),            # separate block
    ]
    segs = _plan(entries)["segments"]
    assert len(segs) == 2
    assert segs[0]["minutes"] == 40 and segs[0]["memo"] == "api work; code review"
    assert (segs[0]["start"], segs[0]["end"]) == ("10:00 AM", "10:40 AM")
    assert sorted(segs[0]["entry_ids"]) == ["1", "2"]
    assert (segs[1]["start"], segs[1]["end"]) == ("12:00 PM", "12:30 PM")


def test_start_snaps_to_ten_minutes_without_overlapping():
    day = _local(*WED, 9, 4)
    entries = [_entry(1, day, 30), _entry(2, day + timedelta(minutes=37), 20)]
    segs = _plan(entries)["segments"]
    assert (segs[0]["start"], segs[0]["end"]) == ("9:00 AM", "9:30 AM")
    assert (segs[1]["start"], segs[1]["end"]) == ("9:40 AM", "10:00 AM")

    # A start that would snap back into the previous segment starts after it.
    tight = [_entry(1, _local(*WED, 9, 0), 26), _entry(2, _local(*WED, 9, 33), 20)]
    segs = _plan(tight)["segments"]
    assert segs[0]["end"] == "9:20 AM" and segs[1]["start"] == "9:30 AM"


def test_non_billable_other_projects_and_running_timers_are_left_out():
    day = _local(*WED, 9)
    running = _entry(4, day + timedelta(hours=5), 30)
    running.update(duration=-1, stop=None)
    entries = [
        _entry(1, day, 30),
        _entry(2, day + timedelta(hours=1), 30, billable=False),
        _entry(3, day + timedelta(hours=2), 30, project="99"),
        running,
    ]
    segs = _plan(entries)["segments"]
    assert [s["entry_ids"] for s in segs] == [["1"]]


def test_memo_is_trimmed_to_upworks_limit_and_has_a_default():
    assert len(up.memo_for(["x" * 300])) == up.MEMO_LIMIT
    assert up.memo_for([]) == up.DEFAULT_MEMO


def test_added_segments_are_not_offered_again():
    entries = [_entry(1, _local(*WED, 9), 30), _entry(2, _local(*WED, 14), 30)]
    state = {"added": {}}
    first = _plan(entries, state)
    up.record_added(state, CONTRACT, "Acme", first["segments"][0], now=NOW)
    again = _plan(entries, state)
    assert [s["added"] for s in again["segments"]] == [True, False]
    assert [s["entry_ids"] for s in up.pending(again)] == [["2"]]
    assert again["issues"] == []
    assert up.queue_payload(again, CONTRACT, "Acme")["pending_minutes"] == 30


def test_entry_merged_into_an_added_block_is_flagged_and_carried_forward():
    base = _local(*WED, 9)
    state = {"added": {}}
    up.record_added(state, CONTRACT, "Acme", _plan([_entry(1, base, 30)])["segments"][0], now=NOW)
    # A second entry is logged right after the added one (merges into its block),
    # then a later one: the extra 20 min must show up later, not be lost or doubled.
    entries = [_entry(1, base, 30), _entry(2, base + timedelta(minutes=31), 20),
               _entry(3, _local(*WED, 15), 10)]
    result = _plan(entries, state)
    assert result["segments"][0]["added"] and result["segments"][0]["minutes"] == 30
    assert [s["minutes"] for s in up.pending(result)] == [30]  # 10 + the 20 carried
    assert any("changed in Toggl" in msg for msg in result["issues"])


def test_deleted_entry_after_adding_is_flagged():
    state = {"added": {}}
    up.record_added(state, CONTRACT, "Acme",
                    _plan([_entry(1, _local(*WED, 9), 30)])["segments"][0], now=NOW)
    result = _plan([], state)
    assert any("no longer in Toggl" in msg for msg in result["issues"])
    assert any("more than Toggl" in msg for msg in result["issues"])


def test_open_weeks_follow_the_monday_noon_utc_lock():
    week = up.week_start(NOW)
    prev = week - timedelta(days=7)
    before_lock = datetime.combine(week, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=11)
    after_lock = before_lock + timedelta(hours=2)
    assert up.open_weeks(before_lock) == [prev, week]
    assert up.open_weeks(after_lock) == [week]
    assert up.lock_time(prev) == datetime.combine(week, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)


def test_reminders_fire_once_per_slot_and_stop_at_the_lock():
    entries = [_entry(1, _local(*WED, 9), 30)]
    week = up.week_start(NOW)
    friday_4pm = datetime.combine(week + timedelta(days=4), datetime.min.time()).astimezone() + timedelta(hours=16)
    state = {"added": {}}

    early = friday_4pm - timedelta(hours=1)
    assert up.reminder_due(early, state, {"Acme": _plan(entries, state, early)}) is None

    results = {"Acme": _plan(entries, state, friday_4pm)}
    due = up.reminder_due(friday_4pm, state, results)
    assert due == (week.isoformat(), "friday", 30)
    up.mark_reminded(state, week.isoformat(), friday_4pm)
    assert up.reminder_due(friday_4pm + timedelta(hours=1), state, results) is None

    sunday_6pm = friday_4pm + timedelta(days=2, hours=2)
    assert up.reminder_due(sunday_6pm, state, {"Acme": _plan(entries, state, sunday_6pm)})[1] == "sunday"
    assert "Acme 30m" in up.reminder_text(results, week.isoformat())

    past_lock = up.lock_time(week) + timedelta(minutes=1)
    assert up.reminder_due(past_lock, {"added": {}}, results) is None

    # Nothing waiting, nothing to remind about.
    done = {"added": {}}
    up.record_added(done, CONTRACT, "Acme", _plan(entries)["segments"][0], now=NOW)
    assert up.reminder_due(friday_4pm, done, {"Acme": _plan(entries, done, friday_4pm)}) is None


def test_project_line_and_status_view():
    entries = [_entry(1, _local(*WED, 9), 30), _entry(2, _local(*WED, 14), 100)]
    state = {"added": {}, "bridge_token": "abc"}
    result = _plan(entries, state)
    assert up.project_line(result).startswith("Upwork: 2h 10m to add before ")
    status = up.status_view(PREFS, state, {"Acme": result}, NOW)
    assert status["configured"] and status["stale"] and not status["connected"]
    assert status["meta"] == "Set up"
    assert status["projects"]["Acme"]["warn"]

    for seg in result["segments"]:
        up.record_added(state, CONTRACT, "Acme", seg, now=NOW)
    state["extension_seen"] = NOW.isoformat()
    done = _plan(entries, state)
    assert up.project_line(done) == "Upwork: all 2h 10m added this week"
    status = up.status_view(PREFS, state, {"Acme": done}, NOW)
    assert status["meta"] == "Up to date" and not status["stale"]


def test_state_round_trip_and_token(tmp_path):
    path = tmp_path / "state.json"
    state = up.load_state(path)
    token = up.ensure_token(state)
    assert token and up.ensure_token(state) == token
    up.save_state(state, path)
    assert up.load_state(path)["bridge_token"] == token


# --- bridge ----------------------------------------------------------------


class _Handlers(upwork_bridge.BridgeHandlers):
    def __init__(self):
        self.pings = 0
        self.added_keys = []

    def token(self):
        return "secret"

    def ping(self):
        self.pings += 1

    def queue(self, contract):
        return {"ok": True, "contract": contract, "entries": [{"key": "k1"}]}

    def added(self, contract, key):
        if key != "k1":
            return False
        self.added_keys.append((contract, key))
        return True


@pytest.fixture
def bridge():
    handlers = _Handlers()
    server = upwork_bridge.start(handlers, port=0)
    yield handlers, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _call(url, token="secret", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if token:
        req.add_header("X-FT-Token", token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read()), dict(err.headers)


def test_bridge_requires_the_pairing_code(bridge):
    handlers, base = bridge
    assert _call(base + "/ping", token=None)[0] == 401
    assert _call(base + "/ping", token="wrong")[0] == 401
    status, body, headers = _call(base + "/ping")
    assert status == 200 and body["ok"] and handlers.pings == 1
    assert not any(h.lower() == "access-control-allow-origin" for h in headers)


def test_bridge_queue_and_added(bridge):
    handlers, base = bridge
    assert _call(base + "/upwork/queue?contract=abc")[0] == 400
    status, body, _ = _call(base + f"/upwork/queue?contract={CONTRACT}")
    assert status == 200 and body["entries"] == [{"key": "k1"}]
    assert _call(base + "/upwork/added", body={"contract": CONTRACT, "key": "nope"})[0] == 404
    assert _call(base + "/upwork/added", body={"contract": CONTRACT, "key": "k1"})[0] == 200
    assert handlers.added_keys == [(CONTRACT, "k1")]
    assert _call(base + "/upwork/added", token="wrong", body={"contract": CONTRACT, "key": "k1"})[0] == 401
    assert _call(base + "/nope")[0] == 404


def test_firefox_based_browsers_get_the_about_debugging_setup():
    assert up.is_gecko("Zen") and up.is_gecko("Firefox Developer Edition")
    assert not up.is_gecko("Google Chrome") and not up.is_gecko("Brave Browser")
    assert up.browser_name({}) == "Google Chrome"
    status = up.status_view({**PREFS, "upwork_browser": "Zen"}, {"added": {}}, {}, NOW)
    assert status["browser"] == "Zen" and status["gecko"]


def _raw(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers)


def test_bridge_answers_cors_only_for_extension_origins(bridge):
    _handlers, base = bridge
    ext = "moz-extension://00000000-1111-2222-3333-444444444444"
    status, headers = _raw(base + "/ping", "OPTIONS", {"Origin": ext})
    assert status == 204 and headers["Access-Control-Allow-Origin"] == ext
    assert "X-FT-Token" in headers["Access-Control-Allow-Headers"]

    for page in ("https://evil.example", "http://localhost:3000", "null", ""):
        status, headers = _raw(base + "/ping", "OPTIONS", {"Origin": page})
        assert status == 403 and "Access-Control-Allow-Origin" not in headers

    status, headers = _raw(base + "/ping", headers={"Origin": ext, "X-FT-Token": "secret"})
    assert status == 200 and headers["Access-Control-Allow-Origin"] == ext
    status, headers = _raw(base + "/ping", headers={"Origin": "https://evil.example", "X-FT-Token": "secret"})
    assert "Access-Control-Allow-Origin" not in headers
    # A wrong code is still refused, but the extension can read why.
    status, headers = _raw(base + "/ping", headers={"Origin": ext, "X-FT-Token": "nope"})
    assert status == 401 and headers["Access-Control-Allow-Origin"] == ext
