"""Real Toggl API integration for menu bar app."""

import os
import json
import shutil
import requests
import calendar
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
from preferences import CACHE_DIR, load_preferences
from api_audit import log_api_request, is_currently_rate_limited
from carryover import get_balance, get_balance_record, set_balance, get_previous_month_str
from integrations import load_integration_settings

# Configuration
BASE_URL = "https://api.track.toggl.com/api/v9"

# Rate limit state
_rate_limited = False
ENTRY_CACHE_VERSION = 1
ENTRY_CACHE_DIR = CACHE_DIR / "entries" / "by_day"
ENTRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _get_api_token():
    token = load_integration_settings().get("TOGGL_API_TOKEN") or os.getenv("TOGGL_API_TOKEN")
    if not token:
        raise ValueError("TOGGL_API_TOKEN not found. Add it in Settings > Integrations.")
    return token


def _get_workspace_id():
    return load_integration_settings().get("TOGGL_WORKSPACE_ID") or os.getenv("TOGGL_WORKSPACE_ID")


def is_rate_limited():
    """Check if we're currently rate limited."""
    global _rate_limited
    return _rate_limited


def _entry_cache_file_for_day(day):
    ENTRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return ENTRY_CACHE_DIR / f"{day.isoformat()}.json"


def _local_day_bounds(day):
    return (
        datetime.combine(day, datetime.min.time()).astimezone(),
        datetime.combine(day, datetime.max.time()).astimezone(),
    )


def _list_days_in_range(start_date, end_date):
    start_day = start_date.astimezone().date()
    end_day = end_date.astimezone().date()
    days = []
    current = start_day
    while current <= end_day:
        days.append(current)
        current += timedelta(days=1)
    return days


def _partition_entries_by_local_start_day(entries):
    grouped = defaultdict(list)
    for entry in entries:
        start_iso = entry.get("start")
        if not start_iso:
            continue
        try:
            local_day = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone().date()
        except ValueError:
            continue
        grouped[local_day].append(entry)
    return grouped


def _entry_in_range(entry, start_date, end_date):
    start_iso = entry.get("start")
    if not start_iso:
        return False
    try:
        entry_start = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return False
    return start_date <= entry_start <= end_date


def _load_entry_day_payload(day):
    cache_file = _entry_cache_file_for_day(day)
    if cache_file.exists():
        try:
            with open(cache_file, 'r') as f:
                payload = json.load(f)
            if payload.get("version") == ENTRY_CACHE_VERSION and payload.get("day") == day.isoformat():
                return payload
        except Exception:
            pass

    # Upgrade path: reuse legacy full-day daily cache files if present.
    start_dt, end_dt = _local_day_bounds(day)
    legacy_entries = get_cached_entries(f"daily_{day.isoformat()}", start_dt, end_dt)
    if legacy_entries is None:
        return None
    _store_entry_day_payload(day, legacy_entries)
    try:
        with open(cache_file, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def _store_entry_day_payload(day, entries):
    cache_file = _entry_cache_file_for_day(day)
    with open(cache_file, 'w') as f:
        json.dump({
            "version": ENTRY_CACHE_VERSION,
            "day": day.isoformat(),
            "fetched_at": datetime.now().isoformat(),
            "entries": entries,
        }, f)


def _store_entries_for_range(start_date, end_date, entries):
    grouped = _partition_entries_by_local_start_day(entries)
    for day in _list_days_in_range(start_date, end_date):
        _store_entry_day_payload(day, grouped.get(day, []))


def _day_cache_is_fresh(day):
    cache_file = _entry_cache_file_for_day(day)
    if not cache_file.exists():
        return False

    today = datetime.now().astimezone().date()
    if day != today:
        return True

    prefs = load_preferences()
    cache_ttl = prefs.get('cache_ttl_today', 1800)
    cache_age = datetime.now().timestamp() - cache_file.stat().st_mtime
    return cache_age < cache_ttl


def _read_cached_entries_from_day_shards(start_date, end_date, ignore_today_ttl=False):
    all_entries = []
    for day in _list_days_in_range(start_date, end_date):
        payload = _load_entry_day_payload(day)
        if payload is None:
            return None
        if not ignore_today_ttl and not _day_cache_is_fresh(day):
            return None
        all_entries.extend(payload.get("entries", []))
    return [entry for entry in all_entries if _entry_in_range(entry, start_date, end_date)]


def merge_ranges(ranges):
    """Merge overlapping or touching datetime ranges."""
    normalized = []
    for start_date, end_date in ranges:
        if start_date is None or end_date is None:
            continue
        start_local = start_date.astimezone()
        end_local = end_date.astimezone()
        if end_local < start_local:
            start_local, end_local = end_local, start_local
        normalized.append((start_local, end_local))

    if not normalized:
        return []

    normalized.sort(key=lambda pair: pair[0])
    merged = [normalized[0]]
    for start_date, end_date in normalized[1:]:
        last_start, last_end = merged[-1]
        if start_date <= last_end + timedelta(seconds=1):
            merged[-1] = (last_start, max(last_end, end_date))
        else:
            merged.append((start_date, end_date))
    return merged


def _day_ranges_within(start_date, end_date, days):
    ranges = []
    sorted_days = sorted(set(days))
    if not sorted_days:
        return ranges

    run_start = run_end = sorted_days[0]
    for day in sorted_days[1:]:
        if day == run_end + timedelta(days=1):
            run_end = day
            continue
        start_bound, _ = _local_day_bounds(run_start)
        _, end_bound = _local_day_bounds(run_end)
        ranges.append((max(start_date, start_bound), min(end_date, end_bound)))
        run_start = run_end = day

    start_bound, _ = _local_day_bounds(run_start)
    _, end_bound = _local_day_bounds(run_end)
    ranges.append((max(start_date, start_bound), min(end_date, end_bound)))
    return ranges


def _missing_entry_ranges(start_date, end_date):
    missing_days = [
        day for day in _list_days_in_range(start_date, end_date)
        if not _day_cache_is_fresh(day)
    ]
    return _day_ranges_within(start_date, end_date, missing_days)


def refresh_entry_ranges(ranges):
    """Fetch fresh Toggl entries for each merged range and populate day shards."""
    for start_date, end_date in merge_ranges(ranges):
        entries = get_time_entries(start_date, end_date)
        _store_entries_for_range(start_date, end_date, entries)


def invalidate_entry_days(days):
    """Delete shared day shards for the given days."""
    for day in set(days):
        _entry_cache_file_for_day(day).unlink(missing_ok=True)


def clear_all_caches():
    """Delete all cached Toggl data, including shared day shards and legacy cache files."""
    if ENTRY_CACHE_DIR.exists():
        shutil.rmtree(ENTRY_CACHE_DIR)

    for path in CACHE_DIR.iterdir():
        if path == ENTRY_CACHE_DIR:
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            path.unlink(missing_ok=True)

    ENTRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_entries_for_range(start_date, end_date, force_refresh=False):
    """Return raw Toggl entries for a date range using shared day-sharded caches."""
    start_local = start_date.astimezone()
    end_local = end_date.astimezone()
    if end_local < start_local:
        start_local, end_local = end_local, start_local

    if force_refresh:
        refresh_entry_ranges([(start_local, end_local)])
    else:
        missing_ranges = _missing_entry_ranges(start_local, end_local)
        if missing_ranges:
            refresh_entry_ranges(missing_ranges)

    cached_entries = _read_cached_entries_from_day_shards(
        start_local, end_local, ignore_today_ttl=True
    )
    return cached_entries or []


def get_time_entries(start_date, end_date):
    """
    Fetch time entries for a date range.
    Returns cached data if rate limited (402) or the request fails.
    """
    global _rate_limited
    url = f"{BASE_URL}/me/time_entries"
    api_token = _get_api_token()
    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    }

    try:
        response = requests.get(url, auth=(api_token, "api_token"), params=params, timeout=10)

        # Check for rate limiting (402)
        if response.status_code == 402:
            _rate_limited = True
            log_api_request("/me/time_entries", "GET", status_code=402, rate_limited=True, cached=True)

            # Try to return cached data
            cached_data = _read_cached_entries_from_day_shards(
                start_date, end_date, ignore_today_ttl=True
            )
            if cached_data is None:
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
        cached_data = _read_cached_entries_from_day_shards(
            start_date, end_date, ignore_today_ttl=True
        )
        if cached_data is None:
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
    api_token = _get_api_token()

    try:
        response = requests.get(url, auth=(api_token, "api_token"), timeout=10)

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


def create_time_entry(start, duration_seconds, description, project_id=None, billable=True, tags=None):
    """
    Create a new time entry in Toggl.

    Args:
        start: timezone-aware datetime for the entry start
        duration_seconds: positive int for a completed entry's duration
        description: entry description/label
        project_id: optional Toggl project id
        billable: whether the entry is billable
        tags: optional list of tag names

    Returns the created entry dict on success.
    """
    global _rate_limited
    workspace_id = _get_workspace_id()
    if workspace_id is None:
        raise ValueError("TOGGL_WORKSPACE_ID not found. Add it in Settings > Integrations.")
    api_token = _get_api_token()

    if start.tzinfo is None:
        raise ValueError("start datetime must be timezone-aware")

    start_utc = start.astimezone(timezone.utc)
    payload = {
        "created_with": "freelance-tracker",
        "description": description,
        "workspace_id": int(workspace_id),
        "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration": int(duration_seconds),
        "billable": billable,
    }
    if project_id is not None:
        payload["project_id"] = int(project_id)
    if tags:
        payload["tags"] = list(tags)

    url = f"{BASE_URL}/workspaces/{workspace_id}/time_entries"
    endpoint = f"/workspaces/{workspace_id}/time_entries"

    try:
        response = requests.post(
            url,
            auth=(api_token, "api_token"),
            json=payload,
            timeout=10,
        )

        if response.status_code == 402:
            _rate_limited = True
            log_api_request(endpoint, "POST", status_code=402, rate_limited=True)
            raise RuntimeError("Toggl API is rate limited (402); time entry not created.")

        response.raise_for_status()
        _rate_limited = False
        log_api_request(endpoint, "POST", status_code=response.status_code)
        return response.json()

    except requests.exceptions.RequestException as e:
        log_api_request(endpoint, "POST", error=str(e))
        raise


def update_time_entry(entry_id, **fields):
    """
    Update an existing time entry. Pass any subset of:
        description, project_id, billable, tags,
        start (tz-aware datetime), duration_seconds (int),
        stop (tz-aware datetime).

    Returns the updated entry dict on success.
    """
    global _rate_limited
    workspace_id = _get_workspace_id()
    if workspace_id is None:
        raise ValueError("TOGGL_WORKSPACE_ID not found. Add it in Settings > Integrations.")
    api_token = _get_api_token()

    payload = {}
    for key in ("description", "billable", "tags"):
        if key in fields:
            payload[key] = fields[key]
    if "project_id" in fields and fields["project_id"] is not None:
        payload["project_id"] = int(fields["project_id"])
    if "duration_seconds" in fields:
        payload["duration"] = int(fields["duration_seconds"])
    if "start" in fields:
        start = fields["start"]
        if start.tzinfo is None:
            raise ValueError("start datetime must be timezone-aware")
        payload["start"] = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if "stop" in fields:
        stop = fields["stop"]
        if stop.tzinfo is None:
            raise ValueError("stop datetime must be timezone-aware")
        payload["stop"] = stop.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not payload:
        raise ValueError("update_time_entry requires at least one field to update")

    url = f"{BASE_URL}/workspaces/{workspace_id}/time_entries/{entry_id}"
    endpoint = f"/workspaces/{workspace_id}/time_entries/{entry_id}"

    try:
        response = requests.put(
            url,
            auth=(api_token, "api_token"),
            json=payload,
            timeout=10,
        )

        if response.status_code == 402:
            _rate_limited = True
            log_api_request(endpoint, "PUT", status_code=402, rate_limited=True)
            raise RuntimeError("Toggl API is rate limited (402); time entry not updated.")

        response.raise_for_status()
        _rate_limited = False
        log_api_request(endpoint, "PUT", status_code=response.status_code)
        return response.json()

    except requests.exceptions.RequestException as e:
        log_api_request(endpoint, "PUT", error=str(e))
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
    """Fetch entries for the given dashboard period via the shared entry cache."""
    today = datetime.now().astimezone().date()

    if period == "daily":
        start_date = datetime.combine(today, datetime.min.time()).astimezone()
        end_date = datetime.combine(today, datetime.max.time()).astimezone()
    elif period == "weekly":
        start_of_week = today - timedelta(days=today.weekday())
        start_date = datetime.combine(start_of_week, datetime.min.time()).astimezone()
        end_date = datetime.combine(today, datetime.max.time()).astimezone()
    elif period == "monthly":
        start_of_month = today.replace(day=1)
        start_date = datetime.combine(start_of_month, datetime.min.time()).astimezone()
        end_date = datetime.combine(today, datetime.max.time()).astimezone()
    else:
        raise ValueError(f"Unsupported period: {period}")

    return get_entries_for_range(start_date, end_date)


def get_entries_since_date(start_date):
    """Get time entries from start_date to today (inclusive) via the shared cache."""
    today = datetime.now().date()
    start_dt = datetime.combine(start_date, datetime.min.time()).astimezone()
    end_dt = datetime.combine(today, datetime.max.time()).astimezone()
    return get_entries_for_range(start_dt, end_dt)


def _calculate_cap_fill_date(hours_by_day, cap_hours):
    """
    Walk through actual time entries chronologically and find the last date
    where cumulative hours are at or just under the cap.
    Returns a date string (YYYY-MM-DD) or None if under cap (all days fit).
    Only meaningful when total hours exceed the cap.
    """
    if not hours_by_day or cap_hours <= 0:
        return None
    total = sum(hours_by_day.values())
    if total <= cap_hours:
        return None  # all hours fit within cap, no cutoff needed

    # Walk through days in chronological order
    cumulative = 0.0
    last_date_under_cap = None
    for day in sorted(hours_by_day.keys()):
        if cumulative + hours_by_day[day] > cap_hours:
            # This day would push us over — the previous day is the cutoff
            return (last_date_under_cap or day).isoformat()
        cumulative += hours_by_day[day]
        last_date_under_cap = day
        if cumulative == cap_hours:
            return day.isoformat()

    return None


def compute_lbd_cap_fill_date(project_name, last_billed_date, cap_hours, projects_map):
    """Compute the cap fill date for an hourly_with_cap project in last_billed_date mode.

    Returns an ISO date string for the last day on which cumulative unbilled hours
    stay at or under cap_hours, or None if total unbilled hours are within the cap.
    Uses the same cached range fetch as the dashboard's unbilled calculation, so no
    additional API calls in a normal refresh cycle.
    """
    if not last_billed_date or cap_hours <= 0:
        return None
    unbilled_entries = get_entries_since_date(last_billed_date + timedelta(days=1))
    hours_by_day = defaultdict(float)
    for e in unbilled_entries:
        if (projects_map.get(str(e.get('project_id')), {}).get('name') == project_name
                and e.get('duration', 0) > 0):
            entry_date = datetime.fromisoformat(e['start'].replace('Z', '+00:00')).date()
            hours_by_day[entry_date] += e['duration'] / 3600
    return _calculate_cap_fill_date(hours_by_day, cap_hours)


def _get_monthly_project_hours(project_id, projects_map, entries_cache=None):
    """Get total hours for a project in the current month (from cache, no API calls)."""
    monthly_entries = get_entries_with_cache("monthly")
    total = 0.0
    for e in monthly_entries:
        if str(e.get("project_id")) == str(project_id) and e.get("duration", 0) > 0:
            total += e["duration"] / 3600
    return total


def get_effective_project_rate(project_info, retainer_hourly_rates, projects_config=None):
    """
    Resolve the effective rate source for a project.

    Priority:
    1) projects_config: hourly_with_cap → (hourly_rate, "hourly_with_cap")  [cap must apply]
    2) projects_config: fixed_monthly required/soft → (monthly/target, "fixed_monthly")
    3) projects_config: fixed_monthly none → (None, "fixed_monthly_flat")
    4) Toggl billable + Toggl project rate → (rate, "toggl")  [also used for billing_type "hourly"]
    5) Legacy retainer_hourly_rates → (rate, "retainer")
    6) No rate → (None, None)

    Note: projects_config takes priority over Toggl for non-hourly billing types so that
    hourly_with_cap cap logic and fixed_monthly flat amounts are always applied correctly,
    regardless of whether the Toggl project also has a billable rate configured.
    """
    if projects_config is None:
        projects_config = {}

    project_name = project_info.get("name")
    if not project_name:
        return None, None

    proj_def = projects_config.get(project_name)
    if proj_def:
        billing_type = proj_def.get('billing_type')
        if billing_type == 'hourly_with_cap':
            hourly_rate = proj_def.get('hourly_rate')
            if hourly_rate and hourly_rate > 0:
                return hourly_rate, "hourly_with_cap"
        elif billing_type == 'fixed_monthly':
            monthly_amount = proj_def.get('monthly_amount', 0)
            hour_tracking = proj_def.get('hour_tracking', 'none')
            if hour_tracking in ('required', 'soft'):
                target_hours = proj_def.get('target_hours', 0)
                if target_hours > 0:
                    return monthly_amount / target_hours, "fixed_monthly"
            else:
                return None, "fixed_monthly_flat"
        # billing_type == 'hourly': fall through to Toggl rate below

    toggl_rate = project_info.get("rate")
    toggl_billable = project_info.get("billable")
    if toggl_billable and toggl_rate:
        return toggl_rate, "toggl"

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
    projects_config = prefs.get('projects', {})

    now = datetime.now()
    today = now.date()
    biz_days_this_month = calculate_business_days(today.year, today.month)
    prev_month_str, _ = get_previous_month_str()

    # For hourly_with_cap projects with last_billed_date, pre-compute unbilled hours
    # (only relevant for monthly view — daily/weekly show regular period hours)
    lbd_results = {}  # proj_name -> {hours, earnings}
    if period == "monthly":
        for proj_name, defn in projects_config.items():
            if defn.get('billing_type') == 'hourly_with_cap' and defn.get('last_billed_date'):
                try:
                    last_billed = datetime.strptime(defn['last_billed_date'], '%Y-%m-%d').date()
                except ValueError:
                    continue
                unbilled_entries = get_entries_since_date(last_billed + timedelta(days=1))
                hours_by_day = defaultdict(float)
                for e in unbilled_entries:
                    if (projects_map.get(str(e.get('project_id')), {}).get('name') == proj_name
                            and e.get('duration', 0) > 0):
                        entry_date = datetime.fromisoformat(e['start'].replace('Z', '+00:00')).date()
                        hours_by_day[entry_date] += e['duration'] / 3600
                unbilled_h = sum(hours_by_day.values())
                worked_days_count = len(hours_by_day)
                cap_hours = defn.get('cap_hours', 0)
                hourly_rate = defn.get('hourly_rate', 0)
                lbd_results[proj_name] = {
                    'hours': unbilled_h,
                    'earnings': min(unbilled_h, cap_hours) * hourly_rate,
                    'rate': hourly_rate,
                    'cap_fill_date': _calculate_cap_fill_date(hours_by_day, cap_hours),
                }

    # Group entries by project
    project_data = defaultdict(lambda: {"duration": 0, "entries": []})

    for entry in entries:
        if entry.get("duration", 0) <= 0:
            continue
        project_id = str(entry.get("project_id"))
        if project_id and project_id != "None":
            project_data[project_id]["duration"] += entry["duration"]
            project_data[project_id]["entries"].append(entry)

    # Calculate earnings
    total_earnings = 0
    fixed_earnings = 0
    total_hours = 0
    billable_projects_list = []
    all_projects_list = []

    for project_id, data in project_data.items():
        project_info = projects_map.get(project_id, {})
        project_name = project_info.get("name", "Unknown Project")
        hours = data["duration"] / 3600
        total_hours += hours

        effective_rate, rate_source = get_effective_project_rate(
            project_info, retainer_hourly_rates, projects_config
        )
        has_earnings = effective_rate is not None or rate_source == "fixed_monthly_flat"

        project_entry = {
            "name": project_name,
            "hours": hours,
            "billable": has_earnings
        }

        if period == "daily":
            time_blocks = []
            for e in data["entries"]:
                if e.get("duration", 0) <= 0 or not e.get("start"):
                    continue
                try:
                    start_dt = datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone()
                except ValueError:
                    continue
                stop_dt = start_dt + timedelta(seconds=e["duration"])
                time_blocks.append({
                    "start": start_dt,
                    "stop": stop_dt,
                    "duration": e["duration"],
                    "description": e.get("description") or "",
                })
            time_blocks.sort(key=lambda b: b["start"])
            project_entry["time_blocks"] = time_blocks

        all_projects_list.append(project_entry)

        if has_earnings:
            proj_def = projects_config.get(project_name, {})

            if rate_source == "fixed_monthly_flat":
                monthly_amount = proj_def.get('monthly_amount', 0)
                if period == 'monthly':
                    earnings = monthly_amount
                else:
                    days_active = len(set(
                        datetime.fromisoformat(e['start'].replace('Z', '+00:00')).date()
                        for e in data['entries'] if e.get('start')
                    ))
                    earnings = (monthly_amount / biz_days_this_month * days_active
                                if biz_days_this_month > 0 else 0)
                fixed_earnings += earnings

            elif rate_source == "fixed_monthly":
                hour_tracking = proj_def.get('hour_tracking', 'none')
                if hour_tracking == 'soft':
                    target_hours = proj_def.get('target_hours', 0)
                    monthly_amount = proj_def.get('monthly_amount', 0)
                    if period == 'monthly':
                        # Cap at the fixed monthly amount
                        earnings = min(hours * effective_rate, monthly_amount)
                    else:
                        # Daily/weekly: $0 once monthly target is reached
                        monthly_project_hours = _get_monthly_project_hours(
                            project_id, projects_map, entries_cache=None
                        )
                        remaining = max(0, target_hours - (monthly_project_hours - hours))
                        earnings = min(hours, remaining) * effective_rate
                else:
                    earnings = hours * effective_rate
                fixed_earnings += earnings

            elif rate_source == "hourly_with_cap" and period == "monthly":
                if project_name in lbd_results:
                    # last_billed_date mode: use unbilled hours since that date
                    hours = lbd_results[project_name]['hours']
                    project_entry["hours"] = hours
                    earnings = lbd_results[project_name]['earnings']
                    project_entry["cap_fill_date"] = lbd_results[project_name].get('cap_fill_date')
                else:
                    prev_carryover = get_balance(project_name, prev_month_str)
                    # Positive carryover = over-delivered last month → reduces this month's cap
                    effective_cap = max(0, proj_def.get('cap_hours', 0) - max(0, prev_carryover))
                    earnings = min(hours, effective_cap) * effective_rate
                    # Calculate cap fill date for calendar-month mode
                    cal_hours_by_day = defaultdict(float)
                    for e in data['entries']:
                        if e.get('duration', 0) > 0 and e.get('start'):
                            ed = datetime.fromisoformat(e['start'].replace('Z', '+00:00')).date()
                            cal_hours_by_day[ed] += e['duration'] / 3600
                    project_entry["cap_fill_date"] = _calculate_cap_fill_date(cal_hours_by_day, effective_cap)

            else:
                earnings = hours * effective_rate

            total_earnings += earnings
            project_entry["earnings"] = earnings
            project_entry["rate"] = effective_rate
            project_entry["rate_source"] = rate_source
            billable_projects_list.append(project_entry)

    # Add lbd projects that had no entries in the current period (all work was before period start)
    if period == "monthly":
        processed_names = {p['name'] for p in all_projects_list}
        for proj_name, lbd in lbd_results.items():
            if proj_name not in processed_names and lbd['hours'] > 0:
                entry = {
                    'name': proj_name,
                    'hours': lbd['hours'],
                    'earnings': lbd['earnings'],
                    'cap_fill_date': lbd.get('cap_fill_date'),
                    'billable': True,
                    'rate': lbd['rate'],
                    'rate_source': 'hourly_with_cap',
                }
                billable_projects_list.append(entry)
                all_projects_list.append(entry)
                total_earnings += lbd['earnings']

    billable_projects_list.sort(key=lambda x: x["earnings"], reverse=True)
    all_projects_list.sort(key=lambda x: x["hours"], reverse=True)

    return {
        "total": total_earnings,
        "fixed_earnings": fixed_earnings,
        "hours": total_hours,
        "projects": billable_projects_list,
        "all_projects": all_projects_list
    }


def force_refresh_entries():
    """
    Force refresh the shared entry shards used by the current dashboard periods.
    Used for manual "Refresh Now" button.
    Invalidates the visible dashboard ranges plus active billing-cycle ranges so
    edits in Toggl are reflected consistently across dashboard, export, and invoice.
    """
    ranges = _get_manual_refresh_ranges()
    days = []
    for start_date, end_date in merge_ranges(ranges):
        days.extend(_list_days_in_range(start_date, end_date))
    invalidate_entry_days(days)

    # Legacy cleanup for old feature-specific cache namespaces.
    for cache_file in _get_manual_refresh_cache_files():
        cache_file.unlink(missing_ok=True)


def _get_manual_refresh_ranges(today=None):
    """Return the date ranges manual refresh should invalidate."""
    if today is None:
        today = datetime.now().astimezone().date()

    yesterday = today - timedelta(days=1)
    ranges = [(
        datetime.combine(today, datetime.min.time()).astimezone(),
        datetime.combine(today, datetime.max.time()).astimezone(),
    )]

    start_of_week = today - timedelta(days=today.weekday())
    if start_of_week < today:
        ranges.append((
            datetime.combine(start_of_week, datetime.min.time()).astimezone(),
            datetime.combine(yesterday, datetime.max.time()).astimezone(),
        ))

    start_of_month = today.replace(day=1)
    if start_of_month < today:
        ranges.append((
            datetime.combine(start_of_month, datetime.min.time()).astimezone(),
            datetime.combine(yesterday, datetime.max.time()).astimezone(),
        ))

    projects_config = load_preferences().get('projects', {})
    for project_name, defn in projects_config.items():
        if defn.get('billing_type') != 'hourly_with_cap':
            continue
        last_billed_date = defn.get('last_billed_date')
        if last_billed_date:
            try:
                start_date = datetime.strptime(last_billed_date, '%Y-%m-%d').date() + timedelta(days=1)
            except ValueError:
                continue
            ranges.append((
                datetime.combine(start_date, datetime.min.time()).astimezone(),
                datetime.combine(today, datetime.max.time()).astimezone(),
            ))

    prev_month_str, _ = get_previous_month_str(today)
    prev_record_month = prev_month_str
    year, month = map(int, prev_record_month.split('-'))
    prev_month_start = datetime(year, month, 1).date()
    prev_month_end = datetime(year, month, calendar.monthrange(year, month)[1]).date()

    for project_name, defn in projects_config.items():
        needs_carryover = (
            (defn.get('billing_type') == 'fixed_monthly' and defn.get('hour_tracking') == 'required')
            or defn.get('billing_type') == 'hourly_with_cap'
        )
        if not needs_carryover:
            continue
        record = get_balance_record(project_name, prev_record_month)
        if record and record.get("source") == "manual":
            continue
        ranges.append((
            datetime.combine(prev_month_start, datetime.min.time()).astimezone(),
            datetime.combine(prev_month_end, datetime.max.time()).astimezone(),
        ))
        break

    return merge_ranges(ranges)


def _get_manual_refresh_cache_files(today=None):
    """Return legacy cache files that manual refresh should invalidate during migration."""
    if today is None:
        today = datetime.now().astimezone().date()

    yesterday = today - timedelta(days=1)
    cache_files = [CACHE_DIR / f"daily_{today.isoformat()}.json"]

    start_of_week = today - timedelta(days=today.weekday())
    if start_of_week < today:
        cache_files.append(
            CACHE_DIR / f"weekly_{start_of_week.isoformat()}_to_{yesterday.isoformat()}.json"
        )

    start_of_month = today.replace(day=1)
    if start_of_month < today:
        cache_files.append(
            CACHE_DIR / f"monthly_{start_of_month.isoformat()}_to_{yesterday.isoformat()}.json"
        )

    cache_files.extend(sorted(CACHE_DIR.glob(f"lbd_*_to_{today.isoformat()}.json")))
    cache_files.extend(sorted(CACHE_DIR.glob("export_*.json")))
    return cache_files


def estimate_manual_refresh_entry_api_calls(today=None):
    """
    Estimate entry API calls triggered by manual refresh.
    Counts merged dashboard, billing-cycle, and carryover refresh spans.
    """
    if today is None:
        today = datetime.now().astimezone().date()
    return len(_get_manual_refresh_ranges(today=today))


def get_daily_earnings():
    """
    Fetch today's earnings from Toggl.
    Returns same structure as mock_data.get_daily_earnings()
    """
    return calculate_period_earnings("daily")


def get_weekly_earnings():
    """Fetch this week's earnings."""
    return calculate_period_earnings("weekly")


def _try_calculate_last_month_carryover(projects_config):
    """
    Auto-calculate and store last month's carryover balances from the shared entry cache.
    Manual overrides are preserved; auto-derived values are recomputed from the shared
    raw-entry cache so prior-month Toggl edits can flow into current-month carryover.
    """
    today = datetime.now().date()
    prev_month_str, _ = get_previous_month_str(today)
    year, month = map(int, prev_month_str.split('-'))
    last_day = calendar.monthrange(year, month)[1]

    prev_start = datetime(year, month, 1).date()
    prev_end = datetime(year, month, last_day).date()
    prev_start_dt = datetime.combine(prev_start, datetime.min.time()).astimezone()
    prev_end_dt = datetime.combine(prev_end, datetime.max.time()).astimezone()

    try:
        prev_month_entries = get_entries_for_range(prev_start_dt, prev_end_dt)
    except Exception:
        return

    projects_map = get_projects()

    for project_name, defn in projects_config.items():
        billing_type = defn.get('billing_type')
        hour_tracking = defn.get('hour_tracking')

        needs_carryover = (
            (billing_type == 'fixed_monthly' and hour_tracking == 'required') or
            billing_type == 'hourly_with_cap'
        )
        if not needs_carryover:
            continue

        record = get_balance_record(project_name, prev_month_str)
        if record and record.get("source") == "manual":
            continue

        total_hours = sum(
            e["duration"] / 3600
            for e in prev_month_entries
            if projects_map.get(str(e.get("project_id")), {}).get("name") == project_name
               and e.get("duration", 0) > 0
        )

        if billing_type == 'hourly_with_cap':
            cap_hours = defn.get('cap_hours', 0)
            # Only positive overflow carries (under-cap = no carryover for this type)
            balance = max(0.0, total_hours - cap_hours)
        else:
            # fixed_monthly/required: both over and under carry
            # Prior month's effective target also needs its own carryover applied
            prior_carryover = get_balance(project_name, _month_before(prev_month_str))
            effective_target = defn.get('target_hours', 0) - prior_carryover
            balance = total_hours - effective_target

        set_balance(project_name, prev_month_str, balance, source="auto")


def _month_before(year_month_str):
    """Return YYYY-MM string for the month before the given one."""
    year, month = map(int, year_month_str.split('-'))
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def get_monthly_earnings():
    """Fetch this month's earnings with detailed breakdown and projection."""
    prefs = load_preferences()
    projects_config = prefs.get('projects', {})

    # Try to auto-calculate last month's carryover if not yet stored
    if projects_config:
        _try_calculate_last_month_carryover(projects_config)

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
    projects_config = prefs.get('projects', {})

    worked_days = set()

    for entry in entries:
        project_id = str(entry.get("project_id"))
        if project_id and project_id != "None":
            project_info = projects_map.get(project_id, {})
            effective_rate, rate_source = get_effective_project_rate(
                project_info, retainer_hourly_rates, projects_config
            )
            # Count entries that contribute to earnings
            if effective_rate is not None or rate_source == "fixed_monthly_flat":
                # Parse the entry start time to get the date
                start_time = entry.get("start")
                if start_time:
                    # Parse ISO format datetime
                    entry_date = datetime.fromisoformat(start_time.replace('Z', '+00:00')).date()
                    worked_days.add(entry_date.isoformat())

    return worked_days


def calculate_monthly_projection():
    """
    Calculate monthly earnings projection.

    fixed_monthly projects are treated as guaranteed income (full monthly_amount).
    Only hourly/hourly_with_cap earnings are extrapolated by pace.

    Formula: fixed_monthly_total + (variable_earnings / worked_days) * workable_days
    """
    now = datetime.now()
    today = now.date()

    prefs = load_preferences()
    projects_config = prefs.get('projects', {})
    vacation_days = prefs.get('vacation_days_per_month', 4)

    # Get current month's earnings (includes fixed_earnings breakdown)
    monthly_data = calculate_period_earnings("monthly")
    current_total = monthly_data["total"]
    fixed_earnings_so_far = monthly_data.get("fixed_earnings", 0)

    # Guaranteed monthly income = sum of all fixed_monthly project amounts
    fixed_monthly_total = sum(
        defn['monthly_amount']
        for defn in projects_config.values()
        if defn.get('billing_type') == 'fixed_monthly'
    )

    # Remaining business days in current month (from tomorrow onwards)
    last_day_of_month = calendar.monthrange(today.year, today.month)[1]
    remaining_biz_days = sum(
        1 for d in range(today.day + 1, last_day_of_month + 1)
        if datetime(today.year, today.month, d).weekday() < 5
    )

    # Handle hourly_with_cap projects with last_billed_date separately.
    # Their unbilled hours span from last_billed_date+1 to today, crossing month boundaries.
    projects_map = get_projects()
    lbd_current_earnings = 0.0
    lbd_projected_earnings = 0.0

    from carryover import get_balance, get_previous_month_str as _prev_month_str
    _prev_ym, _ = _prev_month_str()

    for proj_name, defn in projects_config.items():
        if defn.get('billing_type') != 'hourly_with_cap' or not defn.get('last_billed_date'):
            continue
        try:
            last_billed = datetime.strptime(defn['last_billed_date'], '%Y-%m-%d').date()
        except ValueError:
            continue

        unbilled_entries = get_entries_since_date(last_billed + timedelta(days=1))
        hours_by_day = defaultdict(float)
        for e in unbilled_entries:
            if (projects_map.get(str(e.get('project_id')), {}).get('name') == proj_name
                    and e.get('duration', 0) > 0):
                entry_date = datetime.fromisoformat(e['start'].replace('Z', '+00:00')).date()
                hours_by_day[entry_date] += e['duration'] / 3600

        unbilled_hours = sum(hours_by_day.values())
        worked_days_in_period = len(hours_by_day)
        cap_hours = defn.get('cap_hours', 0)
        hourly_rate = defn.get('hourly_rate', 0)

        lbd_current_earnings += min(unbilled_hours, cap_hours) * hourly_rate

        if worked_days_in_period > 0:
            daily_avg = unbilled_hours / worked_days_in_period
            projected_unbilled = unbilled_hours + daily_avg * remaining_biz_days
        else:
            projected_unbilled = unbilled_hours

        lbd_projected_earnings += min(projected_unbilled, cap_hours) * hourly_rate

    # Variable earnings so far this month (hourly + hourly_with_cap), excluding lbd projects
    variable_earnings = current_total - fixed_earnings_so_far - lbd_current_earnings

    # Business days and workable days
    total_business_days = calculate_business_days(today.year, today.month)
    workable_days = total_business_days - vacation_days

    # Worked days (days with any earnings-contributing entries)
    worked_days = get_worked_days_this_month()
    worked_days_count = len(worked_days)

    # Project generic variable earnings by pace
    if worked_days_count > 0:
        daily_variable_avg = variable_earnings / worked_days_count
        projected_variable = daily_variable_avg * workable_days
    else:
        daily_variable_avg = 0
        projected_variable = 0

    # Add lbd projected earnings to variable total
    projected_variable += lbd_projected_earnings

    # Cap the projected variable earnings for non-lbd hourly_with_cap projects.
    capped_ceiling = 0.0
    has_uncapped_hourly = False
    for proj_name, defn in projects_config.items():
        bt = defn.get('billing_type', 'hourly')
        if bt == 'hourly_with_cap':
            if defn.get('last_billed_date'):
                continue  # already handled above
            prev_carryover = get_balance(proj_name, _prev_ym)
            effective_cap = max(0.0, defn.get('cap_hours', 0) - max(0.0, prev_carryover))
            capped_ceiling += effective_cap * defn.get('hourly_rate', 0)
        elif bt == 'hourly':
            has_uncapped_hourly = True

    # Add lbd ceilings to capped_ceiling for the overall cap check
    for proj_name, defn in projects_config.items():
        if defn.get('billing_type') == 'hourly_with_cap' and defn.get('last_billed_date'):
            capped_ceiling += defn.get('cap_hours', 0) * defn.get('hourly_rate', 0)

    is_projection_capped = (
        not has_uncapped_hourly
        and capped_ceiling > 0
        and projected_variable > capped_ceiling
    )
    if is_projection_capped:
        projected_variable = capped_ceiling

    projected_total = fixed_monthly_total + projected_variable

    return {
        "projected_earnings": projected_total,
        "fixed_monthly_total": fixed_monthly_total,
        "projected_variable": projected_variable,
        "worked_days": worked_days_count,
        "total_business_days": total_business_days,
        "workable_days": workable_days,
        "vacation_days": vacation_days,
        "daily_average": daily_variable_avg,
        "is_projection_capped": is_projection_capped,
        "capped_ceiling": capped_ceiling if not has_uncapped_hourly else None,
    }
