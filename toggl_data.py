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
from api_audit import log_api_request, is_currently_rate_limited

load_dotenv()

# Configuration
API_TOKEN = os.getenv("TOGGL_API_TOKEN")
WORKSPACE_ID = os.getenv("TOGGL_WORKSPACE_ID")
BASE_URL = "https://api.track.toggl.com/api/v9"

# Rate limit state
_rate_limited = False

if not API_TOKEN:
    raise ValueError("TOGGL_API_TOKEN not found in environment variables. Please check your .env file.")


def is_rate_limited():
    """Check if we're currently rate limited."""
    global _rate_limited
    return _rate_limited


def get_time_entries(start_date, end_date):
    """
    Fetch time entries for a date range.
    Returns cached data if rate limited (402).
    """
    global _rate_limited
    url = f"{BASE_URL}/me/time_entries"
    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    }

    try:
        response = requests.get(url, auth=(API_TOKEN, "api_token"), params=params, timeout=10)

        # Check for rate limiting (402)
        if response.status_code == 402:
            _rate_limited = True
            log_api_request("/me/time_entries", "GET", status_code=402, rate_limited=True, cached=True)

            # Try to return cached data
            cache_key = f"daily_{start_date.date().isoformat()}"
            cached_data = get_cached_entries(cache_key, start_date, end_date)
            if cached_data is not None:
                return cached_data
            else:
                # No cache available, return empty
                return []

        response.raise_for_status()
        _rate_limited = False
        log_api_request("/me/time_entries", "GET", status_code=response.status_code)
        return response.json()

    except requests.exceptions.RequestException as e:
        log_api_request("/me/time_entries", "GET", error=str(e))
        # Try to return cached data on any error
        cache_key = f"daily_{start_date.date().isoformat()}"
        cached_data = get_cached_entries(cache_key, start_date, end_date)
        if cached_data is not None:
            log_api_request("/me/time_entries", "GET", cached=True)
            return cached_data
        raise


def get_projects():
    """
    Fetch all projects with their rates (cached).
    Returns cached data if rate limited (402).
    """
    global _rate_limited
    cache_file = CACHE_DIR / "projects.json"
    prefs = load_preferences()
    cache_ttl = prefs.get('cache_ttl_projects', 86400)

    # Check if cache exists and is still valid
    if cache_file.exists():
        cache_age = datetime.now().timestamp() - cache_file.stat().st_mtime
        if cache_age < cache_ttl:
            log_api_request("/me/projects", "GET", cached=True)
            with open(cache_file, 'r') as f:
                return json.load(f)

    # Fetch fresh data
    url = f"{BASE_URL}/me/projects"

    try:
        response = requests.get(url, auth=(API_TOKEN, "api_token"), timeout=10)

        # Check for rate limiting (402)
        if response.status_code == 402:
            _rate_limited = True
            log_api_request("/me/projects", "GET", status_code=402, rate_limited=True, cached=True)

            # Return cached data even if stale
            if cache_file.exists():
                with open(cache_file, 'r') as f:
                    return json.load(f)
            else:
                return {}

        response.raise_for_status()
        _rate_limited = False
        log_api_request("/me/projects", "GET", status_code=response.status_code)

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

    except requests.exceptions.RequestException as e:
        log_api_request("/me/projects", "GET", error=str(e))
        # Return stale cache on error
        if cache_file.exists():
            log_api_request("/me/projects", "GET", cached=True)
            with open(cache_file, 'r') as f:
                return json.load(f)
        raise


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
    For daily: check cache TTL before fetching.
    For weekly/monthly: cache everything before today, fetch today separately.
    """
    now = datetime.now().astimezone()  # Use local timezone
    today = now.date()

    if period == "daily":
        start_date = datetime.combine(today, datetime.min.time()).astimezone()
        end_date = datetime.combine(today, datetime.max.time()).astimezone()

        # Check if we have valid cached daily data
        cache_key = f"daily_{today.isoformat()}"
        cache_file = CACHE_DIR / f"{cache_key}.json"
        prefs = load_preferences()
        cache_ttl = prefs.get('cache_ttl_today', 1800)  # Default 30 minutes

        if cache_file.exists():
            cache_age = datetime.now().timestamp() - cache_file.stat().st_mtime
            if cache_age < cache_ttl:
                # Cache is still fresh
                cached_entries = get_cached_entries(cache_key, start_date, end_date)
                if cached_entries is not None:
                    log_api_request("/me/time_entries", "GET", cached=True)
                    return cached_entries

        # Cache is stale or doesn't exist, fetch fresh data
        entries = get_time_entries(start_date, end_date)
        # Cache the fresh data
        cache_entries(cache_key, entries, start_date, end_date)
        return entries

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
        historical_start = datetime.combine(start_of_week, datetime.min.time()).astimezone()
        historical_end = datetime.combine(today - timedelta(days=1), datetime.max.time()).astimezone()

        cache_key = f"{period}_{start_of_week.isoformat()}_to_{(today - timedelta(days=1)).isoformat()}"
        cached_entries = get_cached_entries(cache_key, historical_start, historical_end)

        if cached_entries is not None:
            all_entries.extend(cached_entries)
        else:
            historical_entries = get_time_entries(historical_start, historical_end)
            cache_entries(cache_key, historical_entries, historical_start, historical_end)
            all_entries.extend(historical_entries)

    # Today's entries (use cache if fresh)
    if today <= end_of_week:
        today_start = datetime.combine(today, datetime.min.time()).astimezone()
        today_end = datetime.combine(today, datetime.max.time()).astimezone()

        # Check cache for today's data
        today_cache_key = f"daily_{today.isoformat()}"
        today_cache_file = CACHE_DIR / f"{today_cache_key}.json"
        prefs = load_preferences()
        cache_ttl = prefs.get('cache_ttl_today', 1800)

        if today_cache_file.exists():
            cache_age = datetime.now().timestamp() - today_cache_file.stat().st_mtime
            if cache_age < cache_ttl:
                # Use cached today's data
                today_cached = get_cached_entries(today_cache_key, today_start, today_end)
                if today_cached is not None:
                    log_api_request("/me/time_entries", "GET", cached=True)
                    all_entries.extend(today_cached)
                    return all_entries

        # Fetch fresh today's data
        today_entries = get_time_entries(today_start, today_end)
        # Cache it
        cache_entries(today_cache_key, today_entries, today_start, today_end)
        all_entries.extend(today_entries)

    return all_entries


def get_effective_project_rate(project_info, retainer_hourly_rates):
    """
    Resolve the hourly rate used for earnings calculations.

    Priority:
    1) Toggl billable + Toggl project rate
    2) Local retainer hourly override from preferences
    """
    toggl_rate = project_info.get("rate")
    toggl_billable = project_info.get("billable")

    if toggl_billable and toggl_rate:
        return toggl_rate, "toggl"

    project_name = project_info.get("name")
    if not project_name:
        return None, None

    retainer_rate = retainer_hourly_rates.get(project_name)
    if isinstance(retainer_rate, (int, float)) and retainer_rate > 0:
        return retainer_rate, "retainer"

    return None, None


def calculate_period_earnings(period):
    """Calculate earnings for a given period and return in menu bar format."""
    entries = get_entries_with_cache(period)
    projects_map = get_projects()
    prefs = load_preferences()
    retainer_hourly_rates = prefs.get('retainer_hourly_rates', {})

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
    billable_projects_list = []
    all_projects_list = []

    for project_id, data in project_data.items():
        project_info = projects_map.get(project_id, {})
        hours = data["duration"] / 3600
        total_hours += hours

        effective_rate, rate_source = get_effective_project_rate(project_info, retainer_hourly_rates)
        has_earnings = effective_rate is not None

        # Base entry for all projects
        project_entry = {
            "name": project_info.get("name", "Unknown Project"),
            "hours": hours,
            "billable": has_earnings
        }

        # Add to all_projects list
        all_projects_list.append(project_entry)

        # Only count projects with an effective rate
        if has_earnings:
            earnings = hours * effective_rate
            total_earnings += earnings

            # Add earnings info to the project entry
            project_entry["earnings"] = earnings
            project_entry["rate"] = effective_rate
            project_entry["rate_source"] = rate_source

            # Add to billable projects list
            billable_projects_list.append(project_entry)

    # Sort projects by earnings (highest first) for billable
    billable_projects_list.sort(key=lambda x: x["earnings"], reverse=True)

    # Sort all projects by hours (highest first)
    all_projects_list.sort(key=lambda x: x["hours"], reverse=True)

    return {
        "total": total_earnings,
        "hours": total_hours,
        "projects": billable_projects_list,
        "all_projects": all_projects_list
    }


def force_refresh_entries():
    """
    Force refresh today's time entries, bypassing cache.
    Used for manual "Refresh Now" button.
    """
    now = datetime.now().astimezone()
    today = now.date()
    start_date = datetime.combine(today, datetime.min.time()).astimezone()
    end_date = datetime.combine(today, datetime.max.time()).astimezone()

    # Fetch fresh data
    entries = get_time_entries(start_date, end_date)

    # Update cache
    cache_key = f"daily_{today.isoformat()}"
    cache_entries(cache_key, entries, start_date, end_date)

    return entries


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
    """Fetch this month's earnings with detailed breakdown and projection."""
    data = calculate_period_earnings("monthly")

    # Add monthly projection
    projection = calculate_monthly_projection()
    data["projection"] = projection

    return data


def calculate_business_days(year, month):
    """Calculate the number of business days (Mon-Fri) in a given month."""
    import calendar
    _, last_day = calendar.monthrange(year, month)
    business_days = 0

    for day in range(1, last_day + 1):
        date = datetime(year, month, day).date()
        # Monday = 0, Sunday = 6
        if date.weekday() < 5:  # Monday to Friday
            business_days += 1

    return business_days


def get_worked_days_this_month():
    """
    Calculate the number of days this month with earnings-contributing time entries.
    Returns a set of dates (YYYY-MM-DD) where work had an effective rate.
    """
    entries = get_entries_with_cache("monthly")
    projects_map = get_projects()
    prefs = load_preferences()
    retainer_hourly_rates = prefs.get('retainer_hourly_rates', {})

    worked_days = set()

    for entry in entries:
        project_id = str(entry.get("project_id"))
        if project_id and project_id != "None":
            project_info = projects_map.get(project_id, {})
            effective_rate, _ = get_effective_project_rate(project_info, retainer_hourly_rates)
            # Count entries that contribute to earnings
            if effective_rate is not None:
                # Parse the entry start time to get the date
                start_time = entry.get("start")
                if start_time:
                    # Parse ISO format datetime
                    entry_date = datetime.fromisoformat(start_time.replace('Z', '+00:00')).date()
                    worked_days.add(entry_date.isoformat())

    return worked_days


def calculate_monthly_projection():
    """
    Calculate monthly earnings projection based on worked days vs workable days.
    Formula: (current_earnings / worked_days) * workable_days
    Workable days = business_days - vacation_days
    """
    from preferences import load_preferences

    now = datetime.now()
    today = now.date()

    # Get current month's earnings
    monthly_data = calculate_period_earnings("monthly")
    current_earnings = monthly_data["total"]

    # Calculate business days in this month
    total_business_days = calculate_business_days(today.year, today.month)

    # Get vacation days preference
    prefs = load_preferences()
    vacation_days = prefs.get('vacation_days_per_month', 4)

    # Calculate workable days (business days minus vacation)
    workable_days = total_business_days - vacation_days

    # Get worked days (days with earnings-contributing time)
    worked_days = get_worked_days_this_month()
    worked_days_count = len(worked_days)

    # Calculate projection
    if worked_days_count > 0:
        daily_average = current_earnings / worked_days_count
        projection = daily_average * workable_days
    else:
        projection = 0

    return {
        "projected_earnings": projection,
        "worked_days": worked_days_count,
        "total_business_days": total_business_days,
        "workable_days": workable_days,
        "vacation_days": vacation_days,
        "daily_average": daily_average if worked_days_count > 0 else 0
    }
