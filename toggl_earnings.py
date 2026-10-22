#!/usr/bin/env python3
"""
Toggl Track Earnings Calculator
Fetches and calculates earnings for daily, weekly, or monthly periods.
Uses caching to minimize API calls (30 per hour quota).
"""

import os
import sys
import json
import argparse
import requests
import calendar
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_TOKEN = os.getenv("TOGGL_API_TOKEN")
WORKSPACE_ID = os.getenv("TOGGL_WORKSPACE_ID")
BASE_URL = "https://api.track.toggl.com/api/v9"
CACHE_DIR = Path.home() / ".toggl_cache"

if not API_TOKEN:
    raise ValueError("TOGGL_API_TOKEN not found in environment variables. Please check your .env file.")

# Ensure cache directory exists
CACHE_DIR.mkdir(exist_ok=True)


def get_time_entries(start_date, end_date):
    """Fetch time entries for a date range."""
    url = f"{BASE_URL}/me/time_entries"
    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat()
    }

    response = requests.get(url, auth=(API_TOKEN, "api_token"), params=params)
    response.raise_for_status()
    return response.json()


def get_projects():
    """Fetch all projects with their rates (cached)."""
    cache_file = CACHE_DIR / "projects.json"

    # Check if cache exists and is less than 24 hours old
    if cache_file.exists():
        cache_age = datetime.now().timestamp() - cache_file.stat().st_mtime
        if cache_age < 86400:  # 24 hours
            with open(cache_file, 'r') as f:
                return json.load(f)

    # Fetch fresh data
    url = f"{BASE_URL}/me/projects"
    response = requests.get(url, auth=(API_TOKEN, "api_token"))
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
            print(f"[Using cached data for {start_of_week} to {today - timedelta(days=1)}]")
            all_entries.extend(cached_entries)
        else:
            print(f"[Fetching historical data: {start_of_week} to {today - timedelta(days=1)}]")
            historical_entries = get_time_entries(historical_start, historical_end)
            cache_entries(cache_key, historical_entries, historical_start, historical_end)
            all_entries.extend(historical_entries)

    # Today's entries (always fresh)
    if today <= end_of_week:
        print(f"[Fetching today's data: {today}]")
        today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
        today_end = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
        today_entries = get_time_entries(today_start, today_end)
        all_entries.extend(today_entries)

    return all_entries


def format_period_label(period):
    """Generate a label for the current period."""
    today = datetime.now().date()

    if period == "daily":
        return today.strftime("%B %d, %Y")
    elif period == "weekly":
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        return f"{start_of_week.strftime('%b %d')} - {end_of_week.strftime('%b %d, %Y')}"
    elif period == "monthly":
        return today.strftime("%B %Y")


def calculate_earnings(period="daily"):
    """Calculate earnings for the specified period."""
    entries = get_entries_with_cache(period)
    projects = get_projects()

    # Group entries by project
    project_data = defaultdict(lambda: {"duration": 0, "entries": []})

    for entry in entries:
        project_id = str(entry.get("project_id"))
        if project_id and project_id != "None":
            project_data[project_id]["duration"] += entry["duration"]
            project_data[project_id]["entries"].append({
                "description": entry.get("description", "No description"),
                "duration": entry["duration"],
                "date": entry.get("start", "")[:10]  # Extract date
            })

    # Calculate earnings and display results
    period_label = format_period_label(period)
    print(f"\n{'='*70}")
    print(f"TOGGL EARNINGS REPORT - {period.upper()}")
    print(f"{period_label}")
    print(f"{'='*70}\n")

    billable_earnings = 0
    billable_hours = 0
    non_billable_hours = 0

    # Separate billable and non-billable projects
    billable_projects = []
    non_billable_projects = []

    for project_id, data in project_data.items():
        project_info = projects.get(project_id, {})
        hours = data["duration"] / 3600

        project_summary = {
            "name": project_info.get("name", "Unknown Project"),
            "client": project_info.get("client_name"),
            "hours": hours,
            "rate": project_info.get("rate"),
            "billable": project_info.get("billable", False),
            "entries": data["entries"]
        }

        if project_summary["billable"] and project_summary["rate"]:
            billable_projects.append(project_summary)
            earnings = hours * project_summary["rate"]
            billable_earnings += earnings
            billable_hours += hours
        else:
            non_billable_projects.append(project_summary)
            non_billable_hours += hours

    # Display billable projects
    if billable_projects:
        print("BILLABLE PROJECTS:")
        print("-" * 70)
        for proj in sorted(billable_projects, key=lambda x: x["hours"], reverse=True):
            earnings = proj["hours"] * proj["rate"]
            print(f"\n{proj['name']} (${proj['rate']}/hr)")
            if proj["client"]:
                print(f"  Client: {proj['client']}")
            print(f"  Time: {proj['hours']:.2f} hours")
            print(f"  Earnings: ${earnings:.2f}")

            if period == "daily":
                print(f"  Tasks:")
                for entry in proj["entries"]:
                    mins = entry["duration"] / 60
                    print(f"    - {entry['description']} ({mins:.0f}min)")
            else:
                # Group by date for weekly/monthly
                entries_by_date = defaultdict(list)
                for entry in proj["entries"]:
                    entries_by_date[entry["date"]].append(entry)

                print(f"  Daily breakdown:")
                for date in sorted(entries_by_date.keys()):
                    day_duration = sum(e["duration"] for e in entries_by_date[date]) / 3600
                    print(f"    {date}: {day_duration:.2f}h")

        print(f"\n{'─'*70}")
        print(f"Subtotal Billable: {billable_hours:.2f} hours = ${billable_earnings:.2f}")
        print(f"{'─'*70}\n")

    # Display non-billable projects
    if non_billable_projects:
        print("\nNON-BILLABLE PROJECTS:")
        print("-" * 70)
        for proj in sorted(non_billable_projects, key=lambda x: x["hours"], reverse=True):
            print(f"\n{proj['name']}")
            if proj["client"]:
                print(f"  Client: {proj['client']}")
            print(f"  Time: {proj['hours']:.2f} hours")

            if period == "daily":
                print(f"  Tasks:")
                for entry in proj["entries"]:
                    mins = entry["duration"] / 60
                    print(f"    - {entry['description']} ({mins:.0f}min)")

        print(f"\n{'─'*70}")
        print(f"Subtotal Non-Billable: {non_billable_hours:.2f} hours")
        print(f"{'─'*70}\n")

    # Total summary
    total_hours = billable_hours + non_billable_hours
    print(f"\n{'='*70}")
    print(f"TOTAL EARNINGS: ${billable_earnings:.2f}")
    print(f"Total Hours Tracked: {total_hours:.2f} hours")
    if billable_hours > 0:
        avg_rate = billable_earnings / billable_hours
        print(f"Average Billable Rate: ${avg_rate:.2f}/hr")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate Toggl earnings for different time periods")
    parser.add_argument("--period", "-p", choices=["daily", "weekly", "monthly"], default="daily",
                        help="Time period to calculate earnings for (default: daily)")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear all cached data before running")

    args = parser.parse_args()

    if args.clear_cache:
        import shutil
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)
            CACHE_DIR.mkdir()
            print("Cache cleared.\n")

    try:
        calculate_earnings(period=args.period)
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from Toggl API: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
