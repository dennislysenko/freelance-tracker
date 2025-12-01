"""Real Toggl API integration for menu bar app."""

import os
import json
import requests
import calendar
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv
from preferences import CACHE_DIR, load_preferences

load_dotenv()

# Configuration
API_TOKEN = os.getenv("TOGGL_API_TOKEN")
WORKSPACE_ID = os.getenv("TOGGL_WORKSPACE_ID")
BASE_URL = "https://api.track.toggl.com/api/v9"

if not API_TOKEN:
    raise ValueError("TOGGL_API_TOKEN not found in environment variables. Please check your .env file.")


def get_time_entries(start_date, end_date):
    """Fetch time entries for a date range."""
    url = f"{BASE_URL}/me/time_entries"
    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    }

    response = requests.get(url, auth=(API_TOKEN, "api_token"), params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def get_projects():
    """Fetch all projects with their rates (cached)."""
    cache_file = CACHE_DIR / "projects.json"
    prefs = load_preferences()
    cache_ttl = prefs.get('cache_ttl_projects', 86400)

    # Check if cache exists and is still valid
    if cache_file.exists():
        cache_age = datetime.now().timestamp() - cache_file.stat().st_mtime
        if cache_age < cache_ttl:
            with open(cache_file, 'r') as f:
                return json.load(f)

    # Fetch fresh data
    url = f"{BASE_URL}/me/projects"
    response = requests.get(url, auth=(API_TOKEN, "api_token"), timeout=10)
    response.raise_for_status()

    # Create a mapping of project_id -> project info
    projects = {}
    for project in response.json():
        projects[str(project["id"])] = {
            "name": project["name"],
            "rate": project.get("rate"),
            "billable": project["billable"],
            "client_name": project.get("client_name")
        }

    # Cache it
    with open(cache_file, 'w') as f:
        json.dump(projects, f)

    return projects


def get_cached_entries(cache_key, start_date, end_date):
    """Get cached time entries if they exist and are valid."""
    cache_file = CACHE_DIR / f"{cache_key}.json"

    if cache_file.exists():
        with open(cache_file, 'r') as f:
            cached_data = json.load(f)
            # Verify the cache covers the requested date range
            if (cached_data.get("start_date") == start_date.isoformat() and
                cached_data.get("end_date") == end_date.isoformat()):
                return cached_data.get("entries", [])

    return None


def cache_entries(cache_key, entries, start_date, end_date):
    """Cache time entries."""
    cache_file = CACHE_DIR / f"{cache_key}.json"

    with open(cache_file, 'w') as f:
        json.dump({
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "entries": entries
        }, f)


def get_entries_with_cache(period):
    """
    Fetch entries for the given period with smart caching.
    For weekly/monthly: cache everything before today, fetch today separately.
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    if period == "daily":
        start_date = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_date = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
        return get_time_entries(start_date, end_date)

    elif period == "weekly":
        # Monday to Sunday
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

    elif period == "monthly":
        # First day to last day of current month
        start_of_month = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        end_of_month = today.replace(day=last_day)
        start_of_week = start_of_month
        end_of_week = end_of_month

    # For weekly/monthly: fetch historical and today separately
    all_entries = []

    # Historical entries (before today) - cached
    if start_of_week < today:
        historical_start = datetime.combine(start_of_week, datetime.min.time()).replace(tzinfo=timezone.utc)
        historical_end = datetime.combine(today - timedelta(days=1), datetime.max.time()).replace(tzinfo=timezone.utc)

        cache_key = f"{period}_{start_of_week.isoformat()}_to_{(today - timedelta(days=1)).isoformat()}"
        cached_entries = get_cached_entries(cache_key, historical_start, historical_end)

        if cached_entries is not None:
            all_entries.extend(cached_entries)
        else:
            historical_entries = get_time_entries(historical_start, historical_end)
            cache_entries(cache_key, historical_entries, historical_start, historical_end)
            all_entries.extend(historical_entries)

    # Today's entries (always fresh)
    if today <= end_of_week:
        today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        today_end = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
        today_entries = get_time_entries(today_start, today_end)
        all_entries.extend(today_entries)

    return all_entries


def calculate_period_earnings(period):
    """Calculate earnings for a given period and return in menu bar format."""
    entries = get_entries_with_cache(period)
    projects_map = get_projects()

    # Group entries by project
    project_data = defaultdict(lambda: {"duration": 0, "entries": []})

    for entry in entries:
        project_id = str(entry.get("project_id"))
        if project_id and project_id != "None":
            project_data[project_id]["duration"] += entry["duration"]
            project_data[project_id]["entries"].append(entry)

    # Calculate earnings
    total_earnings = 0
    total_hours = 0
    projects_list = []

    for project_id, data in project_data.items():
        project_info = projects_map.get(project_id, {})
        hours = data["duration"] / 3600
        total_hours += hours

        # Only count billable projects with rates
        if project_info.get("billable") and project_info.get("rate"):
            earnings = hours * project_info["rate"]
            total_earnings += earnings

            projects_list.append({
                "name": project_info.get("name", "Unknown Project"),
                "earnings": earnings,
                "hours": hours,
                "rate": project_info["rate"]
            })

    # Sort projects by earnings (highest first)
    projects_list.sort(key=lambda x: x["earnings"], reverse=True)

    return {
        "total": total_earnings,
        "hours": total_hours,
        "projects": projects_list
    }


def get_daily_earnings():
    """
    Fetch today's earnings from Toggl.
    Returns same structure as mock_data.get_daily_earnings()
    """
    return calculate_period_earnings("daily")


def get_weekly_earnings():
    """Fetch this week's earnings."""
    return calculate_period_earnings("weekly")


def get_monthly_earnings():
    """Fetch this month's earnings."""
    return calculate_period_earnings("monthly")
