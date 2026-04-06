"""Rich dashboard popover for Freelance Tracker menu bar app.

Uses NSPopover with a WKWebView to show a styled dashboard when
the status bar item is clicked - similar to how Claude Usage Tracker works.
"""

import objc
from AppKit import (
    NSMakeRect, NSEvent, NSViewController, NSView, NSColor, NSAppearance,
)
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController
from preferences import load_preferences, save_preferences, DEFAULT_PREFERENCES
from carryover import get_previous_month_balance

# Import NSPopover and related constants
NSPopover = objc.lookUpClass('NSPopover')
NSMinYEdge = 1  # Show below the button


def _debug(msg):
    from datetime import datetime as _dt
    with open('/tmp/freelance_dashboard_debug.log', 'a') as f:
        f.write(f"{_dt.now().isoformat()} [panel] {msg}\n")


class ActionMessageHandler(objc.lookUpClass('NSObject')):
    """Handles JavaScript messages from the dashboard WebView."""

    def initWithController_callbacks_(self, controller, callbacks):
        self = objc.super(ActionMessageHandler, self).init()
        if self is None:
            return None
        self._controller = controller
        self._callbacks = callbacks
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        action = str(message.body())
        if action.startswith("height:"):
            try:
                self._controller.set_measured_height(int(action.split(":", 1)[1]))
            except (TypeError, ValueError):
                pass
            return
        if action.startswith("toggle:"):
            _, section_key, state = action.split(":", 2)
            self._controller.set_section_expanded(section_key, state == "expanded")
            return
        callback = self._callbacks.get(action)
        if callback:
            callback()


class DashboardViewController(NSViewController):
    """NSViewController that hosts the WKWebView for the popover content."""

    def initWithSize_messageHandler_(self, size, message_handler):
        self = objc.super(DashboardViewController, self).init()
        if self is None:
            return None
        self._size = size
        self._message_handler = message_handler
        self.webview = None
        return self

    def loadView(self):
        w, h = self._size
        container = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        container.setAutoresizingMask_(2 | 16)  # width + height sizable
        # Set dark background on the container to prevent white flash
        container.setWantsLayer_(True)
        container.layer().setBackgroundColor_(
            NSColor.colorWithRed_green_blue_alpha_(0.11, 0.11, 0.12, 1.0).CGColor()
        )

        # Configure WKWebView with message handler
        config = WKWebViewConfiguration.alloc().init()
        content_controller = WKUserContentController.alloc().init()
        content_controller.addScriptMessageHandler_name_(self._message_handler, "action")
        config.setUserContentController_(content_controller)

        # Inject a user script that sets dark background immediately before page loads
        from WebKit import WKUserScript
        dark_bg_script = WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            "document.documentElement.style.background='#1c1c1e';",
            0,  # WKUserScriptInjectionTimeAtDocumentStart
            True
        )
        content_controller.addUserScript_(dark_bg_script)

        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, w, h), config
        )
        # Use setValue:forKey: for _drawsBackground (private but standard approach)
        try:
            self.webview.setValue_forKey_(False, "_drawsBackground")
        except Exception:
            pass
        self.webview.setTranslatesAutoresizingMaskIntoConstraints_(False)
        if hasattr(self.webview, 'setInspectable_'):
            self.webview.setInspectable_(True)

        container.addSubview_(self.webview)
        self.webview.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()).setActive_(True)
        self.webview.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()).setActive_(True)
        self.webview.topAnchor().constraintEqualToAnchor_(container.topAnchor()).setActive_(True)
        self.webview.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor()).setActive_(True)
        self.setView_(container)
        if hasattr(self, 'setPreferredContentSize_'):
            self.setPreferredContentSize_((w, h))

    def loadHTML_(self, html):
        if self.webview:
            self.webview.loadHTMLString_baseURL_(html, None)

    def resizeTo_(self, size):
        """Resize the host view and webview to match the popover size."""
        self._size = size
        if not self.isViewLoaded():
            return

        w, h = size
        if hasattr(self, 'setPreferredContentSize_'):
            self.setPreferredContentSize_(size)

        view = self.view()
        view.setFrameSize_((w, h))
        view.setBoundsSize_((w, h))
        view.layoutSubtreeIfNeeded()


class DashboardPanelController:
    """
    Manages the dashboard popover using NSPopover.
    Shows earnings data with styled HTML/CSS in a WKWebView.
    """

    _instance = None
    PANEL_WIDTH = 420
    PANEL_MIN_HEIGHT = 260
    PANEL_MAX_HEIGHT = 620

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.popover = None
        self._view_controller = None
        self._global_monitor = None
        self._local_monitor = None
        self._callbacks = {}
        self._last_data = None
        self._last_updated_at = None
        self._is_rate_limited = False
        self._error_message = None
        self._message_handler = None
        self._last_measured_height = None
        self._current_panel_height = None

    def set_callbacks(self, callbacks):
        """Set action callbacks for dashboard buttons/menu items."""
        self._callbacks = callbacks

    def set_last_updated(self, updated_at):
        """Store the last successful update time for display in the dashboard."""
        self._last_updated_at = updated_at

    def set_rate_limited(self, rate_limited):
        """Store whether the latest dashboard data came from rate-limited fallback."""
        self._is_rate_limited = bool(rate_limited)

    def set_error_message(self, error_message):
        """Store the latest dashboard refresh error, if any."""
        self._error_message = str(error_message) if error_message else None

    def refresh_contents(self):
        """Reload the current dashboard HTML to reflect status-only changes."""
        if not self.popover or not self._view_controller:
            return

        daily, weekly, monthly = self._last_data or self._placeholder_data()
        if self.popover.isShown():
            self._resize_for_data(daily, weekly, monthly)
        html = self._generate_html(daily, weekly, monthly)
        self._view_controller.loadHTML_(html)

    def _placeholder_data(self):
        """Fallback data used when the dashboard has status but no successful fetch yet."""
        return (
            {"total": 0, "hours": 0, "all_projects": []},
            {"total": 0, "hours": 0, "all_projects": []},
            {"total": 0, "hours": 0, "all_projects": [], "projection": {}},
        )

    def _ensure_popover(self):
        """Create the NSPopover and its content if not already created."""
        if self.popover is not None:
            return

        _debug("Creating NSPopover")

        initial_height = self.PANEL_MIN_HEIGHT
        if self._last_data:
            initial_height = self._preferred_panel_height(*self._last_data)

        # Create message handler for JS -> Python bridge
        self._message_handler = ActionMessageHandler.alloc().initWithController_callbacks_(
            self, self._callbacks
        )

        # Create the view controller with webview
        self._view_controller = DashboardViewController.alloc().initWithSize_messageHandler_(
            (self.PANEL_WIDTH, initial_height), self._message_handler
        )

        # Create NSPopover with dark appearance to prevent white flash
        self.popover = NSPopover.alloc().init()
        self.popover.setContentSize_((self.PANEL_WIDTH, initial_height))
        self.popover.setBehavior_(1)  # NSPopoverBehaviorTransient - close on click outside
        self.popover.setAnimates_(True)
        dark_appearance = NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark")
        self.popover.setAppearance_(dark_appearance)
        self.popover.setContentViewController_(self._view_controller)

        # Force the view to load now so WKWebView is ready
        _ = self._view_controller.view()
        self._current_panel_height = initial_height

        # Pre-load HTML if we already have data (prevents white flash on first show)
        if self._last_data:
            html = self._generate_html(*self._last_data)
            self._view_controller.loadHTML_(html)

        _debug("NSPopover created")

    def toggle(self, status_item_button=None):
        """Toggle the popover. Show relative to the status item button."""
        self._ensure_popover()

        if self.popover.isShown():
            self.hide()
        else:
            self.show(status_item_button)

    def show(self, status_item_button=None):
        """Show the popover anchored to the status item button."""
        self._ensure_popover()
        _debug(f"show() called, button={status_item_button}")

        # Load data into webview
        if self._last_data:
            self._resize_for_data(*self._last_data)
            html = self._generate_html(*self._last_data)
            self._view_controller.loadHTML_(html)
            _debug("HTML loaded")

        if status_item_button:
            # Show relative to the status bar button - macOS handles positioning
            self.popover.showRelativeToRect_ofView_preferredEdge_(
                status_item_button.bounds(),
                status_item_button,
                NSMinYEdge
            )
            _debug(f"Popover shown relative to button, isShown={self.popover.isShown()}")
        else:
            _debug("No button provided, cannot show popover")

        # Set up global event monitor for outside clicks (backup for transient behavior)
        self._start_event_monitors()

    def hide(self):
        """Hide the popover."""
        if self.popover and self.popover.isShown():
            self.popover.performClose_(None)
        self._stop_event_monitors()

    def _start_event_monitors(self):
        """Monitor for clicks outside to close the popover."""
        self._stop_event_monitors()

        mask = (1 << 1) | (1 << 3)  # NSLeftMouseDownMask | NSRightMouseDownMask

        def _global_handler(event):
            if self.popover and self.popover.isShown():
                self.hide()

        self._global_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask, _global_handler
        )

    def _stop_event_monitors(self):
        """Remove event monitors."""
        if self._global_monitor:
            NSEvent.removeMonitor_(self._global_monitor)
            self._global_monitor = None
        if self._local_monitor:
            NSEvent.removeMonitor_(self._local_monitor)
            self._local_monitor = None

    def update_data(self, daily, weekly, monthly):
        """Update the dashboard with new data."""
        self._last_data = (daily, weekly, monthly)
        self._resize_for_data(daily, weekly, monthly)
        if self.popover and self.popover.isShown():
            html = self._generate_html(daily, weekly, monthly)
            self._view_controller.loadHTML_(html)

    def _resize_for_data(self, daily, weekly, monthly):
        """Resize the popover to fit current content, within min/max bounds."""
        if not self.popover or not self._view_controller:
            return

        height = self._preferred_panel_height(daily, weekly, monthly)
        self._apply_panel_height(height, reason="estimate")

    def set_measured_height(self, height):
        """Apply a DOM-measured height reported by the web view."""
        if not self.popover or not self._view_controller:
            return

        height = max(self.PANEL_MIN_HEIGHT, min(self.PANEL_MAX_HEIGHT, int(height)))
        if self._last_measured_height == height and self._current_panel_height == height:
            return

        self._last_measured_height = height
        self._apply_panel_height(height, reason="measured")

    def _apply_panel_height(self, height, reason):
        """Resize the native popover and hosted web view."""
        height = max(self.PANEL_MIN_HEIGHT, min(self.PANEL_MAX_HEIGHT, int(height)))
        if self._current_panel_height == height:
            return

        size = (self.PANEL_WIDTH, height)
        self._view_controller.resizeTo_(size)
        self.popover.setContentSize_(size)
        self._current_panel_height = height
        _debug(f"Applied {reason} popover height {height}")

    def _preferred_panel_height(self, daily, weekly, monthly):
        """Use the last measured DOM height when available, else fall back to an estimate."""
        if self._last_measured_height is not None:
            return self._last_measured_height
        return self._estimate_panel_height(daily, weekly, monthly)

    def _get_section_states(self):
        """Load persisted expanded/collapsed state for dashboard sections."""
        states = DEFAULT_PREFERENCES['dashboard_sections'].copy()
        current = load_preferences().get('dashboard_sections', {})
        if isinstance(current, dict):
            states.update({k: bool(v) for k, v in current.items() if k in states})
        return states

    def set_section_expanded(self, section_key, expanded):
        """Persist section state changed inside the dashboard."""
        valid_sections = set(DEFAULT_PREFERENCES['dashboard_sections'])
        if section_key not in valid_sections:
            return

        prefs = load_preferences()
        current = prefs.get('dashboard_sections', {})
        if not isinstance(current, dict):
            current = DEFAULT_PREFERENCES['dashboard_sections'].copy()
        else:
            current = {
                **DEFAULT_PREFERENCES['dashboard_sections'],
                **current,
            }
        current[section_key] = bool(expanded)
        prefs['dashboard_sections'] = current
        save_preferences(prefs)

    def _estimate_panel_height(self, daily, weekly, monthly):
        """Estimate the height needed for the current dashboard content."""
        prefs = load_preferences()
        project_targets = prefs.get('project_targets', {})
        projects_config = prefs.get('projects', {})
        section_states = self._get_section_states()

        # Base chrome: top/bottom paddings, footer actions/update line, scroll area padding.
        height = 170

        # Three section headers are always visible.
        height += 3 * 46

        if section_states.get('today', True):
            daily_rows = daily.get('all_projects', daily.get('projects', []))
            height += max(1, len(daily_rows)) * 28

        if section_states.get('week', True):
            weekly_rows = weekly.get('all_projects', weekly.get('projects', []))
            height += max(1, len(weekly_rows)) * 28

        monthly_rows = monthly.get('all_projects', monthly.get('projects', []))
        if section_states.get('month', True):
            for project in monthly_rows:
                if project['hours'] <= 0:
                    continue

                name = project['name']
                is_billable = project.get('billable', True)
                proj_def = projects_config.get(name, {})
                billing_type = proj_def.get('billing_type')
                hour_tracking = proj_def.get('hour_tracking')
                has_target = name in project_targets
                has_def = name in projects_config

                if not (is_billable or has_target or has_def):
                    continue

                target = project_targets.get(name)
                if not target and billing_type == 'fixed_monthly' and hour_tracking in ('required', 'soft'):
                    target = proj_def.get('target_hours')
                elif not target and billing_type == 'hourly_with_cap':
                    target = proj_def.get('cap_hours')

                if target:
                    height += 52  # header + progress bar + status

                    if (billing_type == 'fixed_monthly' and hour_tracking == 'required') or \
                       billing_type == 'hourly_with_cap':
                        carryover_balance, _ = get_previous_month_balance(name)
                        if carryover_balance != 0.0:
                            height += 14

                    if billing_type == 'hourly_with_cap' and project.get('cap_fill_date'):
                        height += 14
                else:
                    height += 32

        projection = monthly.get('projection', {})
        if projection and projection.get('worked_days', 0) > 0:
            height += 44
            if projection.get('fixed_monthly_total', 0) > 0:
                height += 14
            if projection.get('daily_average', 0) > 0:
                height += 14
            if projection.get('vacation_days', 0) > 0:
                height += 14

        return max(self.PANEL_MIN_HEIGHT, min(self.PANEL_MAX_HEIGHT, int(height)))

    def _generate_html(self, daily, weekly, monthly):
        """Generate the rich dashboard HTML."""
        prefs = load_preferences()
        project_targets = prefs.get('project_targets', {})
        projects_config = prefs.get('projects', {})
        section_states = self._get_section_states()

        # === TODAY section ===
        today_total = daily.get('total', 0)
        all_daily_projects = daily.get('all_projects', daily.get('projects', []))

        daily_rows = ""
        for p in all_daily_projects:
            if p.get('billable', True):
                daily_rows += f"""
                <div class="project-row">
                    <span class="project-name">{_esc(p['name'])}</span>
                    <span class="project-value">
                        <span class="money">${p['earnings']:.0f}</span>
                        <span class="hours">({p['hours']:.1f}h)</span>
                    </span>
                </div>"""
            else:
                daily_rows += f"""
                <div class="project-row">
                    <span class="project-name">{_esc(p['name'])}</span>
                    <span class="project-value">
                        <span class="hours">{p['hours']:.1f}h</span>
                    </span>
                </div>"""

        if not all_daily_projects:
            daily_rows = '<div class="empty-state">No time logged today</div>'

        # === THIS WEEK section ===
        weekly_total = weekly.get('total', 0)
        all_weekly_projects = weekly.get('all_projects', weekly.get('projects', []))

        weekly_rows = ""
        for p in all_weekly_projects:
            if p.get('billable', True):
                weekly_rows += f"""
                <div class="project-row">
                    <span class="project-name">{_esc(p['name'])}</span>
                    <span class="project-value">
                        <span class="money">${p['earnings']:.0f}</span>
                        <span class="hours">({p['hours']:.1f}h)</span>
                    </span>
                </div>"""
            else:
                weekly_rows += f"""
                <div class="project-row">
                    <span class="project-name">{_esc(p['name'])}</span>
                    <span class="project-value">
                        <span class="hours">{p['hours']:.1f}h</span>
                    </span>
                </div>"""

        if not all_weekly_projects:
            weekly_rows = '<div class="empty-state">No time logged this week</div>'

        # === THIS MONTH section ===
        month_total = monthly.get('total', 0)
        all_monthly_projects = monthly.get('all_projects', monthly.get('projects', []))

        # Collect projects into two groups: tracked (with targets) and untracked
        tracked_projects = []   # (percentage, html)
        untracked_projects = [] # (html,)

        for p in all_monthly_projects:
            if p['hours'] <= 0:
                continue

            name = p['name']
            is_billable = p.get('billable', True)
            proj_def = projects_config.get(name, {})
            billing_type = proj_def.get('billing_type')
            hour_tracking = proj_def.get('hour_tracking')
            has_target = name in project_targets
            has_def = name in projects_config

            if not (is_billable or has_target or has_def):
                continue

            # Determine target
            carryover_balance = 0.0
            prev_month_label = ""
            if (billing_type == 'fixed_monthly' and hour_tracking == 'required') or \
               billing_type == 'hourly_with_cap':
                carryover_balance, prev_month_label = get_previous_month_balance(name)

            target = project_targets.get(name)
            if not target and billing_type == 'fixed_monthly' and \
               hour_tracking in ('required', 'soft'):
                target = proj_def.get('target_hours')
            elif not target and billing_type == 'hourly_with_cap':
                target = proj_def.get('cap_hours')

            if target:
                effective_target = max(0.1, target - carryover_balance)
                percentage = (p['hours'] / effective_target) * 100
                clamped_pct = min(percentage, 100)

                # Pacing: compare hours % vs calendar %
                from datetime import date
                import calendar as cal_mod
                today = date.today()
                days_in_month = cal_mod.monthrange(today.year, today.month)[1]
                calendar_pct = (today.day / days_in_month) * 100
                pace_ratio = percentage / max(calendar_pct, 0.1)

                if percentage > 105:
                    bar_color = "#f85149"
                    status_text = "Over target"
                elif percentage >= 100:
                    bar_color = "#f0883e"
                    status_text = "Complete"
                elif percentage >= 95:
                    bar_color = "#f0883e"
                    status_text = "Almost there"
                elif pace_ratio >= 1.5:
                    bar_color = "#3fb950"
                    status_text = "Well ahead \u2014 ease off or bank hours"
                elif pace_ratio >= 1.15:
                    bar_color = "#3fb950"
                    status_text = "Ahead of pace"
                elif pace_ratio >= 0.85:
                    bar_color = "#3fb950"
                    status_text = "On pace"
                elif pace_ratio >= 0.5:
                    bar_color = "#58a6ff"
                    status_text = "Behind \u2014 ramp up to stay on track"
                else:
                    bar_color = "#58a6ff"
                    status_text = "Way behind \u2014 needs attention"

                carryover_html = ""
                if carryover_balance != 0.0:
                    sign = "+" if carryover_balance > 0 else ""
                    carryover_html = f'<div class="carryover">↳ {sign}{carryover_balance:.1f}h carryover from {_esc(prev_month_label)}</div>'

                cap_html = ""
                cap_fill_date = p.get('cap_fill_date')
                if billing_type == 'hourly_with_cap' and cap_fill_date:
                    from datetime import datetime
                    fill_dt = datetime.strptime(cap_fill_date, '%Y-%m-%d')
                    fill_label = fill_dt.strftime('%b %-d')
                    cap_html = f'<div class="carryover">↳ capped as of {fill_label}</div>'

                # Calendar progress marker already computed above

                row_html = f"""
                <div class="monthly-project">
                    <div class="monthly-project-header">
                        <span class="project-name">{_esc(name)}: {p['hours']:.1f}h / {effective_target:.1f}h ({percentage:.0f}%)</span>
                    </div>
                    <div class="progress-track">
                        <div class="progress-fill-clip"><div class="progress-fill" style="width: {clamped_pct}%; background: {bar_color};"></div></div>
                        <div class="calendar-marker" style="left: {calendar_pct:.1f}%;"></div>
                    </div>
                    <div class="status-text">{status_text}</div>
                    {carryover_html}
                    {cap_html}
                </div>"""
                tracked_projects.append((percentage, row_html))
            elif is_billable:
                row_html = f"""
                <div class="monthly-project">
                    <div class="monthly-project-header">
                        <span class="project-name">{_esc(name)}: {p['hours']:.1f}h</span>
                        <span class="money">${p['earnings']:.0f}</span>
                    </div>
                </div>"""
                untracked_projects.append(row_html)

        # Sort tracked projects: least complete first
        tracked_projects.sort(key=lambda x: x[0])

        # Combine: tracked (sorted by completion%) then untracked
        monthly_rows = "".join(html for _, html in tracked_projects)
        monthly_rows += "".join(untracked_projects)

        # === PROJECTION section ===
        projection = monthly.get('projection', {})
        projection_html = ""
        if projection and projection.get('worked_days', 0) > 0:
            projected = projection['projected_earnings']
            worked = projection['worked_days']
            workable = projection['workable_days']
            vacation = projection.get('vacation_days', 0)
            daily_avg = projection.get('daily_average', 0)
            fixed_total = projection.get('fixed_monthly_total', 0)
            projected_variable = projection.get('projected_variable', 0)

            projection_details = ""
            if fixed_total > 0:
                projection_details += f'<div class="projection-detail">${fixed_total:,.0f} fixed + ${projected_variable:,.0f} projected hourly</div>'
            if daily_avg > 0:
                projection_details += f'<div class="projection-detail">Hourly daily avg: ${daily_avg:,.0f}</div>'
            if vacation > 0:
                projection_details += f'<div class="projection-detail">{vacation} days off excluded</div>'

            projection_html = f"""
            <div class="section projection-section">
                <div class="projection-line">
                    Month Projection: <span class="money">${projected:,.0f}</span> · {worked}/{workable} workable days
                </div>
                {projection_details}
            </div>"""

        today_section = self._render_section(
            "today", "Today", f"${today_total:,.2f}", daily_rows, section_states.get('today', True)
        )
        week_section = self._render_section(
            "week", "This Week", f"${weekly_total:,.2f}", weekly_rows, section_states.get('week', True)
        )
        month_section = self._render_section(
            "month", "This Month", f"${month_total:,.2f}", monthly_rows, section_states.get('month', True)
        )
        update_time_text = ""
        if self._last_updated_at is not None:
            update_time_text = f"Last updated: {self._last_updated_at.strftime('%I:%M %p')}"
        rate_limited_html = ""
        if self._is_rate_limited:
            rate_limited_html = """
            <div class="status-banner warning">
                Toggl rate limit reached. Showing cached data.
            </div>"""
        error_html = ""
        if self._error_message:
            error_html = f"""
            <div class="status-banner error">
                <div class="status-copy">
                    <div class="status-title">Refresh failed. Showing last successful data.</div>
                    <div class="status-detail">{_esc(self._error_message)}</div>
                </div>
                <button class="inline-action-btn" onclick="postAction('refresh')">Retry</button>
            </div>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}

html, body {{
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
    background: #1c1c1e;
    color: #c9d1d9;
    -webkit-user-select: none;
    cursor: default;
    overflow-x: hidden;
}}

.wrapper {{
    padding: 10px 12px 12px;
}}

.section-list {{
    display: flex;
    flex-direction: column;
    gap: 10px;
}}

.footer {{
    margin-top: 12px;
    padding-top: 8px;
    border-top: 1px solid rgba(255,255,255,0.06);
}}

.section {{
    border-radius: 12px;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.05);
    overflow: hidden;
}}

.section-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 12px;
    min-height: 46px;
}}

.section-header.clickable {{
    cursor: pointer;
    transition: background 0.15s ease;
}}

.section-header.clickable:hover {{
    background: rgba(255,255,255,0.03);
}}

.section-meta {{
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
}}

.section-body {{
    padding: 0 12px 10px;
    border-top: 1px solid rgba(255,255,255,0.05);
}}

.section.collapsed .section-body {{
    display: none;
}}

.section.collapsed {{
    background: rgba(255,255,255,0.02);
}}

.section.collapsed .section-header {{
    min-height: 42px;
    padding-top: 8px;
    padding-bottom: 8px;
}}

.section-title {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.8px;
    color: #8b949e;
    text-transform: uppercase;
}}

.section-total {{
    font-size: 18px;
    font-weight: 700;
    color: #3fb950;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}}

.section-chevron {{
    font-size: 13px;
    color: #8b949e;
    margin-left: 10px;
    flex-shrink: 0;
}}

.project-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 6px;
    margin: 1px 0;
    border-radius: 6px;
}}

.project-row:hover {{
    background: rgba(255,255,255,0.04);
}}

.project-name {{
    font-size: 13px;
    color: #b0b8c1;
}}

.project-value {{
    font-size: 13px;
    font-variant-numeric: tabular-nums;
}}

.money {{
    color: #3fb950;
    font-weight: 600;
}}

.hours {{
    color: #8b949e;
    margin-left: 4px;
}}

.monthly-project {{
    padding: 7px 8px;
    margin: 3px 0;
    border-radius: 8px;
    background: rgba(255,255,255,0.03);
}}

.monthly-project-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 5px;
}}

.monthly-project-header .project-name {{
    font-size: 12.5px;
    color: #9aa5b0;
}}

.progress-track {{
    height: 6px;
    background: rgba(255,255,255,0.08);
    border-radius: 3px;
    margin: 5px 0;
    position: relative;
}}

.progress-fill-clip {{
    position: absolute;
    inset: 0;
    border-radius: 3px;
    overflow: hidden;
}}

.progress-fill {{
    height: 100%;
    border-radius: 3px;
    transition: width 0.6s ease;
}}

.calendar-marker {{
    position: absolute;
    top: -2px;
    width: 2px;
    height: calc(100% + 4px);
    background: rgba(255,255,255,0.85);
    border-radius: 1px;
    z-index: 1;
}}

.status-text {{
    font-size: 11px;
    color: #6e7681;
    margin-top: 2px;
}}

.carryover {{
    font-size: 11px;
    color: #6e7681;
    margin-top: 2px;
}}

.projection-section {{
    padding: 8px;
    border-radius: 8px;
    background: rgba(63, 185, 80, 0.05);
    border: 1px solid rgba(63, 185, 80, 0.1);
    margin-bottom: 8px;
}}

.projection-line {{
    font-size: 13px;
    color: #b0b8c1;
    font-variant-numeric: tabular-nums;
}}

.projection-detail {{
    font-size: 11px;
    color: #6e7681;
    margin-top: 3px;
}}

.status-stack {{
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 8px;
}}

.status-banner {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 9px 10px;
    border-radius: 8px;
    border: 1px solid rgba(255,255,255,0.08);
}}

.status-banner.warning {{
    background: rgba(255, 184, 108, 0.08);
    border-color: rgba(255, 184, 108, 0.16);
    color: #d9b26f;
}}

.status-banner.error {{
    background: rgba(248, 81, 73, 0.08);
    border-color: rgba(248, 81, 73, 0.16);
}}

.status-copy {{
    min-width: 0;
}}

.status-title {{
    font-size: 12px;
    color: #d0d7de;
}}

.status-detail {{
    font-size: 10px;
    color: #8b949e;
    margin-top: 2px;
    word-break: break-word;
}}

.divider {{
    height: 1px;
    background: rgba(255,255,255,0.06);
    margin: 10px 0;
}}

.actions {{
    display: flex;
    gap: 6px;
    margin-top: 8px;
}}

.refresh-group {{
    position: relative;
    flex: 1;
}}

.refresh-group.open .refresh-menu {{
    display: flex;
}}

.split-action {{
    display: flex;
    width: 100%;
}}

.action-btn {{
    flex: 1;
    padding: 7px 10px;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 8px;
    background: rgba(255,255,255,0.04);
    color: #8b949e;
    font-size: 11px;
    font-family: inherit;
    cursor: pointer;
    text-align: center;
    transition: all 0.15s;
    -webkit-appearance: none;
}}

.refresh-toggle {{
    width: 100%;
    padding: 0;
    overflow: hidden;
}}

.refresh-primary {{
    flex: 1 1 auto;
    min-width: 0;
    padding: 7px 10px;
    border: 0;
    border-right: 1px solid rgba(255,255,255,0.1);
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
    -webkit-appearance: none;
}}

.refresh-primary:hover,
.refresh-arrow:hover {{
    background: rgba(255,255,255,0.08);
}}

.refresh-arrow {{
    flex: 0 0 10%;
    min-width: 28px;
    max-width: 36px;
    padding: 7px 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
    -webkit-appearance: none;
}}

.action-btn:hover {{
    background: rgba(255,255,255,0.08);
    color: #c9d1d9;
    border-color: rgba(255,255,255,0.18);
}}

.action-btn.danger:hover {{
    background: rgba(248, 81, 73, 0.1);
    color: #f85149;
    border-color: rgba(248, 81, 73, 0.3);
}}

.refresh-menu {{
    position: absolute;
    left: 0;
    right: 0;
    bottom: calc(100% + 6px);
    display: none;
    flex-direction: column;
    gap: 4px;
    padding: 6px;
    border-radius: 10px;
    background: #202225;
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 12px 24px rgba(0,0,0,0.28);
    z-index: 20;
}}

.refresh-option {{
    width: 100%;
    padding: 7px 8px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: #c9d1d9;
    font-size: 11px;
    text-align: left;
    cursor: pointer;
    -webkit-appearance: none;
}}

.refresh-option:hover {{
    background: rgba(255,255,255,0.08);
}}

.inline-action-btn {{
    flex-shrink: 0;
    padding: 6px 9px;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    background: rgba(255,255,255,0.04);
    color: #c9d1d9;
    font-size: 11px;
    cursor: pointer;
    -webkit-appearance: none;
}}

.inline-action-btn:hover {{
    background: rgba(255,255,255,0.08);
}}

.empty-state {{
    font-size: 12px;
    color: #6e7681;
    padding: 8px;
    font-style: italic;
}}

.update-time {{
    font-size: 10px;
    color: #484f58;
    text-align: center;
    margin-top: 6px;
}}
</style>
</head>
<body>
<div class="wrapper">
    <div class="section-list">
        {today_section}
        {week_section}
        {month_section}
    </div>

    <div class="footer">
        <div class="status-stack">
            {error_html}
            {rate_limited_html}
        </div>
        {projection_html}

        <div class="actions">
            <div class="refresh-group" id="refreshGroup">
                <div class="action-btn refresh-toggle split-action">
                    <button class="refresh-primary" onclick="postAction('refresh')">Refresh</button>
                    <button class="refresh-arrow" onclick="toggleRefreshMenu(event)" aria-label="Refresh options">▴</button>
                </div>
                <div class="refresh-menu" id="refreshMenu">
                    <button class="refresh-option" onclick="runRefreshAction('refresh')">Refresh Data</button>
                    <button class="refresh-option" onclick="runRefreshAction('refresh_projects')">Refresh Projects</button>
                </div>
            </div>
            <button class="action-btn" onclick="window.webkit.messageHandlers.action.postMessage('settings')">Settings</button>
            <button class="action-btn" onclick="window.webkit.messageHandlers.action.postMessage('update_app')">Update</button>
            <button class="action-btn danger" onclick="window.webkit.messageHandlers.action.postMessage('quit')">Quit</button>
        </div>

        <div class="update-time">{_esc(update_time_text)}</div>
    </div>
</div>

<script>
    var heightReportQueued = false;
    var heightObserver = null;

    function postAction(message) {{
        var handler = window.webkit &&
            window.webkit.messageHandlers &&
            window.webkit.messageHandlers.action;
        if (handler && typeof handler.postMessage === 'function') {{
            handler.postMessage(message);
        }}
    }}

    function scheduleReportHeight() {{
        if (heightReportQueued) {{
            return;
        }}
        heightReportQueued = true;
        requestAnimationFrame(function() {{
            heightReportQueued = false;
            reportHeight();
        }});
    }}

    function closeRefreshMenu() {{
        var group = document.getElementById('refreshGroup');
        if (group) {{
            group.classList.remove('open');
        }}
    }}

    function toggleRefreshMenu(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var group = document.getElementById('refreshGroup');
        if (!group) return;
        group.classList.toggle('open');
    }}

    function runRefreshAction(action) {{
        closeRefreshMenu();
        postAction(action);
    }}

    function toggleSection(sectionKey) {{
        var section = document.querySelector('[data-section="' + sectionKey + '"]');
        if (!section) return;
        var willExpand = section.getAttribute('data-expanded') !== 'true';
        section.setAttribute('data-expanded', willExpand ? 'true' : 'false');
        section.classList.toggle('collapsed', !willExpand);

        var chevron = section.querySelector('.section-chevron');
        if (chevron) {{
            chevron.textContent = willExpand ? '▾' : '▸';
        }}

        postAction(
            'toggle:' + sectionKey + ':' + (willExpand ? 'expanded' : 'collapsed')
        );
        scheduleReportHeight();
    }}

    function handleSectionKey(event, sectionKey) {{
        if (event.key === 'Enter' || event.key === ' ') {{
            event.preventDefault();
            toggleSection(sectionKey);
        }}
    }}

    function reportHeight() {{
        var wrapper = document.querySelector('.wrapper');
        if (!wrapper || !document.body || !document.documentElement) return;

        var totalHeight = Math.ceil(Math.max(
            wrapper.getBoundingClientRect().height,
            document.body.scrollHeight,
            document.documentElement.scrollHeight
        ));
        postAction('height:' + totalHeight);
    }}

    function startHeightObserver() {{
        if (heightObserver || typeof ResizeObserver !== 'function') {{
            return;
        }}

        var wrapper = document.querySelector('.wrapper');
        heightObserver = new ResizeObserver(function() {{
            scheduleReportHeight();
        }});

        heightObserver.observe(document.documentElement);
        heightObserver.observe(document.body);
        if (wrapper) {{
            heightObserver.observe(wrapper);
        }}
    }}

    document.addEventListener('DOMContentLoaded', function() {{
        startHeightObserver();
        scheduleReportHeight();
    }});
    document.addEventListener('click', function() {{
        closeRefreshMenu();
    }});
    document.addEventListener('keydown', function(event) {{
        if (event.key === 'Escape') {{
            closeRefreshMenu();
        }}
    }});
    window.addEventListener('load', function() {{
        scheduleReportHeight();
    }});
    window.addEventListener('resize', scheduleReportHeight);
</script>
</body>
</html>"""

        return html

    def _render_section(self, section_key, title, total, body_html, expanded):
        """Render a dashboard section with persisted collapse state."""
        collapsed_class = "" if expanded else " collapsed"
        chevron = "▾" if expanded else "▸"
        return f"""
        <div class="section{collapsed_class}" data-section="{section_key}" data-expanded="{str(expanded).lower()}">
            <div class="section-header clickable" role="button" tabindex="0"
                 onclick="toggleSection('{section_key}')"
                 onkeydown="handleSectionKey(event, '{section_key}')">
                <div class="section-meta">
                    <span class="section-title">{_esc(title)}</span>
                    <span class="section-total">{_esc(total)}</span>
                </div>
                <span class="section-chevron">{chevron}</span>
            </div>
            <div class="section-body">
                {body_html}
            </div>
        </div>"""


def _esc(text):
    """Escape HTML entities."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
