"""Carryover balance state management for hourly_with_cap and fixed_monthly/required projects."""

import json
from datetime import date, datetime, timedelta
from preferences import APP_SUPPORT_DIR

CARRYOVER_FILE = APP_SUPPORT_DIR / "retainer_carryover.json"

# Positive balance = over-delivered (credit: reduces next month's target/cap)
# Negative balance = under-delivered (owed: increases next month's target/cap)


def load_carryover():
    """Load carryover state from disk."""
    if CARRYOVER_FILE.exists():
        try:
            with open(CARRYOVER_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_carryover(data):
    """Save carryover state to disk."""
    with open(CARRYOVER_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _normalize_balance_record(value):
    """Normalize legacy scalar or new object records into one shape."""
    if value is None:
        return None
    if isinstance(value, dict):
        hours = value.get("hours", 0.0)
        source = value.get("source") or "manual"
        if source not in {"manual", "auto"}:
            source = "manual"
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            hours = 0.0
        return {
            "hours": hours,
            "source": source,
            "updated_at": value.get("updated_at"),
        }

    # Legacy format stored raw numbers only. Treat them as manual overrides so
    # existing user-entered values are never overwritten by auto recomputation.
    try:
        hours = float(value)
    except (TypeError, ValueError):
        hours = 0.0
    return {
        "hours": hours,
        "source": "manual",
        "updated_at": None,
    }


def get_balance_record(project_name, year_month):
    """Return the normalized carryover record for a project/month, or None."""
    data = load_carryover()
    raw = data.get(project_name, {}).get(year_month)
    return _normalize_balance_record(raw)


def get_balance(project_name, year_month):
    """Get carryover balance for a project in a given month (YYYY-MM). Returns 0.0 if not set."""
    record = get_balance_record(project_name, year_month)
    return record["hours"] if record else 0.0


def has_balance(project_name, year_month):
    """Return True if a balance has been explicitly stored (distinguishes stored 0.0 from unset)."""
    data = load_carryover()
    return project_name in data and year_month in data[project_name]


def set_balance(project_name, year_month, hours, source="manual"):
    """Store carryover balance for a project in a given month."""
    data = load_carryover()
    if project_name not in data:
        data[project_name] = {}
    data[project_name][year_month] = {
        "hours": float(hours),
        "source": source,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_carryover(data)


def get_previous_month_str(today=None):
    """Return (year_month_str, month_label) for the month before today. E.g. ('2026-02', 'Feb')."""
    if today is None:
        today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month = first_of_this_month - timedelta(days=1)
    return last_month.strftime('%Y-%m'), last_month.strftime('%b')


def get_previous_month_balance(project_name):
    """Return (balance_hours, month_label) for the previous month. E.g. (-2.5, 'Feb')."""
    year_month, label = get_previous_month_str()
    balance = get_balance(project_name, year_month)
    return balance, label
