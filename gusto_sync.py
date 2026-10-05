"""Weekly push of one Toggl project's hours into Gusto contractor shifts.

Every Monday from 09:00 local, the previous Monday-Sunday week's finished
Toggl entries for the configured project become Gusto shifts on their real
dates, with the entry's description as the shift note (Gusto requires one).
The user reviews hours in Toggl; this sync is fully automatic.

Contract:
- **Only ever adds.** A Toggl entry pushed once is recorded in a local ledger
  and never pushed again. If it is later edited or deleted in Toggl, the
  mismatch is flagged for the user to fix in Gusto; the app never edits or
  deletes a Gusto shift (those are payroll records a manager may have seen).
- Entries added late to an already-synced week are pushed on the next run if
  they fall inside the lookback window; Gusto may refuse a locked day, which
  is reported, not retried forever.
- A shift already in Gusto with the same start and end is not added again, so
  a lost ledger cannot double-log.
- The Gusto session is a browser login (see gusto_client). When it expires the
  run stops, the user is notified once a day, and nothing retries until they
  log in again.

The pure helpers (`sync_window`, `sync_due`, `plan_pushes`, `find_drift`)
carry the decisions and are unit tested; `run_sync` wires them to Toggl and
Gusto through injectable providers.
"""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from preferences import APP_SUPPORT_DIR

STATE_FILE = APP_SUPPORT_DIR / "gusto_sync_state.json"
LOCK_FILE = APP_SUPPORT_DIR / "gusto_sync.lock"

SYNC_WEEKDAY = 0  # Monday
SYNC_HOUR = 9  # 09:00 local
RETRY_AFTER = timedelta(hours=1)  # first retry after a non-login failure; doubles each time
MAX_RETRY_AFTER = timedelta(hours=24)
LOOKBACK_DAYS = 35  # how far back late additions and edits are noticed
DEFAULT_NOTE = "Logged from Toggl"
ENTRY_TOLERANCE = timedelta(minutes=1)  # Gusto works in whole minutes


# --- state -----------------------------------------------------------------


def load_state(path: Path = STATE_FILE):
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("pushed", {})
            return data
    except Exception:
        pass
    return {"pushed": {}}


def save_state(state, path: Path = STATE_FILE):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(path)


# --- schedule --------------------------------------------------------------


def _sync_anchor(now):
    """The most recent Monday 09:00 at or before `now`."""
    monday = now.date() - timedelta(days=(now.weekday() - SYNC_WEEKDAY) % 7)
    anchor = datetime.combine(monday, datetime.min.time()).replace(hour=SYNC_HOUR)
    if now.tzinfo is not None:
        anchor = anchor.replace(tzinfo=now.tzinfo)
    if now < anchor:
        anchor -= timedelta(days=7)
    return anchor


def target_week(now):
    """(start, end) of the Monday-Sunday week the latest Monday run covers."""
    end = _sync_anchor(now).date() - timedelta(days=1)
    return end - timedelta(days=6), end


def sync_window(now, state, first_day=None):
    """Dates one run looks at: (start, end).

    `end` is the last day of the target week. `start` reaches back
    LOOKBACK_DAYS so late additions and edits are noticed, but never before
    `first_day` (the user's "push hours from" date) or, on a first run without
    one, before the target week itself, so enabling the sync does not suddenly
    backfill a month.
    """
    week_start, end = target_week(now)
    start = end - timedelta(days=LOOKBACK_DAYS - 1)
    recorded = state.get("first_day")
    floor = first_day or (date.fromisoformat(recorded) if recorded else week_start)
    return max(start, floor), end


def sync_due(now, state, enabled=True):
    """True when an automatic run should start now."""
    if not enabled or state.get("login_required"):
        return False
    _, end = target_week(now)
    synced = state.get("synced_through")
    if synced and date.fromisoformat(synced) >= end:
        return False
    last_attempt = state.get("last_attempt")
    if last_attempt and state.get("last_error"):
        try:
            if now - datetime.fromisoformat(last_attempt) < retry_delay(state):
                return False
        except (TypeError, ValueError):
            pass
    return True


def retry_delay(state):
    """Backoff after consecutive failures: 1h, 2h, 4h ... capped at 24h, so a
    persistent error (schema change, missing project) cannot pop the Gusto
    window up every hour."""
    failures = max(int(state.get("consecutive_failures") or 1), 1)
    return min(RETRY_AFTER * (2 ** (failures - 1)), MAX_RETRY_AFTER)


def manual_window(now, state, first_day=None):
    """Dates a "Push to Gusto" click covers: the lookback window up to
    yesterday, so a mid-week push also sends this week's finished days.
    Today is left out because it is not over yet."""
    start, _ = sync_window(now, state, first_day)
    end = now.date() - timedelta(days=1)
    return start, end  # start > end means nothing to push yet


def is_configured(prefs):
    """True when a Toggl project is set as the source of Gusto hours."""
    return bool(prefs.get("gusto_sync_enabled") and (prefs.get("gusto_project") or "").strip())


def is_stale(now, state):
    """Last week not in Gusto yet, or something needs the user.

    Drives the orange dot on Export/Invoice. Unlike `sync_due` this ignores
    retry backoff: a failed run is still stale.
    """
    _, end = target_week(now)
    synced = state.get("synced_through")
    behind = not synced or date.fromisoformat(synced) < end
    return bool(behind or state.get("login_required") or state.get("issues") or state.get("last_error"))


def status_view(prefs, state, now=None, running=False):
    """Everything the dashboard and settings show about the Gusto sync."""
    now = now or datetime.now()
    configured = is_configured(prefs)
    week_start, week_end = target_week(now)
    payday, deadline = next_payday(now.date())
    issues = [issue.get("message", "") for issue in state.get("issues") or []]

    if running:
        meta = "Pushing\u2026"
    elif state.get("login_required"):
        meta = "Needs your Gusto passkey"
    elif state.get("last_error"):
        meta = "Last push failed"
    elif issues:
        meta = f"{len(issues)} need{'s' if len(issues) == 1 else ''} attention"
    elif is_stale(now, state):
        meta = f"{_short(week_start.isoformat())}\u2013{_short(week_end.isoformat())} not pushed"
    else:
        synced = state.get("synced_through")
        meta = f"Up to date through {_short(synced)}" if synced else "Not pushed yet"

    last = state.get("last_result") or {}
    if last:
        last_text = (
            f"Pushed {last.get('pushed', 0)} shifts ({last.get('hours', 0):.2f}h) "
            f"for {_short(last['start'])}\u2013{_short(last['end'])}"
            if last.get("pushed") else
            f"Nothing new for {_short(last['start'])}\u2013{_short(last['end'])}"
        )
    else:
        last_text = ""
    return {
        "configured": configured,
        "stale": configured and not running and is_stale(now, state),
        "running": running,
        "meta": meta,
        "issues": issues,
        "error": state.get("last_error") or "",
        "login_required": bool(state.get("login_required")),
        "last_result": last_text,
        "last_success": state.get("last_success") or "",
        "next_payday": f"{payday.strftime('%a %b %-d')} (hours due {deadline.strftime('%a %b %-d')})",
        "project": prefs.get("gusto_project") or "",
        "company": prefs.get("gusto_company_slug") or "",
    }


# --- payday (for display) --------------------------------------------------


def _nth_weekday(year, month, weekday, n):
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day):
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_bank_holidays(year):
    """Federal holidays for `year` (observed dates), plus next New Year's Day
    when it is observed on Dec 31."""
    fixed = [(1, 1), (6, 19), (7, 4), (11, 11), (12, 25)]
    days = {_observed(date(year, m, d)) for m, d in fixed}
    days |= {
        _nth_weekday(year, 1, 0, 3),  # MLK
        _nth_weekday(year, 2, 0, 3),  # Presidents
        _last_weekday(year, 5, 0),  # Memorial
        _nth_weekday(year, 9, 0, 1),  # Labor
        _nth_weekday(year, 10, 0, 2),  # Columbus
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
    }
    days.add(_observed(date(year + 1, 1, 1)))
    return days


def payday_for(year, month):
    """Payday for the month: the 1st, or the last business day
    before it when the 1st is a weekend or bank holiday."""
    day = date(year, month, 1)
    holidays = us_bank_holidays(year) | us_bank_holidays(year - 1)
    while day.weekday() >= 5 or day in holidays:
        day -= timedelta(days=1)
    return day


def next_payday(today):
    """(payday, submission deadline) for the first payday on or after `today`.

    Hours are due two days before payday.
    """
    year, month = today.year, today.month
    while True:
        payday = payday_for(year, month)
        if payday >= today:
            return payday, payday - timedelta(days=2)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


# --- planning --------------------------------------------------------------


def _parse(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def entry_bounds(entry):
    """(start, stop) as local aware datetimes, or None for a running timer."""
    duration = entry.get("duration") or 0
    if duration <= 0 or not entry.get("start"):
        return None
    start = _parse(entry["start"]).astimezone()
    if entry.get("stop"):
        stop = _parse(entry["stop"]).astimezone()
    else:
        stop = start + timedelta(seconds=duration)
    return start, stop


def _same_shift(shift, start, stop, note=None):
    """Does a Gusto shift represent this Toggl entry?

    Compares clock-in/out times. If Gusto returned no timestamps, falls back
    to day + duration + note, so a shift whose times are hidden still counts
    as present (erring towards "already there" rather than double-logging).
    """
    try:
        clock_in = _parse(shift["clock_in"])
        clock_out = _parse(shift["clock_out"])
    except (KeyError, TypeError, ValueError):
        minutes = round((stop - start).total_seconds() / 60)
        same_day = shift.get("date") == start.date().isoformat()
        same_length = shift.get("minutes") is not None and abs(int(shift["minutes"]) - minutes) <= 1
        same_note = note is None or note in (shift.get("notes") or [])
        return bool(same_day and same_length and same_note)
    return abs(clock_in - start) <= ENTRY_TOLERANCE and abs(clock_out - stop) <= ENTRY_TOLERANCE


def plan_pushes(entries, project_id, state, start, end, existing_shifts=()):
    """Entries that should become Gusto shifts, oldest first.

    An entry whose start and end already match a shift in Gusto carries that
    shift's id as `existing_shift_id`: the caller records it in the ledger
    (so later Toggl edits are still flagged) instead of adding it again.
    """
    pushed = state.get("pushed", {})
    plan = []
    for entry in entries:
        if str(entry.get("project_id")) != str(project_id):
            continue
        if str(entry.get("id")) in pushed:
            continue
        bounds = entry_bounds(entry)
        if bounds is None:
            continue
        entry_start, entry_stop = bounds
        if not (start <= entry_start.date() <= end):
            continue
        if entry_stop - entry_start < ENTRY_TOLERANCE:
            continue
        note = (entry.get("description") or "").strip() or DEFAULT_NOTE
        match = next((s for s in existing_shifts if _same_shift(s, entry_start, entry_stop, note)), None)
        plan.append({
            "existing_shift_id": match.get("id") if match else None,
            "toggl_id": str(entry.get("id")),
            "day": entry_start.date().isoformat(),
            "start": entry_start,
            "stop": entry_stop,
            "hours": (entry_stop - entry_start).total_seconds() / 3600,
            "note": note,
        })
    plan.sort(key=lambda item: item["start"])
    return plan


def find_drift(entries, project_id, state, start, end):
    """Pushed entries (within the window) whose Toggl copy changed or vanished."""
    current = {
        str(e.get("id")): e for e in entries if str(e.get("project_id")) == str(project_id)
    }
    issues = []
    records = sorted(state.get("pushed", {}).items(), key=lambda kv: kv[1].get("start", ""))
    for toggl_id, record in records:
        day = record.get("day")
        if not day or not (start <= date.fromisoformat(day) <= end):
            continue
        label = date.fromisoformat(day).strftime("%a %b %-d")
        entry = current.get(toggl_id)
        if entry is None:
            issues.append({"kind": "deleted", "day": day,
                           "message": f"{label}: an entry pushed to Gusto was deleted in Toggl"})
            continue
        bounds = entry_bounds(entry)
        if bounds is None:
            continue
        try:
            # Compare instants, not strings: a Mac time zone change (travel)
            # must not flag every pushed entry.
            changed = (abs(bounds[0] - _parse(record["start"])) > ENTRY_TOLERANCE
                       or abs(bounds[1] - _parse(record["stop"])) > ENTRY_TOLERANCE)
        except (KeyError, TypeError, ValueError):
            changed = True
        if changed:
            issues.append({"kind": "changed", "day": day,
                           "message": f"{label}: an entry's times changed in Toggl after it was pushed"})
    return issues


# --- orchestration ---------------------------------------------------------


def _default_entries(start, end):
    """Fresh from Toggl (1 call), never the day cache: past days are cached
    forever once written, so a cached day could be missing hours logged after
    it was fetched, and the push would under-report payroll. Raises on a rate
    limit or network error rather than pushing from stale or empty data."""
    import toggl_data

    start_dt = datetime.combine(start, datetime.min.time()).astimezone()
    end_dt = datetime.combine(end, datetime.max.time()).astimezone()
    return toggl_data.fetch_entries_fresh(start_dt, end_dt)


def _default_projects():
    import toggl_data

    return toggl_data.get_projects() or {}


def _project_id(project_name, projects):
    for pid, info in (projects or {}).items():
        if (info or {}).get("name") == project_name:
            return str(pid)
    return None


def run_sync(prefs, state, now=None, dry_run=False, window=None,
             session_factory=None, entries_provider=None, projects_provider=None,
             persist=None):
    """Push due entries. Returns (new_state, summary).

    Never raises for Gusto or Toggl failures; they are recorded in the state
    and summary instead. `window` overrides the computed (start, end) dates
    for manual runs. `persist(state)` is called after every successful write
    so a crash mid-run cannot forget a shift Gusto already has.
    """
    import gusto_client

    now = now or datetime.now()
    state = json.loads(json.dumps(state or {"pushed": {}}))
    state.setdefault("pushed", {})
    entries_provider = entries_provider or _default_entries
    projects_provider = projects_provider or _default_projects
    session_factory = session_factory or gusto_client.open_session
    persist = persist or (lambda _state: None)

    project_name = prefs.get("gusto_project") or ""
    company_slug = prefs.get("gusto_company_slug") or ""
    first_day_pref = prefs.get("gusto_sync_start_date") or ""
    first_day = date.fromisoformat(first_day_pref) if first_day_pref else None

    start, end = window or sync_window(now, state, first_day)
    if start > end:  # e.g. "push hours from" is in the future
        return state, {"start": end.isoformat(), "end": end.isoformat(), "pushed": 0,
                       "hours": 0.0, "adopted": 0, "planned": [], "issues": [], "error": None,
                       "login_required": False, "dry_run": dry_run, "empty": True}
    summary = {"start": start.isoformat(), "end": end.isoformat(), "pushed": 0, "hours": 0.0,
               "adopted": 0,
               "planned": [], "issues": [], "error": None, "login_required": False,
               "dry_run": dry_run}
    if not dry_run:
        state["last_attempt"] = now.isoformat(timespec="seconds")
        if not state.get("first_day"):
            state["first_day"] = (first_day or start).isoformat()

    def fail(message, login=False):
        summary["error"] = message
        summary["login_required"] = login
        if not dry_run:
            state["last_error"] = message
            state["login_required"] = login
            state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        return state, summary

    if not project_name:
        return fail("Choose which Toggl project to push to Gusto.")
    if not company_slug:
        return fail("Log in to Gusto first.", login=True)

    try:
        project_id = _project_id(project_name, projects_provider())
        if project_id is None:
            return fail(f"Toggl project {project_name!r} not found.")
        entries = entries_provider(start, end) or []
    except Exception as exc:
        return fail(f"Could not read Toggl entries: {exc}")

    drift = find_drift(entries, project_id, state, start, end)
    failures = []
    try:
        with session_factory(company_slug) as session:
            tracker_id = session.tracker_id()
            timezone = session.browser_timezone()
            existing = session.list_shifts(tracker_id, start, end)
            plan = plan_pushes(entries, project_id, state, start, end, existing)
            summary["planned"] = [
                {k: (v.isoformat(timespec="minutes") if isinstance(v, datetime) else v)
                 for k, v in item.items()}
                for item in plan
            ]
            if not dry_run:
                for item in plan:
                    adopted = item["existing_shift_id"] is not None
                    try:
                        shift_id = item["existing_shift_id"] or session.add_shift(
                            tracker_id, item["start"], item["stop"], item["note"], timezone
                        )
                    except gusto_client.GustoLoginRequired:
                        raise
                    except gusto_client.GustoError as exc:
                        # The write may have landed even though the reply was
                        # unusable. Look before recording a refusal, or the
                        # next run would add the same hours again.
                        day = date.fromisoformat(item["day"])
                        landed = next(
                            (sh for sh in session.list_shifts(tracker_id, day, day)
                             if _same_shift(sh, item["start"], item["stop"], item["note"])),
                            None,
                        )
                        if landed is None:
                            failures.append({"kind": "rejected", "day": item["day"],
                                             "message": f"{day.strftime('%a %b %-d')}: Gusto refused a shift ({exc})"})
                            continue
                        shift_id = landed.get("id")
                    state["pushed"][item["toggl_id"]] = {
                        "shift_id": shift_id,
                        "day": item["day"],
                        "start": item["start"].isoformat(timespec="seconds"),
                        "stop": item["stop"].isoformat(timespec="seconds"),
                        "pushed_at": now.isoformat(timespec="seconds"),
                    }
                    if adopted:
                        state["pushed"][item["toggl_id"]]["adopted"] = True
                        summary["adopted"] += 1
                    else:
                        summary["pushed"] += 1
                        summary["hours"] += item["hours"]
                    persist(state)
    except gusto_client.GustoLoginRequired as exc:
        return fail(str(exc), login=True)
    except Exception as exc:
        return fail(f"Gusto sync failed: {exc}")

    summary["issues"] = drift + failures
    if not dry_run:
        state["issues"] = summary["issues"]
        state["synced_through"] = max(end.isoformat(), state.get("synced_through") or "")
        state["last_success"] = now.isoformat(timespec="seconds")
        state["last_result"] = {k: summary[k] for k in ("start", "end", "pushed", "hours")}
        state["last_error"] = None
        state["login_required"] = False
        state["consecutive_failures"] = 0
    return state, summary


class SyncBusy(RuntimeError):
    """Another process is already pushing to Gusto."""


@contextmanager
def run_lock(path: Path = LOCK_FILE):
    """Cross-process lock for a real push (the app and the CLI share a ledger).

    Without it, an overlapping `gusto_sync.py run` and the app's Monday run
    would both push the same entries, and the last to save would drop the
    other's ledger records.
    """
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncBusy("Another Gusto push is already running.")
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_locked(prefs, now=None, window=None, session_factory=None, state_path: Path = STATE_FILE,
               lock_path: Path = LOCK_FILE, **kwargs):
    """A real push under the lock, with the ledger loaded and saved inside it.

    Returns (state, summary). If another push holds the lock, nothing runs
    and the summary carries the error; the saved state is untouched.
    """
    try:
        with run_lock(lock_path):
            state = load_state(state_path)
            new_state, summary = run_sync(
                prefs, state, now=now, window=window, session_factory=session_factory,
                persist=lambda st: save_state(st, state_path), **kwargs,
            )
            save_state(new_state, state_path)
            return new_state, summary
    except SyncBusy as exc:
        start, end = window or (date.today(), date.today())
        return load_state(state_path), {
            "start": start.isoformat(), "end": end.isoformat(), "pushed": 0, "hours": 0.0,
            "adopted": 0, "planned": [], "issues": [], "error": str(exc),
            "login_required": False, "dry_run": False, "busy": True,
        }


def describe_result(summary):
    """One-line status for notifications and the settings pane."""
    if summary.get("error"):
        return summary["error"]
    if summary.get("empty"):
        return "Nothing to push yet"
    span = f"{_short(summary['start'])}–{_short(summary['end'])}"
    if summary.get("dry_run"):
        new = [item for item in summary.get("planned", []) if not item.get("existing_shift_id")]
        hours = sum(item["hours"] for item in new)
        return f"Would push {len(new)} shifts ({hours:.2f}h) for {span}"
    if summary.get("pushed"):
        text = f"Pushed {summary['pushed']} shifts ({summary['hours']:.2f}h) for {span}"
    else:
        text = f"Nothing new to push for {span}"
    if summary.get("issues"):
        text += f" · {len(summary['issues'])} need attention"
    return text


def _short(iso_day):
    return date.fromisoformat(iso_day).strftime("%b %-d")


def main(argv=None):
    """CLI: `python gusto_sync.py login | dry-run | run [--start D --end D]`."""
    import argparse

    from preferences import load_preferences, save_preferences

    parser = argparse.ArgumentParser(description="Push Toggl hours to Gusto shifts.")
    parser.add_argument("command", choices=["login", "dry-run", "run"])
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args(argv)

    if args.command == "login":
        import gusto_client

        result = gusto_client.login()
        prefs = load_preferences()
        prefs["gusto_company_slug"] = result["company_slug"]
        save_preferences(prefs)
        state = load_state()
        state["login_required"] = False
        save_state(state)
        print(f"Logged in to Gusto ({result['company_slug']}).")
        return 0

    window = None
    if args.start or args.end:
        if not (args.start and args.end):
            parser.error("--start and --end go together")
        window = (date.fromisoformat(args.start), date.fromisoformat(args.end))

    if args.command == "dry-run":
        _, summary = run_sync(load_preferences(), load_state(), dry_run=True, window=window)
    else:
        _, summary = run_locked(load_preferences(), window=window)
    for item in summary["planned"]:
        tag = "  (already in Gusto)" if item.get("existing_shift_id") else ""
        print(f"  {item['day']}  {item['start'][11:16]}-{item['stop'][11:16]}  "
              f"{item['hours']:.2f}h  {item['note']}{tag}")
    for issue in summary["issues"]:
        print(f"  ! {issue['message']}")
    print(describe_result(summary))
    return 1 if summary["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
