"""Rich dashboard popover for Freelance Tracker menu bar app.

Uses NSPopover with a WKWebView to show a styled dashboard when
the status bar item is clicked - similar to how Claude Usage Tracker works.
"""

import objc
from AppKit import (
    NSMakeRect, NSEvent, NSViewController, NSView, NSColor, NSAppearance,
)
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController
from preferences import load_preferences
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

    def initWithCallbacks_(self, callbacks):
        self = objc.super(ActionMessageHandler, self).init()
        if self is None:
            return None
        self._callbacks = callbacks
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        action = str(message.body())
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
        self.webview.setAutoresizingMask_(1 | 2 | 4 | 8 | 16 | 32)  # flex all
        if hasattr(self.webview, 'setInspectable_'):
            self.webview.setInspectable_(True)

        container.addSubview_(self.webview)
        self.setView_(container)

    def loadHTML_(self, html):
        if self.webview:
            self.webview.loadHTMLString_baseURL_(html, None)


class DashboardPanelController:
    """
    Manages the dashboard popover using NSPopover.
    Shows earnings data with styled HTML/CSS in a WKWebView.
    """

    _instance = None
    PANEL_WIDTH = 420
    PANEL_HEIGHT = 620

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
        self._message_handler = None

    def set_callbacks(self, callbacks):
        """Set action callbacks: {'refresh': fn, 'preferences': fn, 'quit': fn}"""
        self._callbacks = callbacks

    def _ensure_popover(self):
        """Create the NSPopover and its content if not already created."""
        if self.popover is not None:
            return

        _debug("Creating NSPopover")

        # Create message handler for JS -> Python bridge
        self._message_handler = ActionMessageHandler.alloc().initWithCallbacks_(self._callbacks)

        # Create the view controller with webview
        self._view_controller = DashboardViewController.alloc().initWithSize_messageHandler_(
            (self.PANEL_WIDTH, self.PANEL_HEIGHT), self._message_handler
        )

        # Create NSPopover with dark appearance to prevent white flash
        self.popover = NSPopover.alloc().init()
        self.popover.setContentSize_((self.PANEL_WIDTH, self.PANEL_HEIGHT))
        self.popover.setBehavior_(1)  # NSPopoverBehaviorTransient - close on click outside
        self.popover.setAnimates_(True)
        dark_appearance = NSAppearance.appearanceNamed_("NSAppearanceNameVibrantDark")
        self.popover.setAppearance_(dark_appearance)
        self.popover.setContentViewController_(self._view_controller)

        # Force the view to load now so WKWebView is ready
        _ = self._view_controller.view()

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
        if self.popover and self.popover.isShown():
            html = self._generate_html(daily, weekly, monthly)
            self._view_controller.loadHTML_(html)

    def _generate_html(self, daily, weekly, monthly):
        """Generate the rich dashboard HTML."""
        prefs = load_preferences()
        project_targets = prefs.get('project_targets', {})
        projects_config = prefs.get('projects', {})

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

        # === WEEKLY ===
        weekly_total = weekly.get('total', 0)

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}

html, body {{
    height: 100%;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
    background: #1c1c1e;
    color: #c9d1d9;
    -webkit-user-select: none;
    cursor: default;
    overflow: hidden;
}}

.wrapper {{
    display: flex;
    flex-direction: column;
    height: 100%;
}}

.scroll-area {{
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 18px 20px 0;
    -webkit-overflow-scrolling: touch;
}}

.scroll-area::-webkit-scrollbar {{
    width: 6px;
}}
.scroll-area::-webkit-scrollbar-track {{
    background: transparent;
}}
.scroll-area::-webkit-scrollbar-thumb {{
    background: rgba(255,255,255,0.12);
    border-radius: 3px;
}}
.scroll-area::-webkit-scrollbar-thumb:hover {{
    background: rgba(255,255,255,0.2);
}}

.footer {{
    flex-shrink: 0;
    padding: 10px 20px 14px;
    background: #1c1c1e;
    border-top: 1px solid rgba(255,255,255,0.06);
}}

.section {{
    margin-bottom: 14px;
}}

.section-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 8px;
    padding-bottom: 5px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}}

.section-title {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.8px;
    color: #8b949e;
    text-transform: uppercase;
}}

.section-total {{
    font-size: 22px;
    font-weight: 700;
    color: #3fb950;
    font-variant-numeric: tabular-nums;
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

.weekly-line {{
    font-size: 12px;
    color: #8b949e;
    padding: 4px 6px 0;
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

.divider {{
    height: 1px;
    background: rgba(255,255,255,0.06);
    margin: 10px 0;
}}

.actions {{
    display: flex;
    gap: 6px;
    margin-top: auto;
    padding-top: 10px;
    border-top: 1px solid rgba(255,255,255,0.06);
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
    margin-top: 8px;
}}
</style>
</head>
<body>
<div class="wrapper">
    <div class="scroll-area">
        <div class="section">
            <div class="section-header">
                <span class="section-title">Today</span>
                <span class="section-total">${today_total:,.2f}</span>
            </div>
            {daily_rows}
            <div class="weekly-line">
                This week: <span class="money">${weekly_total:,.2f}</span>
            </div>
        </div>

        <div class="divider"></div>

        <div class="section">
            <div class="section-header">
                <span class="section-title">This Month</span>
                <span class="section-total">${month_total:,.2f}</span>
            </div>
            {monthly_rows}
        </div>
    </div>

    <div class="footer">
        {projection_html}

        <div class="actions">
            <button class="action-btn" onclick="window.webkit.messageHandlers.action.postMessage('refresh')">↻ Refresh</button>
            <button class="action-btn" onclick="window.webkit.messageHandlers.action.postMessage('preferences')">⚙ Preferences</button>
            <button class="action-btn danger" onclick="window.webkit.messageHandlers.action.postMessage('quit')">Quit</button>
        </div>

        <div class="update-time" id="updateTime"></div>
    </div>
</div>

<script>
    var now = new Date();
    var h = now.getHours();
    var m = now.getMinutes();
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    m = m < 10 ? '0' + m : m;
    document.getElementById('updateTime').textContent = 'Updated ' + h + ':' + m + ' ' + ampm;
</script>
</body>
</html>"""

        return html


def _esc(text):
    """Escape HTML entities."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
