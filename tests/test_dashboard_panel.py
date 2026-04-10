"""Regression tests for dashboard popover HTML sizing."""

from datetime import datetime

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
    controller.set_last_updated(datetime(2026, 4, 6, 17, 4))
    controller.set_rate_limited(True)
    controller.set_error_message("network timeout")
    html = controller._generate_html(
        {
            "total": 812.5,
            "all_projects": [
                {"name": "Acme Inc", "earnings": 562, "hours": 3.8, "billable": True},
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
    assert "Settings" in html
    assert "Advanced" not in html
    assert "Toggl rate limit reached. Showing cached data." in html
    assert "Refresh failed. Showing last successful data." in html
    assert "network timeout" in html
    assert "Retry" in html
    assert "document.body.scrollHeight" in html
    assert "document.documentElement.scrollHeight" in html
    assert "window.addEventListener('resize', scheduleReportHeight)" in html
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
