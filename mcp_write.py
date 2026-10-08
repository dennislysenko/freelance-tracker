"""Write path for the Freelance Tracker MCP server.

Three operations, each on exactly **one** time entry: log, update, delete.
There is no bulk form of any of them — an agent that wants to change two
entries makes two round trips, each separately previewed and confirmed.

Every write is two-step. The first call validates and returns a preview with
a one-shot `confirm` token; nothing reaches Toggl. The second call replays the
same arguments with that token and performs the write. This mirrors the
in-app assistant's propose-then-apply contract: the user sees exactly what
will change, in the conversation, before it changes.

Reads inside this module stay cache-only (0 Toggl calls). The write itself is
1 call, and the day shard is patched in place afterwards so the cache stays
coherent without a refetch.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import date, datetime, time as _time, timedelta
from typing import Any, Dict, Optional, Tuple

import toggl_data
from app_notify import notify_data_changed

# A token is short-lived: it exists to tie a confirmation to the preview the
# user just read, not to be banked for later.
CONFIRM_TTL_SECONDS = 600

# How far back to look for an entry by id when the caller gives no date hint.
LOOKUP_WINDOW_DAYS = 92

# Pending previews, by token. In-process only: the server is spawned per
# agent session, so a token never outlives the conversation that made it.
_pending: Dict[str, Dict[str, Any]] = {}


class WriteDisabledError(RuntimeError):
    pass


class ConfirmationError(ValueError):
    pass


def require_write_enabled(prefs: Dict[str, Any]) -> None:
    if not prefs.get("mcp_write_enabled", False):
        raise WriteDisabledError(
            "Write access is off. The agent can read your hours but not change "
            "them. Turn on 'Allow agents to log and edit time' in Freelance "
            "Tracker → Settings → Integrations → Agents (MCP)."
        )
    if prefs.get("mcp_anonymize", False):
        # Reads come back with project names replaced and amounts rescaled.
        # Writing against data you cannot actually see is how the wrong client
        # gets billed, so the two settings are mutually exclusive by design.
        raise WriteDisabledError(
            "Writes are blocked while 'Anonymize client names and dollar "
            "amounts' is on, because the agent cannot see which project is "
            "which. Turn anonymization off to let it log or edit time."
        )


# --------------------------------------------------------------------------
# Confirmation tokens
# --------------------------------------------------------------------------

def _fingerprint(action: str, args: Dict[str, Any]) -> str:
    """Hash of the exact operation, so a token cannot be replayed with
    different arguments — confirming "delete entry 1" must not delete 2."""
    blob = json.dumps({"action": action, "args": args}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _purge_expired(now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    for token in [t for t, p in _pending.items() if p["expires_at"] <= now]:
        _pending.pop(token, None)


def _issue(action: str, args: Dict[str, Any], preview: Dict[str, Any]) -> Dict[str, Any]:
    _purge_expired()
    token = secrets.token_hex(4)
    _pending[token] = {
        "action": action,
        "fingerprint": _fingerprint(action, args),
        "expires_at": time.time() + CONFIRM_TTL_SECONDS,
    }
    return {
        "needs_confirmation": True,
        "confirm": token,
        "expires_in_seconds": CONFIRM_TTL_SECONDS,
        "next_step": (
            "Show this to the user and get their go-ahead. Then call the same "
            f"tool again with identical arguments plus confirm='{token}'. "
            "Nothing has been written yet."
        ),
        **preview,
    }


def _redeem(token: str, action: str, args: Dict[str, Any]) -> None:
    """Consume a token or raise. One shot: a retry needs a fresh preview."""
    _purge_expired()
    pending = _pending.pop(token, None)
    if pending is None:
        raise ConfirmationError(
            "That confirmation is unknown or has expired. Call the tool again "
            "without `confirm` to get a fresh preview."
        )
    if pending["action"] != action or pending["fingerprint"] != _fingerprint(action, args):
        raise ConfirmationError(
            "That confirmation was issued for a different change. Call the "
            "tool again without `confirm` to preview what you are asking for now."
        )


def clear_pending() -> None:
    """Drop every outstanding preview. Used by tests."""
    _pending.clear()


# --------------------------------------------------------------------------
# Resolving arguments against cached data (0 Toggl calls)
# --------------------------------------------------------------------------

def _projects() -> Dict[str, Dict[str, Any]]:
    return toggl_data.get_projects() or {}


def resolve_project(name: str) -> Tuple[int, str]:
    """Map a project name to its Toggl id. Exact match first, then
    case-insensitive; never a guess between two candidates."""
    projects = _projects()
    by_name = {
        (info or {}).get("name"): pid
        for pid, info in projects.items()
        if (info or {}).get("name")
    }
    if name in by_name:
        return int(by_name[name]), name

    matches = [n for n in by_name if n and n.lower() == (name or "").lower()]
    if len(matches) == 1:
        return int(by_name[matches[0]]), matches[0]

    partial = [n for n in by_name if n and (name or "").lower() in n.lower()]
    if len(partial) == 1:
        return int(by_name[partial[0]]), partial[0]

    known = ", ".join(sorted(n for n in by_name if n))
    if len(partial) > 1:
        raise ValueError(
            f"{name!r} matches more than one project ({', '.join(sorted(partial))}). "
            "Use the exact name."
        )
    raise ValueError(f"Unknown project {name!r}. Known projects: {known}")


def parse_day(value: str, field: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be YYYY-MM-DD, got {value!r}")


def parse_start(day: date, start_time: str) -> datetime:
    """Local wall-clock HH:MM on `day`, as an aware datetime."""
    try:
        hour, minute = (int(part) for part in str(start_time).split(":"))
        return datetime.combine(day, _time(hour, minute)).astimezone()
    except (TypeError, ValueError):
        raise ValueError(f"start_time must be 24-hour HH:MM, got {start_time!r}")


def _check_duration(minutes: Any) -> int:
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        raise ValueError(f"duration_minutes must be a whole number, got {minutes!r}")
    if minutes <= 0:
        raise ValueError("duration_minutes must be greater than zero")
    if minutes > 24 * 60:
        raise ValueError("duration_minutes cannot exceed one day (1440)")
    return minutes


def _fmt(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").lower()


def describe_span(start: datetime, minutes: int) -> str:
    end = start + timedelta(minutes=minutes)
    return (
        f"{start.strftime('%a %b %-d')} {_fmt(start)}-{_fmt(end)} "
        f"({minutes / 60:.2f}h)"
    )


def find_overlaps(start: datetime, minutes: int, ignore_id=None) -> list:
    """Existing cached entries that overlap the proposed span.

    Degrades to [] when the day is not cached: an overlap warning is a
    courtesy, and its absence must never block a confirmed write.
    """
    end = start + timedelta(minutes=minutes)
    day = start.date()
    try:
        entry, _ = None, None
        payload = toggl_data._load_entry_day_payload(day)
    except Exception:
        return []
    if payload is None:
        return []

    projects = _projects()
    out = []
    for existing in payload.get("entries", []):
        if ignore_id is not None and str(existing.get("id")) == str(ignore_id):
            continue
        seconds = existing.get("duration") or 0
        if seconds <= 0 or not existing.get("start"):
            continue
        try:
            e_start = datetime.fromisoformat(
                existing["start"].replace("Z", "+00:00")
            ).astimezone()
        except ValueError:
            continue
        e_end = e_start + timedelta(seconds=seconds)
        if e_start < end and start < e_end:
            out.append({
                "entry_id": existing.get("id"),
                "project": (projects.get(str(existing.get("project_id"))) or {}).get(
                    "name"
                ) or "(no project)",
                "description": existing.get("description") or "",
                "span": describe_span(e_start, int(seconds // 60)),
            })
    return out


def describe_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Human-readable view of a raw Toggl entry."""
    projects = _projects()
    seconds = entry.get("duration") or 0
    start = None
    if entry.get("start"):
        try:
            start = datetime.fromisoformat(
                entry["start"].replace("Z", "+00:00")
            ).astimezone()
        except ValueError:
            start = None
    minutes = int(seconds // 60) if seconds > 0 else 0
    return {
        "entry_id": entry.get("id"),
        "date": start.date().isoformat() if start else None,
        "start_time": start.strftime("%H:%M") if start else None,
        "duration_minutes": minutes,
        "hours": round(minutes / 60, 2),
        "project": (projects.get(str(entry.get("project_id"))) or {}).get("name"),
        "description": entry.get("description") or "",
        "billable": bool(entry.get("billable")),
        "span": describe_span(start, minutes) if start and minutes else None,
    }


def locate_entry(entry_id, day_hint: Optional[str] = None):
    """Find a cached entry by id. Returns (entry, day)."""
    if day_hint:
        day = parse_day(day_hint, "date")
        start_dt = datetime.combine(day, datetime.min.time()).astimezone()
        end_dt = datetime.combine(day, datetime.max.time()).astimezone()
    else:
        today = datetime.now().astimezone().date()
        start_dt = datetime.combine(
            today - timedelta(days=LOOKUP_WINDOW_DAYS), datetime.min.time()
        ).astimezone()
        end_dt = datetime.combine(
            today + timedelta(days=1), datetime.max.time()
        ).astimezone()

    entry, day = toggl_data.find_cached_entry(entry_id, start_dt, end_dt)
    if entry is None:
        where = f"on {day_hint}" if day_hint else f"in the last {LOOKUP_WINDOW_DAYS} days"
        raise ValueError(
            f"No cached time entry with id {entry_id} {where}. Use get_time_entries "
            "to list entries and their ids; if the day was never cached, open the "
            "Freelance Tracker dashboard to populate it."
        )
    return entry, day


# --------------------------------------------------------------------------
# The three operations
# --------------------------------------------------------------------------

def build_log_time(
    prefs: Dict[str, Any],
    date_: str,
    start_time: str,
    duration_minutes: int,
    project: str,
    description: Optional[str] = None,
    billable: bool = True,
    confirm: Optional[str] = None,
) -> Dict[str, Any]:
    """Create one time entry."""
    require_write_enabled(prefs)

    day = parse_day(date_)
    minutes = _check_duration(duration_minutes)
    start = parse_start(day, start_time)
    project_id, project_name = resolve_project(project)
    description = (description or "").strip()
    billable = bool(billable)

    args = {
        "date": day.isoformat(),
        "start_time": start.strftime("%H:%M"),
        "duration_minutes": minutes,
        "project": project_name,
        "description": description,
        "billable": billable,
    }

    if not confirm:
        overlaps = find_overlaps(start, minutes)
        preview = {
            "action": "log_time",
            "summary": (
                f"Log {describe_span(start, minutes)} to {project_name}"
                + (f" — {description}" if description else "")
            ),
            "entry": {**args, "hours": round(minutes / 60, 2)},
        }
        if overlaps:
            preview["overlaps"] = overlaps
            preview["warning"] = (
                f"This overlaps {len(overlaps)} entry already logged that day. "
                "Check with the user before confirming."
            )
        return _issue("log_time", args, preview)

    _redeem(confirm, "log_time", args)
    created = toggl_data.create_time_entry(
        start=start,
        duration_seconds=minutes * 60,
        description=description,
        project_id=project_id,
        billable=billable,
    )
    toggl_data.upsert_cached_entry(created)
    notify_data_changed(f"mcp log_time {created.get('id')}")
    return {
        "ok": True,
        "action": "log_time",
        "summary": f"Logged {describe_span(start, minutes)} to {project_name}.",
        "entry": describe_entry(created),
        "toggl_api_calls": 1,
    }


_UPDATABLE = ("date", "start_time", "duration_minutes", "project", "description", "billable")


def build_update_entry(
    prefs: Dict[str, Any],
    entry_id: int,
    date_: Optional[str] = None,
    start_time: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    project: Optional[str] = None,
    description: Optional[str] = None,
    billable: Optional[bool] = None,
    on_date: Optional[str] = None,
    confirm: Optional[str] = None,
) -> Dict[str, Any]:
    """Change one field or more on one existing entry."""
    require_write_enabled(prefs)

    entry, current_day = locate_entry(entry_id, on_date)
    before = describe_entry(entry)

    requested = {
        "date": date_,
        "start_time": start_time,
        "duration_minutes": duration_minutes,
        "project": project,
        "description": description,
        "billable": billable,
    }
    if all(v is None for v in requested.values()):
        raise ValueError(
            "Nothing to change. Pass at least one of: " + ", ".join(_UPDATABLE)
        )

    # Fold the requested changes onto the entry's current values so the
    # preview, the fingerprint and the Toggl payload all describe the same
    # end state, whichever subset of fields the caller sent.
    new_day = parse_day(date_) if date_ is not None else date.fromisoformat(before["date"])
    new_minutes = (
        _check_duration(duration_minutes)
        if duration_minutes is not None
        else before["duration_minutes"]
    )
    new_start = parse_start(
        new_day, start_time if start_time is not None else before["start_time"]
    )
    if project is not None:
        new_project_id, new_project_name = resolve_project(project)
    else:
        new_project_id, new_project_name = entry.get("project_id"), before["project"]
    new_description = (
        description.strip() if description is not None else before["description"]
    )
    new_billable = bool(billable) if billable is not None else before["billable"]

    after = {
        "date": new_day.isoformat(),
        "start_time": new_start.strftime("%H:%M"),
        "duration_minutes": new_minutes,
        "hours": round(new_minutes / 60, 2),
        "project": new_project_name,
        "description": new_description,
        "billable": new_billable,
    }
    changed = {
        field: {"from": before.get(field), "to": after.get(field)}
        for field in _UPDATABLE
        if before.get(field) != after.get(field)
    }
    if not changed:
        return {
            "ok": True,
            "action": "update_entry",
            "summary": "No change: the entry already matches those values.",
            "entry": before,
            "toggl_api_calls": 0,
        }

    args = {"entry_id": str(entry_id), **{k: after[k] for k in _UPDATABLE}}

    if not confirm:
        overlaps = find_overlaps(new_start, new_minutes, ignore_id=entry_id)
        preview = {
            "action": "update_entry",
            "summary": (
                f"Change entry {entry_id} ({before['span']} · {before['project']}): "
                + "; ".join(f"{f} {v['from']!r} → {v['to']!r}" for f, v in changed.items())
            ),
            "before": before,
            "after": after,
            "changes": changed,
        }
        if overlaps:
            preview["overlaps"] = overlaps
            preview["warning"] = (
                f"The new time overlaps {len(overlaps)} other entry that day."
            )
        return _issue("update_entry", args, preview)

    _redeem(confirm, "update_entry", args)
    fields = {
        "start": new_start,
        "duration_seconds": new_minutes * 60,
        "description": new_description,
        "billable": new_billable,
    }
    if new_project_id is not None:
        fields["project_id"] = new_project_id
    updated = toggl_data.update_time_entry(entry_id, **fields)
    toggl_data.upsert_cached_entry(updated, previous_day=current_day)
    notify_data_changed(f"mcp update_entry {entry_id}")
    return {
        "ok": True,
        "action": "update_entry",
        "summary": f"Updated entry {entry_id}.",
        "before": before,
        "entry": describe_entry(updated),
        "changes": changed,
        "toggl_api_calls": 1,
    }


def build_delete_entry(
    prefs: Dict[str, Any],
    entry_id: int,
    on_date: Optional[str] = None,
    confirm: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete exactly one entry. Not reversible from here."""
    require_write_enabled(prefs)

    entry, day = locate_entry(entry_id, on_date)
    before = describe_entry(entry)
    args = {"entry_id": str(entry_id)}

    if not confirm:
        return _issue("delete_entry", args, {
            "action": "delete_entry",
            "summary": (
                f"Delete entry {entry_id}: {before['span']} · {before['project']}"
                + (f" — {before['description']}" if before["description"] else "")
            ),
            "entry": before,
            "warning": (
                "Deleting cannot be undone from here, and it removes billable "
                "hours. Read the entry back to the user and get an explicit yes "
                "before confirming. Delete one entry per call — never loop."
            ),
        })

    _redeem(confirm, "delete_entry", args)
    toggl_data.delete_time_entry(entry_id)
    toggl_data.remove_cached_entry(entry.get("id"), day)
    notify_data_changed(f"mcp delete_entry {entry_id}")
    return {
        "ok": True,
        "action": "delete_entry",
        "summary": f"Deleted entry {entry_id} ({before['span']} · {before['project']}).",
        "deleted": before,
        "toggl_api_calls": 1,
    }
