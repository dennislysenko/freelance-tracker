"""Monthly project pacing: where a tracked project stands against its cycle.

Pure computation shared by the dashboard (which renders label + colour) and
the MCP server (which hands agents the underlying numbers). The decision tree
is documented in docs/SOT.md under "Pacing decision tree".

`compute_pacing` never touches Toggl. It only needs numbers the caller already
has plus the project definition; the LBD cycle helpers it calls are pure date
math.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date

from toggl_data import (
    get_lbd_billing_cycle_bounds,
    get_lbd_cycle_progress,
    get_lbd_remaining_business_days,
)

GREEN = "#3fb950"
ORANGE = "#f0883e"
RED = "#f85149"
BLUE = "#58a6ff"

BAND_LABELS = {
    "over_target": "Over target",
    "complete": "Complete",
    "almost": "Almost there",
    "well_ahead": "Well ahead — ease off or bank hours",
    "ahead": "Ahead of pace",
    "on_pace": "On pace",
    "behind": "Behind — ramp up to stay on track",
    "way_behind": "Way behind — needs attention",
    "cap_out_of_reach": "Behind — cap out of reach",
}

BAND_COLORS = {
    "over_target": RED,
    "complete": ORANGE,
    "almost": ORANGE,
    "well_ahead": GREEN,
    "ahead": GREEN,
    "on_pace": GREEN,
    "behind": BLUE,
    "way_behind": BLUE,
    "cap_out_of_reach": BLUE,
}

# Bands that mean the project needs hours, most urgent first.
NEEDS_ATTENTION = ("cap_out_of_reach", "way_behind", "behind")


def _remaining_month_biz_days(today):
    days_in_month = _calendar.monthrange(today.year, today.month)[1]
    return sum(
        1 for d in range(today.day + 1, days_in_month + 1)
        if date(today.year, today.month, d).weekday() < 5
    )


def compute_pacing(hours, target, carryover_balance=0.0, billing_type=None,
                   proj_def=None, today=None):
    """Return the pacing state for one tracked project.

    Args:
        hours: hours logged in the cycle so far.
        target: monthly target / cap in hours (must be truthy).
        carryover_balance: signed carryover from the previous month; positive
            means over-delivered last month, which lowers this month's target.
        billing_type: the project's billing type, or None.
        proj_def: the project definition dict (used for `last_billed_date`).
        today: override for tests.

    Returns a dict with the numbers behind the label so callers can be as
    blunt as they like: `pace_ratio` (post-shrinkage), `raw_ratio`,
    `hours_needed`, `hours_per_remaining_day`, `remaining_biz_days`, plus
    `band`, `label`, `color`, `calendar_pct`, `clamped_pct`.
    """
    proj_def = proj_def or {}
    today = today or date.today()

    effective_target = max(0.1, float(target) - float(carryover_balance or 0.0))
    percentage = (hours / effective_target) * 100
    clamped_pct = min(percentage, 100)

    days_in_month = _calendar.monthrange(today.year, today.month)[1]
    calendar_pct = (today.day / days_in_month) * 100
    elapsed_days = today.day
    cycle_start = None
    cycle_end = None
    is_lbd = billing_type == 'hourly_with_cap' and bool(proj_def.get('last_billed_date'))
    if is_lbd:
        try:
            calendar_pct = get_lbd_cycle_progress(proj_def['last_billed_date'], today=today)
            cycle_start, cycle_end = get_lbd_billing_cycle_bounds(proj_def['last_billed_date'])
            elapsed_days = (today - cycle_start).days + 1 if today >= cycle_start else 0
        except ValueError:
            is_lbd = False

    raw_ratio = percentage / max(calendar_pct, 0.1)
    # Bayesian shrinkage toward neutral (1.0) early in the cycle: the ratio is
    # a noisy estimator when the denominator is tiny, so blend it with 1.0
    # weighted by elapsed days (full signal by day 5).
    shrink_weight = min(max(elapsed_days, 0) / 5.0, 1.0)
    pace_ratio = raw_ratio * shrink_weight + 1.0 * (1 - shrink_weight)

    if percentage > 105:
        band = "over_target"
    elif percentage >= 100:
        band = "complete"
    elif percentage >= 95:
        band = "almost"
    elif pace_ratio >= 1.5:
        band = "well_ahead"
    elif pace_ratio >= 1.15:
        band = "ahead"
    elif pace_ratio >= 0.85:
        band = "on_pace"
    elif pace_ratio >= 0.5:
        band = "behind"
    else:
        band = "way_behind"

    # Remaining business days in the cycle. Computed for every project so the
    # MCP side can report hours/day; only capped projects use it for banding.
    remaining_biz_days = None
    if is_lbd:
        try:
            remaining_biz_days = get_lbd_remaining_business_days(
                proj_def['last_billed_date'], today=today)
        except ValueError:
            remaining_biz_days = None
    else:
        remaining_biz_days = _remaining_month_biz_days(today)

    hours_needed = max(0.0, effective_target - hours)
    hours_per_remaining_day = None
    if remaining_biz_days:
        hours_per_remaining_day = hours_needed / remaining_biz_days

    # Late-cycle feasibility guard (capped projects only). The pace ratio
    # above still credits time that hasn't elapsed, so a cap that is
    # mathematically out of reach can still read "on pace" in the final
    # couple of days. If filling the cap would now need more than a full
    # workday (8h) per remaining business day, downgrade. Only ever turns
    # green -> "Behind"; with 3+ business days left this branch does nothing.
    if (billing_type == 'hourly_with_cap' and BAND_COLORS[band] == GREEN
            and calendar_pct >= 80
            and remaining_biz_days is not None and remaining_biz_days <= 2):
        if hours_needed > 0 and (
            remaining_biz_days == 0 or hours_needed / remaining_biz_days > 8.0
        ):
            band = "cap_out_of_reach"

    return {
        "effective_target": effective_target,
        "percentage": percentage,
        "clamped_pct": clamped_pct,
        "calendar_pct": calendar_pct,
        "elapsed_days": elapsed_days,
        "raw_ratio": raw_ratio,
        "pace_ratio": pace_ratio,
        "band": band,
        "label": BAND_LABELS[band],
        "color": BAND_COLORS[band],
        "cycle_start": cycle_start.isoformat() if cycle_start else None,
        "cycle_end": cycle_end.isoformat() if cycle_end else None,
        "remaining_biz_days": remaining_biz_days,
        "hours_needed": hours_needed,
        "hours_per_remaining_day": hours_per_remaining_day,
    }
