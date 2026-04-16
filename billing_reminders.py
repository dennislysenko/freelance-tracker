"""Scheduling helpers for local billing reminders."""

from __future__ import annotations

import calendar
import json
import re
from datetime import datetime
from pathlib import Path

from preferences import APP_SUPPORT_DIR

REMINDER_STATE_FILE = APP_SUPPORT_DIR / "billing_reminder_state.json"
VALID_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
VALID_TASKS = ("invoice",)
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
VALID_DAY_OF_MONTH = frozenset(range(1, 29)) | {-1, -2, -3}


def resolve_day_of_month(day_of_month, year, month):
    """Return the calendar day this monthly spec targets for a given year/month.

    Positive values (1-28) map to that day directly — 28 is the maximum so the
    reminder always fires, even in February. Negative values count from the
    end: -1 is the last day, -2 is the second-to-last, etc.
    """
    if not isinstance(day_of_month, int) or isinstance(day_of_month, bool):
        return None
    if day_of_month not in VALID_DAY_OF_MONTH:
        return None
    if day_of_month > 0:
        return day_of_month
    _, last_day = calendar.monthrange(year, month)
    return last_day + day_of_month + 1


def load_reminder_state(path: Path = REMINDER_STATE_FILE):
    """Load per-reminder delivery state from disk."""
    if path.exists():
        try:
            with open(path, "r") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_reminder_state(state, path: Path = REMINDER_STATE_FILE):
    """Persist per-reminder delivery state."""
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def clear_reminder_state(reminder=None, path: Path = REMINDER_STATE_FILE):
    """Clear all reminder state or a single reminder key from disk."""
    state = load_reminder_state(path=path)
    if reminder is None:
        save_reminder_state({}, path=path)
        return

    key = reminder_key(reminder)
    if key in state:
        del state[key]
    save_reminder_state(state, path=path)


def reminder_key(reminder):
    """Stable identifier used to dedupe reminder delivery."""
    project_name = str(reminder.get("project_name", "")).strip()
    task = str(reminder.get("task", "")).strip().lower()
    weekday = str(reminder.get("weekday", "")).strip().lower()
    day_of_month = reminder.get("day_of_month")
    reminder_time = str(reminder.get("time", "")).strip()
    schedule_key = f"dom={day_of_month}" if day_of_month else weekday
    return f"{task}|{project_name}|{schedule_key}|{reminder_time}"


def reminder_due(reminder, now=None, state=None):
    """Return True when a reminder should fire at the given local time."""
    now = now or datetime.now()
    state = state or {}

    if not reminder.get("enabled", True):
        return False

    reminder_time = str(reminder.get("time", "")).strip()
    if not TIME_PATTERN.match(reminder_time):
        return False

    day_of_month = reminder.get("day_of_month")
    if day_of_month:
        target_day = resolve_day_of_month(day_of_month, now.year, now.month)
        if target_day is None or now.day != target_day:
            return False
    else:
        weekday = str(reminder.get("weekday", "")).strip().lower()
        if weekday not in VALID_WEEKDAYS:
            return False
        if weekday != VALID_WEEKDAYS[now.weekday()]:
            return False

    hour_str, minute_str = reminder_time.split(":", 1)
    scheduled_minutes = int(hour_str) * 60 + int(minute_str)
    current_minutes = now.hour * 60 + now.minute
    if current_minutes < scheduled_minutes:
        return False

    sent_date = state.get(reminder_key(reminder))
    return sent_date != now.date().isoformat()


def collect_due_reminders(reminders, now=None, state=None):
    """Return all reminders due right now."""
    now = now or datetime.now()
    state = state or {}
    return [reminder for reminder in reminders or [] if reminder_due(reminder, now=now, state=state)]


def mark_reminder_sent(reminder, delivered_on, state=None):
    """Update in-memory state after a reminder is delivered."""
    state = dict(state or {})
    state[reminder_key(reminder)] = delivered_on.isoformat()
    return state


def reminder_notification(reminder):
    """Build user-facing notification strings for a reminder."""
    project_name = str(reminder.get("project_name", "")).strip() or "this project"
    task = str(reminder.get("task", "")).strip().lower()
    if task == "invoice":
        return (
            "Billing reminder",
            project_name,
            f"Invoice for {project_name}.",
        )
    return (
        "Billing reminder",
        project_name,
        f"Handle billing for {project_name}.",
    )
