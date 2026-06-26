"""Projection diagnostics export.

Bundles a user's projection-relevant config plus the fully-computed monthly
projection / period-earnings breakdown into a single JSON blob they can share
when an on-pace estimate looks wrong. Secrets never enter the blob — API tokens
live in `.env` (see integrations.py), never in preferences.json.

When `anonymize=True` (the default, and what the share button uses):
  - Project names are replaced with stable labels ("Project A", "Project B", ...).
  - Every monetary value (rates AND dollar amounts) is multiplied by a single
    undisclosed constant and the constant is dropped. The projection math is
    linear in dollars, so all ratios and the cap comparison are preserved while
    absolute rates/earnings are removed. The inter-project rate *ratio* is the
    one thing that necessarily survives — it is the math being debugged.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List

from preferences import load_preferences

SCHEMA = "freelance-tracker/diagnostics@1"

# Project definition keys that are safe + relevant to projection math.
_PROJECT_DEF_KEYS = (
    "billing_type",
    "hourly_rate",
    "cap_hours",
    "last_billed_date",
    "monthly_amount",
    "hour_tracking",
    "target_hours",
)

# Per-project earnings keys we keep in the breakdown.
_EARN_PROJECT_KEYS = (
    "name",
    "hours",
    "billable",
    "earnings",
    "rate",
    "rate_source",
    "cap_fill_date",
)

# Scalar keys whose values are denominated in dollars (rate $/hr counts — it
# carries a dollar, so it scales by the same constant as plain amounts).
_MONEY_KEYS = frozenset({
    "hourly_rate", "monthly_amount", "rate", "earnings", "total",
    "fixed_earnings", "projected_earnings", "fixed_monthly_total",
    "rev_share_amount", "projected_variable", "daily_average", "capped_ceiling",
    "current_total", "fixed_earnings_so_far", "lbd_current_earnings",
    "lbd_projected_earnings", "variable_earnings",
})

# Dicts whose *values* (not keys) are all monetary, keyed by project/month.
_MONEY_VALUE_DICTS = frozenset({"retainer_hourly_rates", "monthly_rev_share"})

# Scale so the largest monetary magnitude maps to this, keeping readable digits.
_SCALE_TARGET = 1000.0


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _excel_label(i: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA', ..."""
    label = ""
    i += 1
    while i > 0:
        i, rem = divmod(i - 1, 26)
        label = chr(ord("A") + rem) + label
    return f"Project {label}"


def _slim_projects_config(projects: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for name, defn in (projects or {}).items():
        if not isinstance(defn, dict):
            continue
        out[name] = {k: defn[k] for k in _PROJECT_DEF_KEYS if k in defn}
    return out


def _slim_earnings_projects(projects: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for p in projects or []:
        if isinstance(p, dict):
            out.append({k: p.get(k) for k in _EARN_PROJECT_KEYS})
    return out


def _transform_money(obj: Any, fn: Callable[[float], float], in_value_dict: bool = False) -> Any:
    """Return a copy of `obj` with every monetary number passed through `fn`."""
    if isinstance(obj, dict):
        new: Dict[str, Any] = {}
        for k, v in obj.items():
            if in_value_dict and _is_number(v):
                new[k] = fn(v)
            elif k in _MONEY_KEYS and _is_number(v):
                new[k] = fn(v)
            elif k in _MONEY_VALUE_DICTS and isinstance(v, dict):
                new[k] = _transform_money(v, fn, in_value_dict=True)
            else:
                new[k] = _transform_money(v, fn, in_value_dict=False)
        return new
    if isinstance(obj, list):
        return [_transform_money(x, fn, in_value_dict) for x in obj]
    return obj


def _collect_money(obj: Any) -> List[float]:
    values: List[float] = []
    _transform_money(obj, lambda v: (values.append(abs(v)) or v))
    return values


def _scale_money(blob: Dict[str, Any]) -> Dict[str, Any]:
    reference = max(_collect_money(blob), default=0.0)
    if reference <= 0:
        return blob
    k = _SCALE_TARGET / reference
    return _transform_money(blob, lambda v: round(v * k, 2))


def _collect_names(config: Dict[str, Any], earnings: Dict[str, Any]) -> List[str]:
    names = set()
    names.update((config.get("projects") or {}).keys())
    names.update((config.get("retainer_hourly_rates") or {}).keys())
    names.update((config.get("project_targets") or {}).keys())
    for key in ("projects", "all_projects"):
        for p in earnings.get(key, []) or []:
            if isinstance(p, dict) and p.get("name"):
                names.add(p["name"])
    names.discard(None)
    return sorted(names)


def _anonymize_names(blob: Dict[str, Any]) -> Dict[str, Any]:
    config = blob["config"]
    earnings = blob["month"]["earnings"]
    name_map = {name: _excel_label(i) for i, name in enumerate(_collect_names(config, earnings))}

    def remap(d):
        return {name_map.get(k, k): v for k, v in (d or {}).items()}

    config["projects"] = remap(config.get("projects"))
    config["retainer_hourly_rates"] = remap(config.get("retainer_hourly_rates"))
    config["project_targets"] = remap(config.get("project_targets"))
    # monthly_rev_share is keyed by month (YYYY-MM), not project name — leave it.
    for key in ("projects", "all_projects"):
        for p in earnings.get(key, []) or []:
            if isinstance(p, dict) and p.get("name") in name_map:
                p["name"] = name_map[p["name"]]
    return blob


def build_diagnostics(anonymize: bool = True) -> Dict[str, Any]:
    """Assemble the diagnostics blob. Imports the data layer lazily so this
    module stays importable in tests without Toggl credentials."""
    from toggl_data import calculate_monthly_projection, calculate_period_earnings

    prefs = load_preferences()
    projection = calculate_monthly_projection()
    earnings = calculate_period_earnings("monthly")

    now = datetime.now()
    blob: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": now.date().isoformat(),
        "month": {  # placeholder, replaced below — declared for key order
        },
        "timezone": now.astimezone().tzname(),
        "anonymized": bool(anonymize),
        "config": {
            "vacation_days_per_month": prefs.get("vacation_days_per_month"),
            "projects": _slim_projects_config(prefs.get("projects", {})),
            "retainer_hourly_rates": dict(prefs.get("retainer_hourly_rates", {}) or {}),
            "project_targets": dict(prefs.get("project_targets", {}) or {}),
            "monthly_rev_share": dict(prefs.get("monthly_rev_share", {}) or {}),
        },
    }
    blob["month"] = {
        "key": now.strftime("%Y-%m"),
        "projection": projection,
        "earnings": {
            "total": earnings.get("total"),
            "fixed_earnings": earnings.get("fixed_earnings"),
            "hours": earnings.get("hours"),
            "projects": _slim_earnings_projects(earnings.get("projects", [])),
            "all_projects": _slim_earnings_projects(earnings.get("all_projects", [])),
        },
    }

    if anonymize:
        blob = _anonymize_names(blob)
        blob = _scale_money(blob)
        blob["monetary_note"] = (
            "All rates and dollar amounts multiplied by an undisclosed constant; "
            "absolute values removed, ratios and the cap comparison preserved."
        )

    return blob


def diagnostics_json(anonymize: bool = True) -> str:
    return json.dumps(build_diagnostics(anonymize=anonymize), indent=2, default=str)
