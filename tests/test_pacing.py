"""Table tests for the shared pacing decision tree (pacing.py)."""

from datetime import date, timedelta

import pytest

import pacing
from pacing import compute_pacing


# A mid-month Wednesday: day 15 of 30 -> calendar_pct 50, shrinkage weight 1.
MID = date(2026, 4, 15)


def _band(hours, target=100, **kw):
    return compute_pacing(hours, target, today=MID, **kw)["band"]


@pytest.mark.parametrize("hours, expected", [
    (106, "over_target"),
    (100, "complete"),
    (95, "almost"),
    (80, "well_ahead"),     # ratio 1.6
    (60, "ahead"),          # ratio 1.2
    (45, "on_pace"),        # ratio 0.9
    (30, "behind"),         # ratio 0.6
    (20, "way_behind"),     # ratio 0.4
])
def test_bands_mid_month(hours, expected):
    assert _band(hours) == expected


def test_percentage_beats_ratio_ordering():
    # 96% done on day 15 would be "well ahead" by ratio, but percentage wins.
    assert _band(96) == "almost"


def test_carryover_lowers_effective_target():
    r = compute_pacing(40, 100, carryover_balance=20, today=MID)
    assert r["effective_target"] == 80
    assert r["percentage"] == pytest.approx(50)
    assert r["hours_needed"] == 40


def test_negative_carryover_raises_target():
    r = compute_pacing(40, 100, carryover_balance=-10, today=MID)
    assert r["effective_target"] == 110


@pytest.mark.parametrize("day, weight", [(1, 0.2), (3, 0.6), (5, 1.0), (20, 1.0)])
def test_early_cycle_shrinkage(day, weight):
    today = date(2026, 4, day)
    # 10h of 100 on day N of 30 -> raw ratio = (10 / (N/30*100)) ... compute directly.
    r = compute_pacing(10, 100, today=today)
    expected = r["raw_ratio"] * weight + 1.0 * (1 - weight)
    assert r["pace_ratio"] == pytest.approx(expected)


def test_day_one_big_lead_is_shrunk_to_on_pace():
    # 5h on day 1: raw ratio 1.5, shrunk to 1.1 -> on pace, not "ahead".
    r = compute_pacing(5, 100, today=date(2026, 4, 1))
    assert r["raw_ratio"] == pytest.approx(1.5)
    assert r["band"] == "on_pace"


def test_lbd_project_paces_against_billing_cycle(monkeypatch):
    monkeypatch.setattr(pacing, "get_lbd_cycle_progress",
                        lambda lbd, today=None: 20.0)
    monkeypatch.setattr(pacing, "get_lbd_billing_cycle_bounds",
                        lambda lbd: (date(2026, 4, 11), date(2026, 5, 31)))
    monkeypatch.setattr(pacing, "get_lbd_remaining_business_days",
                        lambda lbd, today=None: 30)
    r = compute_pacing(32.2, 132, 0.0, "hourly_with_cap",
                       {"last_billed_date": "2026-04-10"}, today=MID)
    assert r["calendar_pct"] == 20.0
    assert r["cycle_start"] == "2026-04-11"
    assert r["cycle_end"] == "2026-05-31"
    assert r["elapsed_days"] == 5
    assert r["band"] == "ahead"
    assert r["remaining_biz_days"] == 30
    assert r["hours_per_remaining_day"] == pytest.approx((132 - 32.2) / 30)


def test_lbd_bad_date_falls_back_to_calendar_month():
    r = compute_pacing(50, 100, 0.0, "hourly_with_cap",
                       {"last_billed_date": "not-a-date"}, today=MID)
    assert r["cycle_start"] is None
    assert r["calendar_pct"] == pytest.approx(50)


def test_late_cycle_unreachable_cap_downgrades(monkeypatch):
    monkeypatch.setattr(pacing, "get_lbd_cycle_progress", lambda lbd, today=None: 87.5)
    monkeypatch.setattr(pacing, "get_lbd_billing_cycle_bounds",
                        lambda lbd: (date(2026, 3, 1), date(2026, 4, 17)))
    monkeypatch.setattr(pacing, "get_lbd_remaining_business_days", lambda lbd, today=None: 2)
    # 104/132 = 78.7%, ratio 0.9 -> on pace; 28h / 2 days = 14h/day > 8.
    r = compute_pacing(104, 132, 0.0, "hourly_with_cap",
                       {"last_billed_date": "2026-02-28"}, today=MID)
    assert r["band"] == "cap_out_of_reach"
    assert r["color"] == pacing.BLUE


def test_late_cycle_reachable_cap_stays_green(monkeypatch):
    monkeypatch.setattr(pacing, "get_lbd_cycle_progress", lambda lbd, today=None: 87.5)
    monkeypatch.setattr(pacing, "get_lbd_billing_cycle_bounds",
                        lambda lbd: (date(2026, 3, 1), date(2026, 4, 17)))
    monkeypatch.setattr(pacing, "get_lbd_remaining_business_days", lambda lbd, today=None: 2)
    # 120/132 = 90.9%, ratio 1.04 -> on pace; 12h / 2 = 6h/day, fine.
    r = compute_pacing(120, 132, 0.0, "hourly_with_cap",
                       {"last_billed_date": "2026-02-28"}, today=MID)
    assert r["band"] == "on_pace"


def test_guard_inert_with_three_business_days_left(monkeypatch):
    monkeypatch.setattr(pacing, "get_lbd_cycle_progress", lambda lbd, today=None: 85.0)
    monkeypatch.setattr(pacing, "get_lbd_billing_cycle_bounds",
                        lambda lbd: (date(2026, 3, 1), date(2026, 4, 20)))
    monkeypatch.setattr(pacing, "get_lbd_remaining_business_days", lambda lbd, today=None: 3)
    r = compute_pacing(100, 132, 0.0, "hourly_with_cap",
                       {"last_billed_date": "2026-02-28"}, today=MID)
    assert r["band"] == "on_pace"


def test_guard_does_not_apply_to_fixed_monthly():
    # Last business day of the month, far from target: guard is capped-only.
    today = date(2026, 4, 30)
    r = compute_pacing(90, 100, 0.0, "fixed_monthly", {}, today=today)
    assert r["band"] != "cap_out_of_reach"
    assert r["remaining_biz_days"] == 0
    assert r["hours_per_remaining_day"] is None


def test_calendar_month_remaining_business_days():
    # Wed Apr 15 2026: remaining weekdays Apr 16-30 = 11.
    r = compute_pacing(10, 100, today=MID)
    assert r["remaining_biz_days"] == 11
    assert r["hours_per_remaining_day"] == pytest.approx(90 / 11)


def test_label_and_color_follow_band():
    r = compute_pacing(20, 100, today=MID)
    assert r["label"] == pacing.BAND_LABELS["way_behind"]
    assert r["color"] == pacing.BLUE
