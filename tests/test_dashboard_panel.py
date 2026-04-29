"""Regression tests for dashboard popover HTML sizing."""

from datetime import date, datetime, timedelta

import dashboard_panel
from dashboard_panel import DashboardPanelController


class _FakePopover:
    def __init__(self):
        self.sizes = []

    def setContentSize_(self, size):
        self.sizes.append(size)


class _FakeViewController:
    def __init__(self):
        self.sizes = []

    def resizeTo_(self, size):
        self.sizes.append(size)


def _make_controller():
    DashboardPanelController._instance = None
    return DashboardPanelController()


def test_dashboard_html_measures_document_height():
    """Popover sizing should measure document height and observe later layout changes."""
    controller = _make_controller()
    controller.set_exportable_projects([
        {
            "id": "1",
            "name": "Pied Piper",
            "can_export": True,
            "can_invoice": True,
            "stripe_customer_id": "cus_123",
            "upwork_contract_id": "12345678",
            "last_billed_date": "",
            "cap_fill_date": "",
        },
    ])
    controller.set_last_updated(datetime(2026, 4, 6, 17, 4))
    controller.set_rate_limited(True)
    controller.set_error_message("network timeout")
    html = controller._generate_html(
        {
            "total": 812.5,
            "all_projects": [
                {
                    "name": "Acme Inc",
                    "earnings": 562,
                    "hours": 3.8,
                    "billable": True,
                    "time_blocks": [
                        {
                            "start": datetime(2026, 4, 6, 9, 0),
                            "stop": datetime(2026, 4, 6, 9, 30),
                            "duration": 1800,
                            "description": "Write follow-up email",
                        },
                    ],
                },
            ],
        },
        {
            "total": 2291.19,
            "all_projects": [
                {"name": "Globex", "earnings": 600, "hours": 4.5, "billable": True},
            ],
        },
        {
            "total": 5062.5,
            "all_projects": [
                {"name": "Stark Industries", "earnings": 4000, "hours": 1.2, "billable": True},
            ],
            "projection": {},
        },
    )

    assert 'class="section-list"' in html
    assert "scheduleReportHeight" in html
    assert "new ResizeObserver" in html
    assert "refresh_projects" in html
    assert "settings" in html
    assert "update_app" in html
    assert "toggleRefreshMenu" in html
    assert "refresh-primary" in html
    assert "refresh-arrow" in html
    assert "Refresh Data" in html
    assert "Refresh Projects" in html
    assert "Clear All Caches" in html
    assert "Open Cache Folder" in html
    assert "Open Upwork Diary" in html
    assert "open_upwork_diary:" in html
    assert "save_upwork_contract:" in html
    assert "copyDescription(this)" in html
    assert "copy_text:" in html
    assert "Settings" in html
    assert "Advanced" not in html
    assert "Toggl rate limit reached. Showing cached data." in html
    assert "Refresh failed. Showing last successful data." in html
    assert "network timeout" in html
    assert "Retry" in html
    assert "document.body.scrollHeight" in html
    assert "document.documentElement.scrollHeight" in html
    assert "window.addEventListener('resize', function()" in html
    assert "syncFooterClearance();" in html
    assert "Last updated: 05:04 PM" in html


def test_measured_height_reapplies_after_estimate_resizes_popover():
    """A repeated DOM height must still override a newer estimated popover size."""
    controller = _make_controller()
    controller.popover = _FakePopover()
    controller._view_controller = _FakeViewController()
    controller._current_panel_height = 422
    controller._last_measured_height = 386

    controller.set_measured_height(386)

    assert controller._current_panel_height == 386
    assert controller.popover.sizes == [(controller.PANEL_WIDTH, 386)]
    assert controller._view_controller.sizes == [(controller.PANEL_WIDTH, 386)]


def test_measured_height_skips_when_native_size_is_already_current():
    """Duplicate DOM reports should not thrash once the native size already matches."""
    controller = _make_controller()
    controller.popover = _FakePopover()
    controller._view_controller = _FakeViewController()
    controller._current_panel_height = 386
    controller._last_measured_height = 386

    controller.set_measured_height(386)

    assert controller.popover.sizes == []
    assert controller._view_controller.sizes == []


def test_preferred_height_uses_last_measured_value():
    """Once the web view has measured content, later opens should reuse that value."""
    controller = _make_controller()
    controller._last_measured_height = 386

    preferred = controller._preferred_panel_height({}, {}, {})

    assert preferred == 386


def test_month_section_includes_zero_hour_projects_with_targets(monkeypatch):
    """Tracked projects should stay visible before any time has been logged this month."""
    controller = _make_controller()

    monkeypatch.setattr(
        dashboard_panel,
        "load_preferences",
        lambda: {
            "project_targets": {
                "Bench Project": 12,
            },
            "projects": {
                "Retainer Client": {
                    "billing_type": "fixed_monthly",
                    "monthly_amount": 2400,
                    "hour_tracking": "required",
                    "target_hours": 20,
                },
            },
            "dashboard_sections": {
                "today": True,
                "week": True,
                "month": True,
            },
        },
    )
    monkeypatch.setattr(
        dashboard_panel,
        "get_previous_month_balance",
        lambda name: (0.0, "Mar"),
    )

    html = controller._generate_html(
        {"total": 0, "all_projects": []},
        {"total": 0, "all_projects": []},
        {
            "total": 0,
            "all_projects": [
                {"name": "Worked Client", "earnings": 500, "hours": 3.0, "billable": True},
            ],
            "projection": {},
        },
    )

    assert "Bench Project: 0.0h / 12.0h (0%)" in html
    assert "Retainer Client: 0.0h / 20.0h (0%)" in html


def test_lbd_capped_projects_use_billing_cycle_pace(monkeypatch):
    """LBD capped projects should pace against their billing-cycle window, not the calendar month."""
    controller = _make_controller()

    monkeypatch.setattr(
        dashboard_panel,
        "load_preferences",
        lambda: {
            "project_targets": {},
            "projects": {
                "Retainer Client": {
                    "billing_type": "hourly_with_cap",
                    "hourly_rate": 200,
                    "cap_hours": 132,
                    "last_billed_date": "2026-04-10",
                },
            },
            "dashboard_sections": {
                "today": True,
                "week": True,
                "month": True,
            },
        },
    )
    monkeypatch.setattr(
        dashboard_panel,
        "get_previous_month_balance",
        lambda name: (0.0, "Mar"),
    )
    monkeypatch.setattr(
        dashboard_panel,
        "get_lbd_cycle_progress",
        lambda last_billed_date, today=None: 20.0,
    )

    html = controller._generate_html(
        {"total": 0, "all_projects": []},
        {"total": 0, "all_projects": []},
        {
            "total": 6440,
            "all_projects": [
                {
                    "name": "Retainer Client",
                    "earnings": 6440,
                    "hours": 32.2,
                    "billable": True,
                },
            ],
            "projection": {},
        },
    )

    assert "Retainer Client: 32.2h / 132.0h (24%)" in html
    assert "Ahead of pace" in html
    assert 'class="calendar-marker" style="left: 20.0%;' in html


def _early_cycle_prefs():
    return {
        "project_targets": {},
        "projects": {
            "Retainer Client": {
                "billing_type": "hourly_with_cap",
                "hourly_rate": 200,
                "cap_hours": 132,
                "last_billed_date": "2026-04-10",
            },
        },
        "dashboard_sections": {"today": True, "week": True, "month": True},
    }


def _patch_early_cycle(monkeypatch):
    today = date.today()
    monkeypatch.setattr(dashboard_panel, "load_preferences", _early_cycle_prefs)
    monkeypatch.setattr(
        dashboard_panel,
        "get_previous_month_balance",
        lambda name: (0.0, "Mar"),
    )
    monkeypatch.setattr(
        dashboard_panel,
        "get_lbd_cycle_progress",
        lambda last_billed_date, today=None: 100.0 / 33,
    )
    monkeypatch.setattr(
        dashboard_panel,
        "get_lbd_billing_cycle_bounds",
        lambda last_billed_date: (today, today + timedelta(days=32)),
    )


def test_early_cycle_small_lead_shrinks_to_on_pace(monkeypatch):
    """Day 1 of a 33-day cycle: ~1.5pp lead must not trip 'Well ahead'."""
    controller = _make_controller()
    _patch_early_cycle(monkeypatch)

    # 6.0h / 132h = 4.55% vs calendar 3.03% -> raw ratio 1.5 -> "Well ahead"
    # Shrunk on day 1 (weight 0.2): 1.5*0.2 + 0.8 = 1.10 -> "On pace"
    html = controller._generate_html(
        {"total": 0, "all_projects": []},
        {"total": 0, "all_projects": []},
        {
            "total": 1200,
            "all_projects": [
                {"name": "Retainer Client", "earnings": 1200, "hours": 6.0, "billable": True},
            ],
            "projection": {},
        },
    )

    assert "On pace" in html
    assert "Well ahead" not in html


def test_early_cycle_small_lag_shrinks_to_on_pace(monkeypatch):
    """Day 1 of a 33-day cycle: small lag must not trip 'Way behind'."""
    controller = _make_controller()
    _patch_early_cycle(monkeypatch)

    # 1.0h / 132h = 0.76% vs calendar 3.03% -> raw ratio 0.25 -> "Way behind"
    # Shrunk on day 1 (weight 0.2): 0.25*0.2 + 0.8 = 0.85 -> "On pace"
    html = controller._generate_html(
        {"total": 0, "all_projects": []},
        {"total": 0, "all_projects": []},
        {
            "total": 200,
            "all_projects": [
                {"name": "Retainer Client", "earnings": 200, "hours": 1.0, "billable": True},
            ],
            "projection": {},
        },
    )

    assert "On pace" in html
    assert "Way behind" not in html
    assert "Behind" not in html
