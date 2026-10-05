#!/usr/bin/env python3
"""Freelance Tracker MCP server: lets agents read hours, projections, and rules.

Run by an MCP host (Claude Code, Codex) over stdio. It reads the same on-disk
state as the menu bar app and never calls Toggl itself; see
docs/mcp-server-plan.md for the design and docs/mcp-agents.md for setup.

Read-only. Nothing here writes to Toggl, preferences, or carryover.
"""

from __future__ import annotations

import os
import sys

# stdio MCP owns stdout. Project modules print() on error paths, which would
# corrupt the protocol stream, so route stdout to stderr before importing any
# of them. The transport is handed the real stdout explicitly below.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr
os.environ["FREELANCE_TRACKER_CACHE_ONLY"] = "1"

from datetime import date, datetime, timedelta  # noqa: E402
from typing import Any, Dict, List, Optional  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402

import toggl_data  # noqa: E402
from carryover import get_previous_month_balance  # noqa: E402
from diagnostics import _excel_label  # noqa: E402
from pacing import compute_pacing  # noqa: E402
from preferences import CACHE_DIR, load_preferences  # noqa: E402
from toggl_data import (  # noqa: E402
    CacheMissError,
    calculate_monthly_projection,
    calculate_period_earnings,
    get_entries_for_range,
    get_projects,
)


def _enforce_cache_only() -> None:
    """Belt and braces: the env var above covers a fresh interpreter, this
    covers the case where toggl_data was imported before we set it."""
    toggl_data.CACHE_ONLY = True


SERVER_NAME = "freelance-tracker"
MAX_ENTRY_RANGE_DAYS = 92

INSTRUCTIONS = """\
Read-only access to the user's Freelance Tracker: Toggl hours, earnings, the
monthly projection, per-project pacing, and the billing rules behind them.
Data comes from the app's local cache and is as fresh as its last refresh;
every response carries `data_as_of`. This server cannot log time or change
settings. Start with `get_month_status` when asked how the month is going.
"""

mcp = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)


class DisabledError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Guards and shared helpers
# --------------------------------------------------------------------------

def _require_enabled(prefs: Dict[str, Any]) -> None:
    if not prefs.get("mcp_enabled", False):
        raise DisabledError(
            "The MCP server is disabled. Enable it in Freelance Tracker → "
            "Settings → Integrations → Agents (MCP)."
        )


def _finish(prefs: Dict[str, Any], payload: Dict[str, Any], start: date, end: date) -> Dict[str, Any]:
    """Attach freshness metadata and apply anonymization if configured."""
    payload["data_as_of"] = _data_as_of(start, end)
    return _maybe_anonymize(prefs, payload)


# Keys whose values are dollars (rates count: they carry a dollar). Kept as a
# superset of diagnostics._MONEY_KEYS plus this server's own field names.
_MONEY_KEYS = frozenset({
    "hourly_rate", "monthly_amount", "rate", "earnings", "total", "month_total",
    "fixed_earnings", "projected_earnings", "fixed_monthly_total",
    "rev_share_amount", "projected_variable", "daily_average", "capped_ceiling",
    "current_total", "fixed_earnings_so_far", "lbd_current_earnings",
    "lbd_projected_earnings", "variable_earnings",
})
# Dicts whose *values* are all dollars, keyed by project or month.
_MONEY_VALUE_DICTS = frozenset({"retainer_hourly_rates", "monthly_rev_share"})
# Dicts keyed by project name.
_NAME_KEYED_DICTS = frozenset({"projects", "project_targets", "retainer_hourly_rates"})
# String fields holding a project name.
_NAME_FIELDS = frozenset({"name", "project", "project_filter"})


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _collect_project_names(prefs: Dict[str, Any], payload: Any) -> List[str]:
    names = set()
    for key in _NAME_KEYED_DICTS:
        names.update((prefs.get(key) or {}).keys())

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in _NAME_FIELDS and isinstance(v, str):
                    names.add(v)
                walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    walk(payload)
    names.discard("")
    return sorted(names)


def _anonymize(obj: Any, name_map: Dict[str, str], scale: float, in_money_dict=False) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = name_map.get(k, k) if isinstance(k, str) else k
            if k in _NAME_FIELDS and isinstance(v, str):
                out[key] = name_map.get(v, v)
            elif in_money_dict and _is_number(v):
                out[key] = round(v * scale, 2)
            elif k in _MONEY_KEYS and _is_number(v):
                out[key] = round(v * scale, 2)
            elif k in _MONEY_VALUE_DICTS and isinstance(v, dict):
                out[key] = _anonymize(v, name_map, scale, in_money_dict=True)
            else:
                out[key] = _anonymize(v, name_map, scale)
        return out
    if isinstance(obj, list):
        return [_anonymize(x, name_map, scale, in_money_dict) for x in obj]
    return obj


def _maybe_anonymize(prefs: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Replace project names with stable labels and rescale every dollar value
    by one undisclosed constant, as the diagnostics export does. Ratios
    survive; absolute rates and amounts do not."""
    if not prefs.get("mcp_anonymize", False):
        return payload
    names = _collect_project_names(prefs, payload)
    name_map = {n: _excel_label(i) for i, n in enumerate(names)}
    magnitudes: List[float] = []

    def collect(obj, in_money_dict=False):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if (in_money_dict or k in _MONEY_KEYS) and _is_number(v):
                    magnitudes.append(abs(v))
                elif k in _MONEY_VALUE_DICTS and isinstance(v, dict):
                    collect(v, True)
                else:
                    collect(v)
        elif isinstance(obj, list):
            for x in obj:
                collect(x, in_money_dict)

    collect(payload)
    reference = max(magnitudes, default=0.0)
    scale = (1000.0 / reference) if reference > 0 else 1.0
    out = _anonymize(payload, name_map, scale)
    out["anonymized"] = True
    return out


def _shard_mtime(day: date) -> Optional[datetime]:
    path = toggl_data.ENTRY_CACHE_DIR / f"{day.isoformat()}.json"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return None


def _data_as_of(start: date, end: date) -> Optional[str]:
    """Oldest shard mtime in the range; None if any shard is missing."""
    oldest = None
    day = start
    while day <= end:
        mtime = _shard_mtime(day)
        if mtime is None:
            return None
        if oldest is None or mtime < oldest:
            oldest = mtime
        day += timedelta(days=1)
    return oldest.isoformat(timespec="minutes") if oldest else None


def _today() -> date:
    return datetime.now().astimezone().date()


def _month_start(today: date) -> date:
    return today.replace(day=1)


def _week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


def _round(v: Any, nd: int = 2) -> Any:
    if isinstance(v, float):
        return round(v, nd)
    return v


def _slim_project(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": p.get("name"),
        "hours": _round(p.get("hours", 0.0)),
        "earnings": _round(p.get("earnings")),
        "rate": p.get("rate"),
        "rate_source": p.get("rate_source"),
        "billable": p.get("billable"),
    }


def _resolve_target(name, project_targets, projects_config):
    proj_def = projects_config.get(name, {}) or {}
    billing_type = proj_def.get("billing_type")
    hour_tracking = proj_def.get("hour_tracking")
    target = project_targets.get(name)
    if not target and billing_type == "fixed_monthly" and hour_tracking in ("required", "soft"):
        target = proj_def.get("target_hours")
    elif not target and billing_type == "hourly_with_cap":
        target = proj_def.get("cap_hours")
    return target, proj_def, billing_type, hour_tracking


def _tool_error(exc: Exception) -> Dict[str, Any]:
    return {"error": str(exc), "error_type": type(exc).__name__}


# --------------------------------------------------------------------------
# Core builders (pure functions over the data layer; unit-tested directly)
# --------------------------------------------------------------------------

def build_overview() -> Dict[str, Any]:
    prefs = load_preferences()
    _require_enabled(prefs)
    today = _today()

    daily = calculate_period_earnings("daily")
    weekly = calculate_period_earnings("weekly")
    monthly = calculate_period_earnings("monthly")
    projection = calculate_monthly_projection()

    def period(d):
        return {
            "total": _round(d.get("total", 0.0)),
            "hours": _round(d.get("hours", 0.0)),
            "projects": [_slim_project(p) for p in d.get("all_projects", [])],
        }

    payload = {
        "today": period(daily),
        "this_week": period(weekly),
        "this_month": period(monthly),
        "projection": {
            "projected_earnings": _round(projection.get("projected_earnings")),
            "fixed_monthly_total": _round(projection.get("fixed_monthly_total")),
            "rev_share_amount": _round(projection.get("rev_share_amount")),
            "projected_variable": _round(projection.get("projected_variable")),
            "daily_average": _round(projection.get("daily_average")),
            "worked_days": projection.get("worked_days"),
            "workable_days": projection.get("workable_days"),
            "total_business_days": projection.get("total_business_days"),
            "vacation_days": projection.get("vacation_days"),
            "vacation_source": projection.get("vacation_source"),
            "is_projection_capped": projection.get("is_projection_capped"),
        },
        "today_date": today.isoformat(),
    }
    return _finish(prefs, payload, _month_start(today), today)


def build_month_status() -> Dict[str, Any]:
    prefs = load_preferences()
    _require_enabled(prefs)
    today = _today()
    project_targets = prefs.get("project_targets", {}) or {}
    projects_config = prefs.get("projects", {}) or {}

    monthly = calculate_period_earnings("monthly")
    projection = calculate_monthly_projection()
    by_name = {p.get("name"): p for p in monthly.get("all_projects", [])}

    names = list(by_name.keys())
    for name in list(project_targets) + list(projects_config):
        if name not in by_name:
            names.append(name)

    projects: List[Dict[str, Any]] = []
    attention: List[Dict[str, Any]] = []
    for name in dict.fromkeys(names):
        p = by_name.get(name, {"name": name, "hours": 0.0, "billable": False})
        target, proj_def, billing_type, hour_tracking = _resolve_target(
            name, project_targets, projects_config
        )
        row = _slim_project(p)
        row["billing_type"] = billing_type or ("hourly" if p.get("billable") else None)
        row["hour_tracking"] = hour_tracking
        row["cap_fill_date"] = p.get("cap_fill_date")
        row["target_hours"] = target

        if not (p.get("billable") or target or name in projects_config):
            continue
        if p.get("hours", 0) <= 0 and not target:
            continue

        if target:
            carryover_balance, prev_label = 0.0, ""
            if (billing_type == "fixed_monthly" and hour_tracking == "required") \
                    or billing_type == "hourly_with_cap":
                carryover_balance, prev_label = get_previous_month_balance(name)
            pacing = compute_pacing(
                p.get("hours", 0.0), target, carryover_balance, billing_type, proj_def, today=today
            )
            row["carryover_hours"] = carryover_balance
            row["carryover_from"] = prev_label or None
            row["pacing"] = {k: _round(v, 3) for k, v in pacing.items()}
            _rank_attention(attention, name, pacing, billing_type)
        projects.append(row)

    attention.sort(key=lambda a: a["severity"])
    for a in attention:
        a.pop("severity", None)

    payload = {
        "today_date": today.isoformat(),
        "month_total": _round(monthly.get("total", 0.0)),
        "month_hours": _round(monthly.get("hours", 0.0)),
        "projected_earnings": _round(projection.get("projected_earnings")),
        "is_projection_capped": projection.get("is_projection_capped"),
        "worked_days": projection.get("worked_days"),
        "workable_days": projection.get("workable_days"),
        "projects": projects,
        "attention": attention,
    }
    return _finish(prefs, payload, _month_start(today), today)


def _rank_attention(attention, name, pacing, billing_type):
    """Append an attention item for this project, if any. Lower severity sorts first."""
    band = pacing["band"]
    base = {
        "project": name,
        "band": band,
        "hours_needed": _round(pacing["hours_needed"]),
        "remaining_biz_days": pacing["remaining_biz_days"],
        "hours_per_remaining_day": _round(pacing["hours_per_remaining_day"]),
    }
    if band in ("cap_out_of_reach", "way_behind"):
        attention.append({**base, "severity": 0, "reason": pacing["label"]})
    elif band == "behind":
        attention.append({**base, "severity": 1, "reason": pacing["label"]})
    elif (billing_type == "hourly_with_cap" and pacing["cycle_end"]
          and pacing["remaining_biz_days"] is not None
          and pacing["remaining_biz_days"] <= 3 and pacing["hours_needed"] > 0):
        attention.append({
            **base, "severity": 2,
            "reason": f"Billing cycle ends {pacing['cycle_end']} with hours still unfilled",
        })
    elif band == "over_target":
        attention.append({
            **base, "severity": 3,
            "reason": "Over target: extra hours beyond the cap/target are not billable this cycle",
        })
    elif band == "well_ahead":
        attention.append({
            **base, "severity": 4,
            "reason": "Well ahead: hours here could move to a project that is behind",
        })


def build_projection() -> Dict[str, Any]:
    prefs = load_preferences()
    _require_enabled(prefs)
    today = _today()
    projection = calculate_monthly_projection()
    payload = {"today_date": today.isoformat(), **projection}
    return _finish(prefs, payload, _month_start(today), today)


def describe_rule(name: str, defn: Dict[str, Any]) -> str:
    bt = defn.get("billing_type", "hourly")
    if bt == "hourly_with_cap":
        s = f"{name}: hourly at ${defn.get('hourly_rate', 0):g}/h, capped at {defn.get('cap_hours', 0):g}h"
        lbd = defn.get("last_billed_date")
        if lbd:
            try:
                start, end = toggl_data.get_lbd_billing_cycle_bounds(lbd)
                s += f"; unbilled since {lbd}, billing cycle {start.isoformat()} to {end.isoformat()}"
            except ValueError:
                s += f"; last billed {lbd}"
        else:
            s += " per calendar month; overflow hours carry to next month"
        return s
    if bt == "fixed_monthly":
        amt = defn.get("monthly_amount", 0)
        tracking = defn.get("hour_tracking", "none")
        s = f"{name}: fixed ${amt:g}/month (guaranteed income)"
        if tracking == "required":
            s += f"; {defn.get('target_hours', 0):g}h expected, over/under rolls forward as carryover"
        elif tracking == "soft":
            s += f"; {defn.get('target_hours', 0):g}h soft target, no carryover"
        else:
            s += "; no hour tracking"
        return s
    rate = defn.get("hourly_rate")
    return f"{name}: hourly" + (f" at ${rate:g}/h" if rate else " at the Toggl project rate") + ", no cap"


def build_project_rules() -> Dict[str, Any]:
    prefs = load_preferences()
    _require_enabled(prefs)
    today = _today()
    projects_config = prefs.get("projects", {}) or {}
    ym = today.strftime("%Y-%m")

    rules = [describe_rule(n, d) for n, d in projects_config.items() if isinstance(d, dict)]
    for name, hours in (prefs.get("project_targets") or {}).items():
        rules.append(f"{name}: monthly hour target {hours:g}h")
    for name, rate in (prefs.get("retainer_hourly_rates") or {}).items():
        if name not in projects_config:
            rules.append(f"{name}: legacy retainer hourly override ${rate:g}/h")

    payload = {
        "today_date": today.isoformat(),
        "projects": projects_config,
        "project_targets": prefs.get("project_targets", {}),
        "retainer_hourly_rates": prefs.get("retainer_hourly_rates", {}),
        "monthly_rev_share": {ym: (prefs.get("monthly_rev_share") or {}).get(ym, 0)},
        "vacation_days_per_month": prefs.get("vacation_days_per_month"),
        "days_off_keywords": prefs.get("days_off_keywords", []),
        "rules_plain_english": rules,
        "pacing_bands": (
            "Bands on pace_ratio (hours% / calendar%): >=1.5 well_ahead, >=1.15 ahead, "
            ">=0.85 on_pace, >=0.5 behind, else way_behind. Percentage >105 over_target, "
            ">=100 complete, >=95 almost. Capped projects in the last 2 business days "
            "needing >8h/day read cap_out_of_reach."
        ),
    }
    payload["data_as_of"] = datetime.now().astimezone().isoformat(timespec="minutes")
    if prefs.get("mcp_anonymize", False):
        payload["rules_plain_english"] = ["(hidden: anonymization is on)"]
        payload = _maybe_anonymize(prefs, payload)
    return payload


def build_time_entries(start: str, end: str, project: Optional[str] = None) -> Dict[str, Any]:
    prefs = load_preferences()
    _require_enabled(prefs)
    try:
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError(f"Dates must be YYYY-MM-DD: {exc}") from exc
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    if (end_d - start_d).days + 1 > MAX_ENTRY_RANGE_DAYS:
        raise ValueError(f"Range too long: at most {MAX_ENTRY_RANGE_DAYS} days per call")

    start_dt = datetime.combine(start_d, datetime.min.time()).astimezone()
    end_dt = datetime.combine(end_d, datetime.max.time()).astimezone()
    entries = get_entries_for_range(start_dt, end_dt)
    projects_map = get_projects()

    days: Dict[str, Dict[str, Any]] = {}
    total_seconds = 0
    for e in entries:
        if e.get("duration", 0) <= 0 or not e.get("start"):
            continue
        pname = projects_map.get(str(e.get("project_id")), {}).get("name") or "(no project)"
        if project and pname.lower() != project.lower():
            continue
        try:
            start_local = datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone()
        except ValueError:
            continue
        day_key = start_local.date().isoformat()
        bucket = days.setdefault(day_key, {"date": day_key, "hours": 0.0, "entries": []})
        bucket["entries"].append({
            "project": pname,
            "description": e.get("description") or "",
            "start": start_local.isoformat(timespec="minutes"),
            "hours": _round(e["duration"] / 3600),
        })
        bucket["hours"] += e["duration"] / 3600
        total_seconds += e["duration"]

    for b in days.values():
        b["hours"] = _round(b["hours"])
        b["entries"].sort(key=lambda x: x["start"])

    payload = {
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "project_filter": project,
        "total_hours": _round(total_seconds / 3600),
        "days": [days[k] for k in sorted(days)],
    }
    return _finish(prefs, payload, start_d, end_d)


def build_data_freshness() -> Dict[str, Any]:
    prefs = load_preferences()
    today = _today()
    ttl = prefs.get("cache_ttl_today", 1800)
    today_mtime = _shard_mtime(today)
    today_age = (datetime.now().astimezone() - today_mtime).total_seconds() if today_mtime else None

    projects_file = CACHE_DIR / "projects.json"
    try:
        projects_age = datetime.now().timestamp() - projects_file.stat().st_mtime
    except OSError:
        projects_age = None

    return {
        "mcp_enabled": bool(prefs.get("mcp_enabled", False)),
        "mcp_anonymize": bool(prefs.get("mcp_anonymize", False)),
        "today_date": today.isoformat(),
        "today_shard_as_of": today_mtime.isoformat(timespec="minutes") if today_mtime else None,
        "today_shard_stale": (today_age is None) or (today_age > ttl),
        "this_week_as_of": _data_as_of(_week_start(today), today),
        "this_month_as_of": _data_as_of(_month_start(today), today),
        "projects_cache_age_seconds": int(projects_age) if projects_age is not None else None,
        "note": (
            "This server never calls Toggl. To refresh, open the Freelance Tracker "
            "dashboard or press Refresh Now there."
        ),
    }


# --------------------------------------------------------------------------
# MCP surface
# --------------------------------------------------------------------------

def _run(fn, *args, **kwargs) -> Dict[str, Any]:
    _enforce_cache_only()
    try:
        return fn(*args, **kwargs)
    except (DisabledError, CacheMissError, ValueError) as exc:
        return _tool_error(exc)


@mcp.tool()
def get_overview() -> Dict[str, Any]:
    """Today / this week / this month totals and hours, per-project, plus the month projection summary."""
    return _run(build_overview)


@mcp.tool()
def get_month_status() -> Dict[str, Any]:
    """Per-project month status with pacing (percentage, pace_ratio, hours needed per remaining business day) and a ranked `attention` list of projects that need hours. Start here for 'how is my month going'."""
    return _run(build_month_status)


@mcp.tool()
def get_projection() -> Dict[str, Any]:
    """The full monthly earnings projection including the raw `trace` terms and days-off dates."""
    return _run(build_projection)


@mcp.tool()
def get_project_rules() -> Dict[str, Any]:
    """Billing rules per project (hourly / capped / fixed monthly, caps, targets, last billed date), rev share, vacation settings, plus a plain-English rendering of each rule."""
    return _run(build_project_rules)


@mcp.tool()
def get_time_entries(start: str, end: str, project: Optional[str] = None) -> Dict[str, Any]:
    """Cached Toggl time entries between two YYYY-MM-DD dates (inclusive, max 92 days), grouped by day. Optional exact project-name filter."""
    return _run(build_time_entries, start, end, project)


@mcp.tool()
def get_data_freshness() -> Dict[str, Any]:
    """How fresh the cached data is, whether the server is enabled, and how to refresh."""
    return _run(build_data_freshness)


@mcp.prompt()
def progress_checkin() -> str:
    """Discuss how the user's freelance month is going and where to put hours."""
    return (
        "Check in on my freelance month using the freelance-tracker tools.\n\n"
        "1. Call get_month_status first. Lead with the `attention` list: for each item, "
        "say the project, how many hours it still needs, and what that is per remaining "
        "business day. Quote those numbers rather than the pacing label.\n"
        "2. Then summarise the projection from the same response (projected vs. what is "
        "already earned; note if it is capped).\n"
        "3. Treat fixed_monthly projects as guaranteed income; do not suggest adding hours "
        "there unless a required-hours target is behind.\n"
        "4. Be direct. If I am behind, say so plainly; do not soften it.\n"
        "5. If a response contains `error`, tell me what it says instead of guessing. "
        "Data is only as fresh as `data_as_of`.\n"
        "6. You cannot log time or change settings through these tools. Do not offer to."
    )


def main() -> None:
    # Hand the real stdout back for the protocol stream; everything else keeps
    # going to stderr.
    _enforce_cache_only()
    sys.stdout = _REAL_STDOUT
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
