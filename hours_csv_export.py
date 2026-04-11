"""Export Toggl time entries to a billing-ready CSV.

Output is byte-compatible with the format produced by the standalone
`process_toggl_hours.py` script (per-project, previous calendar month).
"""

import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from toggl_data import (
    cache_entries,
    get_cached_entries,
    get_time_entries,
)

CSV_HEADER = [
    "Description",
    "Start date",
    "Start time",
    "End date",
    "End time",
    "Duration",
    "Time Billed (hours)",
    "Hourly Rate (USD)",
    "Money Billed (USD)",
]


def previous_month_range(today=None):
    """Return (first_day, last_day) of the previous calendar month."""
    if today is None:
        today = datetime.now().date()
    first_of_this_month = today.replace(day=1)
    last_of_prev = first_of_this_month - timedelta(days=1)
    first_of_prev = last_of_prev.replace(day=1)
    return first_of_prev, last_of_prev


def format_duration_minutes(seconds):
    """Render seconds as 'M:SS min' (M is total minutes, not zero-padded)."""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d} min"


def _format_rate(rate):
    """Render rate as int if whole, else as default float repr (matches sample)."""
    if float(rate) == int(rate):
        return str(int(rate))
    return str(float(rate))


def build_rows(entries, hourly_rate):
    """Convert Toggl entries into CSV row dicts.

    Filters running/zero-length entries. Converts UTC timestamps to local time.
    Sorts by start datetime descending to match the sample CSV ordering.
    """
    rows = []
    for entry in entries:
        duration_seconds = entry.get("duration", 0)
        if not duration_seconds or duration_seconds <= 0:
            continue

        start_iso = entry.get("start")
        stop_iso = entry.get("stop")
        if not start_iso or not stop_iso:
            continue

        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone()
        stop_dt = datetime.fromisoformat(stop_iso.replace("Z", "+00:00")).astimezone()

        time_billed_hours = duration_seconds / 3600
        money_billed = time_billed_hours * hourly_rate

        rows.append({
            "_sort_key": start_dt,
            "Description": entry.get("description") or "",
            "Start date": start_dt.strftime("%Y-%m-%d"),
            "Start time": start_dt.strftime("%H:%M:%S"),
            "End date": stop_dt.strftime("%Y-%m-%d"),
            "End time": stop_dt.strftime("%H:%M:%S"),
            "Duration": format_duration_minutes(duration_seconds),
            "Time Billed (hours)": str(float(time_billed_hours)),
            "Hourly Rate (USD)": _format_rate(hourly_rate),
            "Money Billed (USD)": str(float(money_billed)),
        })

    rows.sort(key=lambda r: r["_sort_key"], reverse=True)
    for r in rows:
        r.pop("_sort_key", None)
    return rows


def write_csv(rows, output_path):
    """Write header + rows + total row to output_path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_hours = 0.0
    total_money = 0.0
    for r in rows:
        total_hours += float(r["Time Billed (hours)"])
        total_money += float(r["Money Billed (USD)"])

    total_row = {col: "" for col in CSV_HEADER}
    total_row["Description"] = "---- Total ----"
    total_row["Time Billed (hours)"] = str(float(total_hours))
    total_row["Money Billed (USD)"] = str(float(total_money))

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CSV_HEADER,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        writer.writerow(total_row)

    return output_path


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "project"


def export_project_range(
    project_id,
    project_name,
    hourly_rate,
    start_d,
    end_d,
    output_dir=None,
):
    """Fetch entries in [start_d, end_d] for a project and write the CSV.

    Returns the resolved output Path. Raises RuntimeError if no entries match.
    """
    project_entries = get_project_entries_for_range(project_id, project_name, start_d, end_d)

    rows = build_rows(project_entries, hourly_rate)
    if not rows:
        range_label = _format_range_label(start_d, end_d)
        raise RuntimeError(f"No billable entries found for {project_name} ({range_label})")

    if output_dir is None:
        output_dir = Path.home() / "Downloads"
    else:
        output_dir = Path(output_dir)

    filename = _build_filename(project_name, start_d, end_d)
    output_path = output_dir / filename
    return write_csv(rows, output_path)


def get_project_entries_for_range(project_id, project_name, start_d, end_d):
    """Fetch cached-or-live time entries for one project within [start_d, end_d]."""
    start_dt = datetime.combine(start_d, datetime.min.time()).astimezone()
    end_dt = datetime.combine(end_d, datetime.max.time()).astimezone()

    cache_key = f"export_{start_d.isoformat()}_to_{end_d.isoformat()}"
    entries = get_cached_entries(cache_key, start_dt, end_dt)
    if entries is None:
        entries = get_time_entries(start_dt, end_dt)
        cache_entries(cache_key, entries, start_dt, end_dt)

    project_entries = [
        e for e in entries
        if str(e.get("project_id")) == str(project_id)
    ]
    if not project_entries:
        range_label = _format_range_label(start_d, end_d)
        raise RuntimeError(f"No entries found for {project_name} ({range_label})")
    return project_entries


def _format_range_label(start_d, end_d):
    if start_d.day == 1 and end_d == _last_day_of_month(start_d) and start_d.year == end_d.year and start_d.month == end_d.month:
        return start_d.strftime("%b %Y")
    return f"{start_d.isoformat()} to {end_d.isoformat()}"


def _last_day_of_month(d):
    import calendar
    last = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last)


def _build_filename(project_name, start_d, end_d):
    slug = _slugify(project_name)
    if start_d.day == 1 and end_d == _last_day_of_month(start_d) and start_d.year == end_d.year and start_d.month == end_d.month:
        return f"{slug}_{start_d.strftime('%Y-%m')}_hours.csv"
    return f"{slug}_{start_d.isoformat()}_to_{end_d.isoformat()}_hours.csv"
