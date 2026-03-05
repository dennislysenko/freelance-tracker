"""Carryover balance state management for hourly_with_cap and fixed_monthly/required projects."""

import json
from datetime import date, timedelta
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


def get_balance(project_name, year_month):
    """Get carryover balance for a project in a given month (YYYY-MM). Returns 0.0 if not set."""
    data = load_carryover()
    return data.get(project_name, {}).get(year_month, 0.0)


def has_balance(project_name, year_month):
    """Return True if a balance has been explicitly stored (distinguishes stored 0.0 from unset)."""
    data = load_carryover()
    return project_name in data and year_month in data[project_name]


def set_balance(project_name, year_month, hours):
    """Store carryover balance for a project in a given month."""
    data = load_carryover()
    if project_name not in data:
        data[project_name] = {}
    data[project_name][year_month] = hours
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
