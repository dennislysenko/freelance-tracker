"""Turn an Upwork-linked Toggl project's hours into Upwork manual-time entries.

Upwork has no API for adding time, and its policy forbids tools that drive
the site for you. So this app never touches Upwork itself: it works out the
exact entries to add (date, start, end, memo, in Upwork's 10-minute steps),
serves them to the companion browser extension over a localhost bridge, and
the extension fills the "Add manual time" form. The user checks it and clicks
Save; the extension then reports the entry as added and it goes in a local
ledger.

Contract (same as the Gusto push):
- **Add-only.** Nothing here edits or deletes Upwork time.
- **Ledger + dedupe.** An added entry is recorded with the Toggl entries it
  came from and is never offered again, even if a block is re-merged.
- **Non-billable never counts.** Entries marked non-billable are left out.
- **Drift is flagged, not fixed.** Toggl changes after an entry was added
  are reported for the user to fix in Upwork by hand.
- **Never over Toggl.** Within an Upwork week (Monday to Sunday, UTC) the
  minutes offered are floor-to-10 of the running Toggl total, so the week
  never exceeds Toggl and lone 15-minute entries come out as 10, 20, 10, ...
- **Before the lock.** A week closes Monday 12:00 UTC; reminders fire on
  Friday afternoon and Sunday evening while time is still waiting.

Everything below is pure apart from the state file, and unit tested.
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from preferences import APP_SUPPORT_DIR

STATE_FILE = APP_SUPPORT_DIR / "upwork_push_state.json"

STEP_MINUTES = 10  # Upwork manual time works in 10-minute segments
DEFAULT_STEP_MINUTES = 15  # what agents use for every other project
MERGE_GAP = timedelta(minutes=5)  # same-day entries this close become one block
MEMO_LIMIT = 140  # Upwork's memo field limit
DEFAULT_MEMO = "Logged from Toggl"
LOCK_HOUR_UTC = 12  # a week closes the following Monday 12:00 UTC
REMINDER_SLOTS = (  # (label, days after the UTC week start, local hour)
    ("friday", 4, 16),
    ("sunday", 6, 18),
)

_state_lock = threading.RLock()


# --- state -----------------------------------------------------------------


def load_state(path: Path = STATE_FILE):
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("added", {})
            return data
    except Exception:
        pass
    return {"added": {}}


def save_state(state, path: Path = STATE_FILE):
    with _state_lock:
        tmp = Path(str(path) + ".tmp")
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        tmp.replace(path)


def ensure_token(state):
    """The pairing code the extension sends with every bridge request."""
    token = state.get("bridge_token")
    if not token:
        token = secrets.token_hex(8)
        state["bridge_token"] = token
    return token


# --- configuration ---------------------------------------------------------


def linked_contracts(prefs):
    """{project name: contract id} for projects with an Upwork contract."""
    out = {}
    for name, contract in (prefs.get("upwork_contracts") or {}).items():
        contract = str(contract or "").strip()
        if name and contract.isdigit():
            out[name] = contract
    return out


def step_minutes(prefs, project_name):
    """Billing step for a project: 10 for Upwork-linked ones, else 15."""
    return STEP_MINUTES if project_name in linked_contracts(prefs) else DEFAULT_STEP_MINUTES


def is_configured(prefs):
    return bool(linked_contracts(prefs))


# --- weeks and locks -------------------------------------------------------


def week_start(moment):
    """Monday (UTC) of the Upwork week containing an aware datetime."""
    utc = moment.astimezone(timezone.utc)
    return utc.date() - timedelta(days=utc.weekday())


def lock_time(week):
    """When the week starting on `week` stops taking manual time (UTC)."""
    return datetime.combine(week + timedelta(days=7), time(LOCK_HOUR_UTC), tzinfo=timezone.utc)


def open_weeks(now):
    """Weeks that still take manual time, oldest first."""
    current = week_start(now)
    previous = current - timedelta(days=7)
    return [previous, current] if now < lock_time(previous) else [current]


def lock_label(week):
    local = lock_time(week).astimezone()
    return local.strftime("%a %b %-d, %-I:%M %p")


def short_lock_label(week):
    return lock_time(week).astimezone().strftime("%a %-I:%M %p")


# --- entries -> blocks -----------------------------------------------------


def _parse(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def _is_billable(entry):
    return (entry or {}).get("billable", True) is not False


def build_blocks(entries, project_id):
    """Finished, billable entries of one project, same-day neighbours merged.

    A block keeps the real worked seconds (gaps between merged entries are
    not billed), the local start of its first entry, and every entry id.
    """
    rows = []
    for e in entries or []:
        if str(e.get("project_id")) != str(project_id) or not _is_billable(e):
            continue
        seconds = e.get("duration") or 0
        if seconds <= 0 or not e.get("start"):
            continue
        start = _parse(e["start"]).astimezone()
        stop = _parse(e["stop"]).astimezone() if e.get("stop") else start + timedelta(seconds=seconds)
        rows.append((start, stop, seconds, str(e.get("id")), (e.get("description") or "").strip()))
    rows.sort(key=lambda r: r[0])

    blocks = []
    for start, stop, seconds, eid, desc in rows:
        last = blocks[-1] if blocks else None
        if (last and last["start"].date() == start.date()
                and week_start(last["start"]) == week_start(start)
                and start - last["stop"] <= MERGE_GAP):
            last["stop"] = max(last["stop"], stop)
            last["seconds"] += seconds
            last["entry_ids"].append(eid)
            if desc and desc not in last["descriptions"]:
                last["descriptions"].append(desc)
            continue
        blocks.append({
            "start": start, "stop": stop, "seconds": seconds,
            "entry_ids": [eid], "descriptions": [desc] if desc else [],
        })
    return blocks


def memo_for(descriptions):
    text = "; ".join(d for d in descriptions if d) or DEFAULT_MEMO
    if len(text) > MEMO_LIMIT:
        text = text[:MEMO_LIMIT - 1].rstrip() + "…"
    return text


def _snap(moment):
    """Nearest 10-minute mark."""
    base = moment.replace(second=0, microsecond=0)
    minute = base.minute + moment.second / 60
    snapped = int(round(minute / STEP_MINUTES)) * STEP_MINUTES
    return base.replace(minute=0) + timedelta(minutes=snapped)


def _hm(moment):
    return moment.strftime("%H:%M")


def _label(moment):
    return moment.strftime("%-I:%M %p")


def _at(day_iso, hm):
    d = date.fromisoformat(day_iso)
    h, m = (int(x) for x in hm.split(":"))
    return datetime.combine(d, time(h, m)).astimezone()


def block_key(contract, block):
    return f"{contract}:" + "+".join(sorted(block["entry_ids"]))


# --- planning --------------------------------------------------------------


def plan(entries, project_id, project_name, contract, state, now):
    """Every Upwork segment for the open weeks, added or not, plus issues.

    Returns {"segments": [...], "issues": [...], "weeks": {week_iso: {...}}}.
    A segment: key, week, date, start/end ("10:00 AM"), start_hm/end_hm,
    minutes, memo, entry_ids, toggl_minutes, added (bool).
    """
    weeks = open_weeks(now)
    records = {k: r for k, r in (state.get("added") or {}).items()
               if r.get("contract") == contract}
    used = set()
    segments, issues, week_info = [], [], {}

    by_week = {}
    for block in build_blocks(entries, project_id):
        by_week.setdefault(week_start(block["start"]), []).append(block)

    for week in weeks:
        week_iso = week.isoformat()
        actual_seconds = 0
        allocated = 0
        added_minutes = 0
        prev_end = None
        for block in by_week.get(week, []):
            actual_seconds += block["seconds"]
            ids = set(block["entry_ids"])
            matches = [k for k, r in records.items()
                       if k not in used and ids & set(r.get("entry_ids") or [])]
            toggl_minutes = round(block["seconds"] / 60)
            if matches:
                used.update(matches)
                recs = [records[k] for k in matches]
                minutes = sum(int(r.get("minutes") or 0) for r in recs)
                allocated += minutes
                added_minutes += minutes
                was = sum(int(r.get("toggl_minutes") or 0) for r in recs)
                if abs(was - toggl_minutes) >= 1:
                    issues.append(
                        f"{_day_label(block['start'])} {_label(block['start'])}: changed in Toggl "
                        f"after it was added to Upwork ({was} → {toggl_minutes} min)"
                    )
                first = recs[0]
                seg_start = _at(first["date"], first["start_hm"])
                seg_end = _at(recs[-1]["date"], recs[-1]["end_hm"])
                prev_end = seg_end if not prev_end or seg_end > prev_end else prev_end
                segments.append(_segment(matches[0], week_iso, seg_start, seg_end, minutes,
                                         first.get("memo") or "", block, toggl_minutes, True))
                continue

            target = (int(actual_seconds // 60) // STEP_MINUTES) * STEP_MINUTES
            minutes = max(0, target - allocated)
            if minutes == 0:
                continue
            allocated += minutes
            seg_start = _snap(block["start"])
            if prev_end and prev_end.date() == seg_start.date() and seg_start < prev_end:
                seg_start = prev_end
            seg_end = seg_start + timedelta(minutes=minutes)
            # A segment must end on its own day: the form's last end time is 11:50 PM.
            latest_end = datetime.combine(seg_start.date(), time(23, 60 - STEP_MINUTES)).astimezone()
            if seg_end > latest_end:
                seg_end = latest_end
                seg_start = seg_end - timedelta(minutes=minutes)
            prev_end = seg_end
            segments.append(_segment(block_key(contract, block), week_iso, seg_start, seg_end, minutes,
                                     memo_for(block["descriptions"]), block, toggl_minutes, False))

        toggl_floor = (int(actual_seconds // 60) // STEP_MINUTES) * STEP_MINUTES
        if added_minutes > toggl_floor:
            issues.append(
                f"Week of {_short(week)}: Upwork has {added_minutes - toggl_floor} min more than Toggl"
            )
        week_info[week_iso] = {
            "toggl_minutes": round(actual_seconds / 60),
            "added_minutes": added_minutes,
            "pending_minutes": sum(s["minutes"] for s in segments
                                   if s["week"] == week_iso and not s["added"]),
            "locks_label": lock_label(week),
            "short_lock_label": short_lock_label(week),
        }

    open_isos = {w.isoformat() for w in weeks}
    for key, record in records.items():
        if key in used or record.get("week") not in open_isos:
            continue
        # Still in Upwork, so it still counts toward that week's total there.
        info = week_info[record["week"]]
        info["added_minutes"] += int(record.get("minutes") or 0)
        floor = (info["toggl_minutes"] // STEP_MINUTES) * STEP_MINUTES
        over = info["added_minutes"] - floor
        over_msg = f"Week of {_short(date.fromisoformat(record['week']))}: Upwork has"
        issues[:] = [m for m in issues if not m.startswith(over_msg)]
        if over > 0:
            issues.append(f"{over_msg} {over} min more than Toggl")
        issues.append(
            f"{_day_label(_at(record['date'], record['start_hm']))} {record.get('start', record['start_hm'])}: "
            "added to Upwork but no longer in Toggl as billable time — remove it in Upwork"
        )
    return {"segments": segments, "issues": issues, "weeks": week_info}


def _segment(key, week_iso, start, end, minutes, memo, block, toggl_minutes, added):
    return {
        "key": key,
        "week": week_iso,
        "date": start.date().isoformat(),
        "start": _label(start),
        "end": _label(end),
        "start_hm": _hm(start),
        "end_hm": _hm(end),
        "minutes": minutes,
        "memo": memo,
        "entry_ids": list(block["entry_ids"]),
        "toggl_minutes": toggl_minutes,
        "added": added,
    }


def pending(result):
    return [s for s in result["segments"] if not s["added"]]


def record_added(state, contract, project_name, segment, now=None, how="extension"):
    """Put a segment in the ledger so it is never offered again."""
    now = now or datetime.now().astimezone()
    with _state_lock:
        state.setdefault("added", {})[segment["key"]] = {
            "contract": contract,
            "project": project_name,
            "week": segment["week"],
            "date": segment["date"],
            "start": segment["start"],
            "start_hm": segment["start_hm"],
            "end_hm": segment["end_hm"],
            "minutes": segment["minutes"],
            "memo": segment["memo"],
            "entry_ids": list(segment["entry_ids"]),
            "toggl_minutes": segment["toggl_minutes"],
            "added_at": now.isoformat(timespec="seconds"),
            "how": how,
        }


def queue_payload(result, contract, project_name):
    """What the extension gets: the not-yet-added segments."""
    entries = []
    for s in pending(result):
        entries.append({
            "key": s["key"], "date": s["date"], "start": s["start"], "end": s["end"],
            "minutes": s["minutes"], "memo": s["memo"],
            "locks_label": result["weeks"].get(s["week"], {}).get("locks_label", ""),
        })
    weeks = [{"week_start": w, "locks_label": info["locks_label"]}
             for w, info in sorted(result["weeks"].items()) if info["pending_minutes"]]
    return {
        "ok": True,
        "contract": contract,
        "project": project_name,
        "pending_minutes": sum(e["minutes"] for e in entries),
        "weeks": weeks,
        "entries": entries,
        "issues": list(result["issues"]),
    }


# --- status and reminders --------------------------------------------------


def _hours(minutes):
    """130 -> "2h 10m", 60 -> "1h", 20 -> "20m"."""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


def _short(day):
    return day.strftime("%b %-d")


def _day_label(moment):
    return moment.strftime("%a %b %-d")


def project_line(result):
    """Sub-line under the project's bar, e.g.
    "Upwork: 1.5h to add before Mon 7:00 AM · 0.5h of 2h added"."""
    weeks = result["weeks"]
    waiting = [(w, i) for w, i in sorted(weeks.items()) if i["pending_minutes"]]
    current = weeks[max(weeks)] if weeks else {"added_minutes": 0, "toggl_minutes": 0}
    added = _hours(current["added_minutes"])
    if waiting:
        total = sum(i["pending_minutes"] for _, i in waiting)
        text = f"Upwork: {_hours(total)} to add before {waiting[0][1]['short_lock_label']}"
        return text + (f" · {added} added this week" if current["added_minutes"] else "")
    if current["added_minutes"]:
        return f"Upwork: all {added} added this week"
    return "Upwork: nothing to add this week"


def status_view(prefs, state, results, now=None):
    """Dashboard data. `results` = {project name: plan() output}."""
    now = now or datetime.now().astimezone()
    contracts = linked_contracts(prefs)
    projects = {}
    total_pending = 0
    issues = []
    first_lock = None
    for name in contracts:
        result = results.get(name)
        if not result:
            continue
        waiting = sum(i["pending_minutes"] for i in result["weeks"].values())
        total_pending += waiting
        issues.extend(f"{name}: {msg}" for msg in result["issues"])
        for _w, info in sorted(result["weeks"].items()):
            if info["pending_minutes"]:
                first_lock = first_lock or info["short_lock_label"]
                break
        projects[name] = {"line": project_line(result), "pending_minutes": waiting,
                          "warn": bool(waiting or result["issues"])}
    connected = bool(state.get("extension_seen"))
    if not connected:
        meta = "Set up"
    elif total_pending:
        meta = f"{_hours(total_pending)} to add · locks {first_lock}"
    elif issues:
        meta = f"{len(issues)} need{'s' if len(issues) == 1 else ''} attention"
    else:
        meta = "Up to date"
    return {
        "configured": bool(contracts),
        "connected": connected,
        "extension_seen": state.get("extension_seen") or "",
        "token": state.get("bridge_token") or "",
        "browser": browser_name(prefs),
        "gecko": is_gecko(browser_name(prefs)),
        "stale": bool(total_pending or issues),
        "pending_minutes": total_pending,
        "meta": meta,
        "issues": issues,
        "projects": projects,
        "contracts": contracts,
    }


GECKO_BROWSERS = ("zen", "firefox", "librewolf", "floorp", "waterfox")
GECKO_DEBUG_URL = "about:debugging#/runtime/this-firefox"


def browser_name(prefs):
    return str((prefs or {}).get("upwork_browser") or "Google Chrome").strip() or "Google Chrome"


def is_gecko(browser):
    """Firefox-based browsers (Zen, Firefox, ...) load the extension from
    about:debugging instead of Chrome's Developer mode."""
    name = str(browser or "").lower()
    return any(g in name for g in GECKO_BROWSERS)


def reminder_due(now, state, results):
    """(week_iso, slot label, pending minutes) for a reminder to send now, or None.

    Fires once per slot per week, only while the week is open and has time
    waiting. If the app was asleep through a slot it fires on wake, as long
    as the lock has not passed.
    """
    sent = state.get("reminders_sent") or {}
    waiting = {}
    for result in results.values():
        for week_iso, info in result["weeks"].items():
            if info["pending_minutes"]:
                waiting[week_iso] = waiting.get(week_iso, 0) + info["pending_minutes"]
    for week_iso in sorted(waiting):
        week = date.fromisoformat(week_iso)
        if now >= lock_time(week):
            continue
        due = [label for label, days, hour in REMINDER_SLOTS
               if now >= datetime.combine(week + timedelta(days=days), time(hour)).astimezone()]
        fresh = [label for label in due if label not in sent.get(week_iso, [])]
        if fresh:
            return week_iso, fresh[-1], waiting[week_iso]
    return None


def mark_reminded(state, week_iso, now=None):
    """Mark every slot already due for this week as sent (one ping, not two)."""
    now = now or datetime.now().astimezone()
    week = date.fromisoformat(week_iso)
    due = [label for label, days, hour in REMINDER_SLOTS
           if now >= datetime.combine(week + timedelta(days=days), time(hour)).astimezone()]
    with _state_lock:
        sent = state.setdefault("reminders_sent", {})
        sent[week_iso] = sorted(set(sent.get(week_iso, [])) | set(due))
        for old in [w for w in sent if w < (week - timedelta(days=28)).isoformat()]:
            sent.pop(old, None)


def reminder_text(result_by_project, week_iso):
    week = date.fromisoformat(week_iso)
    parts = []
    for name, result in result_by_project.items():
        info = result["weeks"].get(week_iso) or {}
        if info.get("pending_minutes"):
            parts.append(f"{name} {_hours(info['pending_minutes'])}")
    return (f"Not in Upwork yet: {', '.join(parts)}. The week locks {lock_label(week)}. "
            "Open Export/Invoice → Push to Upwork.")


def window_for(now):
    """Local datetime range covering every open week (for reading entries)."""
    weeks = open_weeks(now)
    start = datetime.combine(weeks[0], time(), tzinfo=timezone.utc).astimezone()
    end = datetime.combine(weeks[-1] + timedelta(days=7), time(), tzinfo=timezone.utc).astimezone()
    return start, end
