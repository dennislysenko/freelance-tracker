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
from toggl_data import get_lbd_billing_cycle_bounds, get_lbd_cycle_progress

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
        if action.startswith("export_csv:"):
            parts = action.split(":")
            # export_csv:{project_id}:{start_iso}:{end_iso}
            if len(parts) >= 4:
                project_id = parts[1]
                start_iso = parts[2]
                end_iso = parts[3]
                callback = self._callbacks.get("export_csv")
                if callback:
                    callback(project_id, start_iso, end_iso)
            return
        if action.startswith("stripe_invoice_prepare:"):
            parts = action.split(":")
            if len(parts) >= 4:
                project_id = parts[1]
                start_iso = parts[2]
                end_iso = parts[3]
                callback = self._callbacks.get("stripe_invoice_prepare")
                if callback:
                    callback(project_id, start_iso, end_iso)
            return
        if action.startswith("stripe_invoice_create:"):
            parts = action.split(":")
            if len(parts) >= 5:
                project_id = parts[1]
                start_iso = parts[2]
                end_iso = parts[3]
                customer_id = parts[4]
                callback = self._callbacks.get("stripe_invoice_create")
                if callback:
                    callback(project_id, start_iso, end_iso, customer_id)
            return
        if action.startswith("open_upwork_diary:"):
            parts = action.split(":")
            if len(parts) >= 3:
                project_id = parts[1]
                entry_date_iso = parts[2]
                callback = self._callbacks.get("open_upwork_diary")
                if callback:
                    callback(project_id, entry_date_iso)
            return
        if action.startswith("save_upwork_contract:"):
            parts = action.split(":", 3)
            if len(parts) >= 4:
                project_id = parts[1]
                contract_id = parts[2]
                entry_date_iso = parts[3]
                callback = self._callbacks.get("save_upwork_contract")
                if callback:
                    callback(project_id, contract_id, entry_date_iso)
            return
        if action.startswith("copy_text:"):
            encoded_text = action.split(":", 1)[1]
            callback = self._callbacks.get("copy_text")
            if callback:
                callback(encoded_text)
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
        self._exportable_projects = []
        self._stripe_invoice_state = None

    def set_exportable_projects(self, projects):
        """List of project capability dicts used by dashboard billing actions."""
        self._exportable_projects = list(projects or [])

    def set_stripe_invoice_state(self, state):
        """Store the active Stripe invoice workflow state for dashboard rendering."""
        self._stripe_invoice_state = dict(state) if state else None

    def get_stripe_invoice_state(self):
        """Return the current Stripe invoice workflow state."""
        return dict(self._stripe_invoice_state) if self._stripe_invoice_state else None

    def clear_stripe_invoice_state(self):
        """Clear any active Stripe invoice workflow state."""
        self._stripe_invoice_state = None

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

    @staticmethod
    def _resolve_monthly_target(name, project_targets, projects_config):
        """Return the configured monthly target/cap for a project, if any."""
        proj_def = projects_config.get(name, {})
        billing_type = proj_def.get('billing_type')
        hour_tracking = proj_def.get('hour_tracking')

        target = project_targets.get(name)
        if not target and billing_type == 'fixed_monthly' and hour_tracking in ('required', 'soft'):
            target = proj_def.get('target_hours')
        elif not target and billing_type == 'hourly_with_cap':
            target = proj_def.get('cap_hours')

        return target, proj_def, billing_type, hour_tracking

    @classmethod
    def _monthly_projects_for_display(cls, monthly_projects, project_targets, projects_config):
        """
        Include zero-hour projects that still have an active monthly target/cap.
        This keeps the tracked-hours section visible even before the first entry lands.
        """
        display_projects = [dict(project) for project in monthly_projects]
        seen_names = {
            project.get("name")
            for project in display_projects
            if project.get("name")
        }

        candidate_names = []
        for name, target in project_targets.items():
            if isinstance(target, (int, float)) and target > 0 and name not in seen_names:
                candidate_names.append(name)
                seen_names.add(name)

        for name in projects_config:
            target, _proj_def, _billing_type, _hour_tracking = cls._resolve_monthly_target(
                name, project_targets, projects_config
            )
            if target and name not in seen_names:
                candidate_names.append(name)
                seen_names.add(name)

        for name in candidate_names:
            proj_def = projects_config.get(name, {})
            display_projects.append({
                "name": name,
                "hours": 0.0,
                "billable": proj_def.get("billing_type") in ("fixed_monthly", "hourly_with_cap"),
            })

        return display_projects

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

        monthly_rows = self._monthly_projects_for_display(
            monthly.get('all_projects', monthly.get('projects', [])),
            project_targets,
            projects_config,
        )
        if section_states.get('month', True):
            for project in monthly_rows:
                name = project['name']
                is_billable = project.get('billable', True)
                target, proj_def, billing_type, hour_tracking = self._resolve_monthly_target(
                    name, project_targets, projects_config
                )
                has_target = name in project_targets
                has_def = name in projects_config

                if not (is_billable or has_target or has_def):
                    continue

                if project['hours'] <= 0 and not target:
                    continue

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
            blocks = p.get('time_blocks') or []
            if p.get('billable', True):
                value_html = (
                    f'<span class="money">${p["earnings"]:.0f}</span>'
                    f'<span class="hours">({p["hours"]:.1f}h)</span>'
                )
            else:
                value_html = f'<span class="hours">{p["hours"]:.1f}h</span>'

            if blocks:
                project_key = _esc(p['name'])
                blocks_html = ""
                for block in blocks:
                    start_s = self._format_block_time(block["start"])
                    stop_s = self._format_block_time(block["stop"])
                    mins = max(1, round(block["duration"] / 60))
                    desc = block.get("description") or "(no description)"
                    blocks_html += f"""
                    <div class="time-block">
                        <span class="time-block-range">{start_s}\u2013{stop_s}</span>
                        <span class="time-block-duration">({mins}m)</span>
                        <button class="time-block-desc" type="button"
                                data-copy-text="{_esc(desc)}"
                                onclick="copyDescription(this)">{_esc(desc)}</button>
                    </div>"""
                daily_rows += f"""
                <div class="project-row expandable" data-project-key="{project_key}">
                    <div class="project-row-header" role="button" tabindex="0"
                         onclick="toggleProject(this)"
                         onkeydown="handleProjectKey(event, this)">
                        <span class="project-name">
                            <span class="row-chevron">\u25b8</span>{_esc(p['name'])}
                        </span>
                        <span class="project-value">{value_html}</span>
                    </div>
                    <div class="time-blocks">{blocks_html}
                    </div>
                </div>"""
            else:
                daily_rows += f"""
                <div class="project-row">
                    <span class="project-name">{_esc(p['name'])}</span>
                    <span class="project-value">{value_html}</span>
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
        all_monthly_projects = self._monthly_projects_for_display(
            monthly.get('all_projects', monthly.get('projects', [])),
            project_targets,
            projects_config,
        )

        # Collect projects into two groups: tracked (with targets) and untracked
        tracked_projects = []   # (percentage, html)
        untracked_projects = [] # (html,)

        for p in all_monthly_projects:
            name = p['name']
            is_billable = p.get('billable', True)
            target, proj_def, billing_type, hour_tracking = self._resolve_monthly_target(
                name, project_targets, projects_config
            )
            has_target = name in project_targets
            has_def = name in projects_config

            if not (is_billable or has_target or has_def):
                continue

            if p['hours'] <= 0 and not target:
                continue

            # Determine target
            carryover_balance = 0.0
            prev_month_label = ""
            if (billing_type == 'fixed_monthly' and hour_tracking == 'required') or \
               billing_type == 'hourly_with_cap':
                carryover_balance, prev_month_label = get_previous_month_balance(name)

            if target:
                effective_target = max(0.1, target - carryover_balance)
                percentage = (p['hours'] / effective_target) * 100
                clamped_pct = min(percentage, 100)

                # Pacing: compare hours % vs calendar progress, except capped LBD
                # projects which pace against their billing-cycle window.
                from datetime import date
                import calendar as cal_mod
                today = date.today()
                days_in_month = cal_mod.monthrange(today.year, today.month)[1]
                calendar_pct = (today.day / days_in_month) * 100
                elapsed_days = today.day
                period_inline = ""
                if billing_type == 'hourly_with_cap' and proj_def.get('last_billed_date'):
                    try:
                        calendar_pct = get_lbd_cycle_progress(proj_def['last_billed_date'], today=today)
                        period_start, period_end = get_lbd_billing_cycle_bounds(proj_def['last_billed_date'])
                        period_label = f"{period_start.month}/{period_start.day}-{period_end.month}/{period_end.day}"
                        period_inline = f'<span class="billing-period">{period_label}</span>'
                        if today >= period_start:
                            elapsed_days = (today - period_start).days + 1
                        else:
                            elapsed_days = 0
                    except ValueError:
                        pass
                pace_ratio = percentage / max(calendar_pct, 0.1)
                # Bayesian shrinkage toward neutral (1.0) early in the cycle:
                # the ratio is a noisy estimator when the denominator is tiny,
                # so blend it with 1.0 weighted by elapsed days (full signal by day 5).
                shrink_weight = min(max(elapsed_days, 0) / 5.0, 1.0)
                pace_ratio = pace_ratio * shrink_weight + 1.0 * (1 - shrink_weight)

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
                        {period_inline}
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

        # Compute preset date ranges (Python-side so JS doesn't have to)
        from datetime import date as _date, timedelta as _td
        from hours_csv_export import previous_month_range
        _today = _date.today()
        _this_week_start = _today - _td(days=_today.weekday())
        _this_week_end = _today
        _last_week_start = _today - _td(days=_today.weekday() + 7)
        _last_week_end = _last_week_start + _td(days=6)
        _last_month_start, _last_month_end = previous_month_range(_today)
        _ytd_start = _date(_today.year, 1, 1)
        _ytd_end = _today

        def _short_range(s, e):
            same_year = s.year == e.year
            if same_year:
                return f"{s.strftime('%b %-d')} \u2013 {e.strftime('%b %-d')}"
            return f"{s.strftime('%b %-d, %Y')} \u2013 {e.strftime('%b %-d, %Y')}"

        if self._exportable_projects:
            project_buttons = []
            invoice_project_buttons = []
            upwork_project_buttons = []
            for p in self._exportable_projects:
                lbd = p.get('last_billed_date') or ''
                lbd_attr = f' data-last-billed="{_esc(lbd)}"' if lbd else ''
                cap_fill_date = p.get('cap_fill_date') or ''
                cap_fill_attr = f' data-cap-fill-date="{_esc(cap_fill_date)}"' if cap_fill_date else ''
                stripe_customer_id = p.get('stripe_customer_id') or ''
                upwork_contract_id = p.get('upwork_contract_id') or ''
                if p.get('can_export'):
                    project_buttons.append(
                        f'<button class="export-option" '
                        f'data-project-id="{_esc(p["id"])}" '
                        f'data-project-name="{_esc(p["name"])}"'
                        f'{lbd_attr}{cap_fill_attr} '
                        f'onclick="selectExportProject(this)">{_esc(p["name"])}</button>'
                    )
                if p.get('can_invoice'):
                    stripe_customer_attr = (
                        f' data-stripe-customer="{_esc(stripe_customer_id)}"'
                        if stripe_customer_id else
                        ''
                    )
                    invoice_badge = (
                        '<span class="export-option-meta">Linked</span>'
                        if stripe_customer_id else
                        '<span class="export-option-meta muted">Needs customer</span>'
                    )
                    invoice_project_buttons.append(
                        f'<button class="export-option invoice-option" '
                        f'data-project-id="{_esc(p["id"])}" '
                        f'data-project-name="{_esc(p["name"])}"'
                        f'{lbd_attr}{cap_fill_attr}{stripe_customer_attr} '
                        f'onclick="selectInvoiceProject(this)">'
                        f'<span>{_esc(p["name"])}</span>{invoice_badge}</button>'
                    )
                upwork_badge = (
                    '<span class="export-option-meta">Linked</span>'
                    if upwork_contract_id else
                    '<span class="export-option-meta muted">Needs contract</span>'
                )
                upwork_project_buttons.append(
                    f'<button class="export-option invoice-option" '
                    f'data-project-id="{_esc(p["id"])}" '
                    f'data-project-name="{_esc(p["name"])}"'
                    f' data-upwork-contract-id="{_esc(upwork_contract_id)}"'
                    f'onclick="upworkSelectProject(this)">'
                    f'<span>{_esc(p["name"])}</span>{upwork_badge}</button>'
                )
            export_items_html = "".join(project_buttons) or '<div class="export-empty">No exportable projects</div>'
            invoice_items_html = "".join(invoice_project_buttons) or '<div class="export-empty">No invoiceable projects</div>'
            upwork_items_html = "".join(upwork_project_buttons) or '<div class="export-empty">No projects available</div>'
        else:
            export_items_html = '<div class="export-empty">No exportable projects</div>'
            invoice_items_html = '<div class="export-empty">No invoiceable projects</div>'
            upwork_items_html = '<div class="export-empty">No projects available</div>'

        export_menu_data_attrs = (
            f'data-today="{_today.isoformat()}" '
            f'data-this-week-start="{_this_week_start.isoformat()}" '
            f'data-this-week-end="{_this_week_end.isoformat()}" '
            f'data-last-week-start="{_last_week_start.isoformat()}" '
            f'data-last-week-end="{_last_week_end.isoformat()}" '
            f'data-last-month-start="{_last_month_start.isoformat()}" '
            f'data-last-month-end="{_last_month_end.isoformat()}" '
            f'data-ytd-start="{_ytd_start.isoformat()}" '
            f'data-ytd-end="{_ytd_end.isoformat()}"'
        )
        this_week_label = _short_range(_this_week_start, _this_week_end)
        last_week_label = _short_range(_last_week_start, _last_week_end)
        last_month_label = _short_range(_last_month_start, _last_month_end)
        ytd_label = _short_range(_ytd_start, _ytd_end)
        stripe_state_html = self._render_stripe_invoice_state_html()

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
    padding-bottom: 24px;
}}

.footer {{
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 11;
    margin: 0;
    padding: 10px 12px 12px;
    border-top: 1px solid rgba(255,255,255,0.08);
    background:
        linear-gradient(180deg, rgba(28,28,30,0.22) 0%, rgba(28,28,30,0.9) 18%, #1c1c1e 100%);
    backdrop-filter: blur(18px);
    box-shadow: 0 -10px 24px rgba(0,0,0,0.28);
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

.project-row.expandable {{
    display: block;
    padding: 0;
}}

.project-row.expandable:hover {{
    background: transparent;
}}

.project-row-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 6px;
    border-radius: 6px;
    cursor: pointer;
}}

.project-row-header:hover {{
    background: rgba(255,255,255,0.04);
}}

.project-row-header:focus {{
    outline: none;
    background: rgba(255,255,255,0.06);
}}

.row-chevron {{
    display: inline-block;
    width: 12px;
    color: #6e7781;
    font-size: 10px;
    margin-right: 4px;
    transition: transform 0.15s ease;
}}

.project-row.expanded .row-chevron {{
    transform: rotate(90deg);
}}

.time-blocks {{
    display: none;
    padding: 2px 6px 4px 22px;
}}

.project-row.expanded .time-blocks {{
    display: block;
}}

.time-block {{
    display: flex;
    align-items: baseline;
    gap: 6px;
    font-size: 12px;
    color: #8b949e;
    padding: 2px 0;
    font-variant-numeric: tabular-nums;
}}

.time-block-range {{
    color: #b0b8c1;
    flex-shrink: 0;
}}

.time-block-duration {{
    color: #6e7781;
    flex-shrink: 0;
}}

.time-block-desc {{
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    border: 0;
    padding: 0;
    background: transparent;
    color: #8b949e;
    font: inherit;
    text-align: left;
    cursor: pointer;
    -webkit-appearance: none;
}}

.time-block-desc:hover {{
    color: #c9d1d9;
    text-decoration: underline;
}}

.time-block-desc.copied {{
    color: #58a6ff;
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

.billing-period {{
    font-size: 10.5px;
    color: #6e7681;
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
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

.export-group {{
    position: relative;
    flex: 1;
}}

.export-group.open .export-menu {{
    display: flex;
}}

.export-toggle {{
    width: 100%;
    padding: 0;
    overflow: hidden;
    display: block;
}}

.export-primary {{
    display: block;
    width: 100%;
    padding: 7px 10px;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: center;
    cursor: pointer;
    -webkit-appearance: none;
}}

.export-toggle:hover {{
    background: rgba(255,255,255,0.08);
    color: #c9d1d9;
    border-color: rgba(255,255,255,0.18);
}}

.export-menu {{
    position: absolute;
    left: 50%;
    right: auto;
    bottom: calc(100% + 6px);
    width: min(280px, calc(100vw - 32px));
    --export-menu-shift: 0px;
    transform: translateX(calc(-50% + var(--export-menu-shift)));
    display: none;
    flex-direction: column;
    gap: 4px;
    padding: 6px;
    border-radius: 10px;
    background: #202225;
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 12px 24px rgba(0,0,0,0.28);
    z-index: 20;
    max-height: 280px;
    overflow-y: auto;
}}

.export-menu.wide {{
    width: min(320px, calc(100vw - 24px));
}}

.export-option {{
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

.export-option:hover {{
    background: rgba(255,255,255,0.08);
}}

.invoice-option {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
}}

.export-option-meta {{
    font-size: 11px;
    color: #3fb950;
    flex: 0 0 auto;
    text-align: right;
}}

.export-option-meta.muted {{
    color: #6e7681;
}}

.export-empty {{
    padding: 7px 8px;
    color: #6e7681;
    font-size: 11px;
    font-style: italic;
}}

.export-stage {{
    display: flex;
    flex-direction: column;
    gap: 4px;
}}

.export-stage-title {{
    padding: 4px 8px 6px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #6e7681;
}}

.export-stage-header {{
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 4px 4px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    margin-bottom: 4px;
}}

.export-stage-header .export-stage-title {{
    padding: 4px 4px;
    text-transform: none;
    letter-spacing: 0;
    font-size: 11px;
    color: #c9d1d9;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}

.export-back {{
    flex: 0 0 auto;
    width: 24px;
    height: 24px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: #c9d1d9;
    font-size: 14px;
    cursor: pointer;
    -webkit-appearance: none;
}}

.export-back:hover {{
    background: rgba(255,255,255,0.08);
}}

.export-preset {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    width: 100%;
    padding: 7px 10px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: #c9d1d9;
    font: inherit;
    text-align: left;
    cursor: pointer;
    -webkit-appearance: none;
}}

.export-preset:hover {{
    background: rgba(255,255,255,0.08);
}}

.export-preset-name {{
    font-size: 11px;
}}

.export-preset-range {{
    font-size: 10px;
    color: #6e7681;
}}

.export-custom-form {{
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px 10px 4px;
    border-top: 1px solid rgba(255,255,255,0.05);
    margin-top: 4px;
}}

.export-custom-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-size: 11px;
    color: #c9d1d9;
}}

.export-custom-row input[type="date"],
.export-custom-row input[type="text"] {{
    flex: 0 0 auto;
    padding: 4px 6px;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 6px;
    background: rgba(255,255,255,0.04);
    color: #c9d1d9;
    font: inherit;
    font-size: 11px;
    color-scheme: dark;
}}

.upwork-contract-form {{
    gap: 8px;
}}

.upwork-contract-form .export-custom-row {{
    align-items: flex-start;
    flex-direction: column;
}}

.upwork-contract-form .export-custom-row input[type="text"] {{
    width: 100%;
}}

.export-form-note {{
    font-size: 11px;
    line-height: 1.4;
    color: #8b949e;
}}

.export-custom-submit {{
    margin-top: 2px;
    padding: 7px 10px;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    background: rgba(88, 166, 255, 0.12);
    color: #58a6ff;
    font: inherit;
    font-size: 11px;
    cursor: pointer;
    -webkit-appearance: none;
}}

.export-custom-submit:hover {{
    background: rgba(88, 166, 255, 0.2);
}}

.more-group {{
    position: relative;
    flex: 0 0 auto;
}}

.more-group.open .more-menu {{
    display: flex;
}}

.more-toggle {{
    min-width: 36px;
    padding: 7px 10px;
    font-size: 14px;
    line-height: 1;
}}

.more-menu {{
    position: absolute;
    right: 0;
    bottom: calc(100% + 6px);
    min-width: 140px;
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

.more-option {{
    width: 100%;
    padding: 7px 10px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: #c9d1d9;
    font-size: 11px;
    text-align: left;
    cursor: pointer;
    -webkit-appearance: none;
}}

.more-option:hover {{
    background: rgba(255,255,255,0.08);
}}

.more-option.danger {{
    color: #c9d1d9;
}}

.more-option.danger:hover {{
    background: rgba(248, 81, 73, 0.12);
    color: #f85149;
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

.stripe-state-overlay {{
    position: fixed;
    inset: 0;
    background: rgba(10, 12, 16, 0.74);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 18px;
    z-index: 50;
}}

.stripe-state-card {{
    width: 100%;
    max-width: 360px;
    border-radius: 14px;
    background: #161b22;
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 16px 40px rgba(0,0,0,0.45);
    padding: 16px;
}}

.stripe-state-card.success {{
    border-color: rgba(63,185,80,0.28);
}}

.stripe-state-card.error {{
    border-color: rgba(248,81,73,0.28);
}}

.stripe-state-title {{
    font-size: 15px;
    font-weight: 700;
    color: #f0f6fc;
}}

.stripe-state-detail {{
    margin-top: 8px;
    font-size: 12px;
    line-height: 1.45;
    color: rgba(201,209,217,0.92);
}}

.stripe-state-note {{
    margin-top: 8px;
    font-size: 11px;
    line-height: 1.45;
    color: rgba(201,209,217,0.72);
}}

.stripe-state-actions {{
    margin-top: 14px;
    display: flex;
    gap: 8px;
}}

.stripe-state-btn {{
    flex: 1;
    border: 0;
    border-radius: 10px;
    padding: 10px 12px;
    background: rgba(88,166,255,0.95);
    color: #fff;
    font-size: 12px;
    font-weight: 600;
}}

.stripe-state-btn.secondary {{
    background: rgba(255,255,255,0.08);
    color: #c9d1d9;
}}

.stripe-state-btn.success {{
    background: rgba(63,185,80,0.92);
}}

.stripe-customer-picker {{
    width: 100%;
    margin-top: 12px;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.08);
    background: rgba(255,255,255,0.04);
    color: #f0f6fc;
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
                    <button class="refresh-option" onclick="runRefreshAction('clear_all_caches')">Clear All Caches</button>
                    <button class="refresh-option" onclick="runRefreshAction('open_cache_folder')">Open Cache Folder</button>
                </div>
            </div>
            <div class="export-group" id="exportGroup">
                <div class="action-btn export-toggle">
                    <button class="export-primary" onclick="toggleExportMenu(event)">Export/Invoice</button>
                </div>
                <div class="export-menu" id="exportMenu" {export_menu_data_attrs}>
                    <div class="export-stage" id="exportStage0">
                        <div class="export-stage-title">Choose action</div>
                        <button class="export-option" onclick="exportChooseWorkflow('csv')">Export CSV</button>
                        <button class="export-option" onclick="exportChooseWorkflow('invoice')">Create Stripe Invoice</button>
                        <button class="export-option" onclick="exportChooseWorkflow('upwork')">Open Upwork Diary</button>
                    </div>
                    <div class="export-stage" id="exportStage1" style="display:none;">
                        <div class="export-stage-title">Choose project</div>
                        {export_items_html}
                    </div>
                    <div class="export-stage" id="exportStage2" style="display:none;">
                        <div class="export-stage-header">
                            <button class="export-back" onclick="exportBackToProjects(event)" aria-label="Back">←</button>
                            <span class="export-stage-title" id="exportStage2Title">Project</span>
                        </div>
                        <button class="export-preset" id="exportPresetLbd" style="display:none;" onclick="exportPickPreset(this)">
                            <span class="export-preset-name">Since last billed</span>
                            <span class="export-preset-range" id="exportPresetLbdRange"></span>
                        </button>
                        <button class="export-preset" data-start="{_this_week_start.isoformat()}" data-end="{_this_week_end.isoformat()}" onclick="exportPickPreset(this)">
                            <span class="export-preset-name">This week</span>
                            <span class="export-preset-range">{_esc(this_week_label)}</span>
                        </button>
                        <button class="export-preset" data-start="{_last_week_start.isoformat()}" data-end="{_last_week_end.isoformat()}" onclick="exportPickPreset(this)">
                            <span class="export-preset-name">Last week</span>
                            <span class="export-preset-range">{_esc(last_week_label)}</span>
                        </button>
                        <button class="export-preset" data-start="{_last_month_start.isoformat()}" data-end="{_last_month_end.isoformat()}" onclick="exportPickPreset(this)">
                            <span class="export-preset-name">Last month</span>
                            <span class="export-preset-range">{_esc(last_month_label)}</span>
                        </button>
                        <button class="export-preset" data-start="{_ytd_start.isoformat()}" data-end="{_ytd_end.isoformat()}" onclick="exportPickPreset(this)">
                            <span class="export-preset-name">Year to date</span>
                            <span class="export-preset-range">{_esc(ytd_label)}</span>
                        </button>
                        <button class="export-preset export-custom-toggle" onclick="exportToggleCustomForm(event)">
                            <span class="export-preset-name">Custom range…</span>
                            <span class="export-preset-range">▾</span>
                        </button>
                        <div class="export-custom-form" id="exportCustomForm" style="display:none;">
                            <label class="export-custom-row">
                                <span>Start</span>
                                <input type="date" id="exportCustomStart" value="{_last_month_start.isoformat()}">
                            </label>
                            <label class="export-custom-row">
                                <span>End</span>
                                <input type="date" id="exportCustomEnd" value="{_last_month_end.isoformat()}">
                            </label>
                            <button class="export-custom-submit" onclick="exportSubmitCustom(event)">Export</button>
                        </div>
                    </div>
                    <div class="export-stage" id="invoiceStage1" style="display:none;">
                        <div class="export-stage-title">Choose project</div>
                        {invoice_items_html}
                    </div>
                    <div class="export-stage" id="upworkStage1" style="display:none;">
                        <div class="export-stage-header">
                            <button class="export-back" onclick="upworkBackToActions(event)" aria-label="Back">←</button>
                            <span class="export-stage-title">Open today&apos;s diary</span>
                        </div>
                        {upwork_items_html}
                    </div>
                    <div class="export-stage" id="upworkStage2" style="display:none;">
                        <div class="export-stage-header">
                            <button class="export-back" onclick="upworkBackToProjects(event)" aria-label="Back">←</button>
                            <span class="export-stage-title" id="upworkStage2Title">Project</span>
                        </div>
                        <div class="export-custom-form upwork-contract-form">
                            <label class="export-custom-row">
                                <span>Contract ID</span>
                                <input type="text" id="upworkContractInput" inputmode="numeric" placeholder="12345678">
                            </label>
                            <div class="export-form-note" id="upworkContractNote">
                                Save the Upwork contract id once, then this shortcut can open today&apos;s work diary directly.
                            </div>
                            <button class="export-custom-submit" onclick="submitUpworkContract(event)">Save &amp; Open Diary</button>
                        </div>
                    </div>
                    <div class="export-stage" id="invoiceStage2" style="display:none;">
                        <div class="export-stage-header">
                            <button class="export-back" onclick="invoiceBackToProjects(event)" aria-label="Back">←</button>
                            <span class="export-stage-title" id="invoiceStage2Title">Project</span>
                        </div>
                        <button class="export-preset" id="invoicePresetLbd" style="display:none;" onclick="invoicePickPreset(this)">
                            <span class="export-preset-name">Since last billed</span>
                            <span class="export-preset-range" id="invoicePresetLbdRange"></span>
                        </button>
                        <button class="export-preset" data-start="{_this_week_start.isoformat()}" data-end="{_this_week_end.isoformat()}" onclick="invoicePickPreset(this)">
                            <span class="export-preset-name">This week</span>
                            <span class="export-preset-range">{_esc(this_week_label)}</span>
                        </button>
                        <button class="export-preset" data-start="{_last_week_start.isoformat()}" data-end="{_last_week_end.isoformat()}" onclick="invoicePickPreset(this)">
                            <span class="export-preset-name">Last week</span>
                            <span class="export-preset-range">{_esc(last_week_label)}</span>
                        </button>
                        <button class="export-preset" data-start="{_last_month_start.isoformat()}" data-end="{_last_month_end.isoformat()}" onclick="invoicePickPreset(this)">
                            <span class="export-preset-name">Last month</span>
                            <span class="export-preset-range">{_esc(last_month_label)}</span>
                        </button>
                        <button class="export-preset" data-start="{_ytd_start.isoformat()}" data-end="{_ytd_end.isoformat()}" onclick="invoicePickPreset(this)">
                            <span class="export-preset-name">Year to date</span>
                            <span class="export-preset-range">{_esc(ytd_label)}</span>
                        </button>
                        <button class="export-preset export-custom-toggle" onclick="invoiceToggleCustomForm(event)">
                            <span class="export-preset-name">Custom range…</span>
                            <span class="export-preset-range">▾</span>
                        </button>
                        <div class="export-custom-form" id="invoiceCustomForm" style="display:none;">
                            <label class="export-custom-row">
                                <span>Start</span>
                                <input type="date" id="invoiceCustomStart" value="{_last_month_start.isoformat()}">
                            </label>
                            <label class="export-custom-row">
                                <span>End</span>
                                <input type="date" id="invoiceCustomEnd" value="{_last_month_end.isoformat()}">
                            </label>
                            <button class="export-custom-submit" onclick="invoiceSubmitCustom(event)">Continue</button>
                        </div>
                    </div>
                </div>
            </div>
            <div class="more-group" id="moreGroup">
                <button class="action-btn more-toggle" onclick="toggleMoreMenu(event)" aria-label="More actions">⋯</button>
                <div class="more-menu" id="moreMenu">
                    <button class="more-option" onclick="runMoreAction('settings')">Settings</button>
                    <button class="more-option" onclick="runMoreAction('update_app')">Update</button>
                    <button class="more-option danger" onclick="runMoreAction('quit')">Quit</button>
                </div>
            </div>
        </div>

        <div class="update-time">{_esc(update_time_text)}</div>
    </div>
</div>
{stripe_state_html}
<div class="stripe-state-overlay" id="stripeInlineLoading" style="display:none;">
    <div class="stripe-state-card">
        <div class="stripe-state-title">Creating draft invoice…</div>
        <div class="stripe-state-detail" id="stripeInlineLoadingDetail">Preparing Stripe invoice details.</div>
        <div class="stripe-state-note">This creates a draft only. You will still review and send it from Stripe.</div>
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

    function copyDescription(el) {{
        if (!el) return;
        var text = el.getAttribute('data-copy-text') || '';
        if (!text) return;
        postAction('copy_text:' + encodeURIComponent(text));
        el.classList.add('copied');
        var original = el.getAttribute('data-original-text');
        if (!original) {{
            original = el.textContent;
            el.setAttribute('data-original-text', original);
        }}
        el.textContent = 'Copied';
        window.setTimeout(function() {{
            el.classList.remove('copied');
            el.textContent = original;
        }}, 900);
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

    function syncFooterClearance() {{
        var footer = document.querySelector('.footer');
        var sectionList = document.querySelector('.section-list');
        if (!footer || !sectionList) return;
        var footerHeight = Math.ceil(footer.getBoundingClientRect().height);
        sectionList.style.paddingBottom = String(footerHeight + 8) + 'px';
    }}

    function closeRefreshMenu() {{
        var group = document.getElementById('refreshGroup');
        if (group) {{
            group.classList.remove('open');
        }}
    }}

    function closeExportMenu() {{
        var group = document.getElementById('exportGroup');
        if (group) {{
            group.classList.remove('open');
        }}
        setExportMenuWide(false);
    }}

    function closeMoreMenu() {{
        var group = document.getElementById('moreGroup');
        if (group) {{
            group.classList.remove('open');
        }}
    }}

    function closeAllPopupMenus() {{
        closeRefreshMenu();
        closeExportMenu();
        closeMoreMenu();
    }}

    function toggleRefreshMenu(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var group = document.getElementById('refreshGroup');
        if (!group) return;
        closeExportMenu();
        closeMoreMenu();
        group.classList.toggle('open');
    }}

    function toggleExportMenu(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var group = document.getElementById('exportGroup');
        if (!group) return;
        closeRefreshMenu();
        closeMoreMenu();
        var willOpen = !group.classList.contains('open');
        group.classList.toggle('open');
        if (willOpen) {{
            exportShowStage0();
            scheduleExportMenuPosition();
        }}
    }}

    function exportShowStage0() {{
        setExportMenuWide(false);
        var ids = ['exportStage0', 'exportStage1', 'exportStage2', 'invoiceStage1', 'invoiceStage2', 'upworkStage1', 'upworkStage2'];
        for (var i = 0; i < ids.length; i++) {{
            var el = document.getElementById(ids[i]);
            if (el) {{
                el.style.display = (ids[i] === 'exportStage0') ? '' : 'none';
            }}
        }}
        var exportForm = document.getElementById('exportCustomForm');
        if (exportForm) exportForm.style.display = 'none';
        var invoiceForm = document.getElementById('invoiceCustomForm');
        if (invoiceForm) invoiceForm.style.display = 'none';
        var upworkInput = document.getElementById('upworkContractInput');
        if (upworkInput) upworkInput.value = '';
        scheduleExportMenuPosition();
    }}

    function setExportMenuWide(isWide) {{
        var menu = document.getElementById('exportMenu');
        if (!menu) return;
        menu.classList.toggle('wide', !!isWide);
        scheduleExportMenuPosition();
    }}

    function scheduleExportMenuPosition() {{
        window.requestAnimationFrame(positionExportMenu);
    }}

    function positionExportMenu() {{
        var menu = document.getElementById('exportMenu');
        var group = document.getElementById('exportGroup');
        if (!menu || !group || !group.classList.contains('open')) return;
        menu.style.setProperty('--export-menu-shift', '0px');
        var viewportWidth = document.documentElement.clientWidth || window.innerWidth || 0;
        var margin = 12;
        var rect = menu.getBoundingClientRect();
        var shift = 0;
        if (rect.left < margin) {{
            shift += (margin - rect.left);
        }}
        if (rect.right > (viewportWidth - margin)) {{
            shift -= (rect.right - (viewportWidth - margin));
        }}
        menu.style.setProperty('--export-menu-shift', shift.toFixed(1) + 'px');
    }}

    function exportChooseWorkflow(kind) {{
        var stage0 = document.getElementById('exportStage0');
        if (stage0) stage0.style.display = 'none';
        if (kind === 'csv') {{
            exportShowStage1();
            return;
        }}
        if (kind === 'upwork') {{
            upworkShowStage1();
            return;
        }}
        invoiceShowStage1();
    }}

    function exportShowStage1() {{
        setExportMenuWide(false);
        var s0 = document.getElementById('exportStage0');
        var s1 = document.getElementById('exportStage1');
        var s2 = document.getElementById('exportStage2');
        var i1 = document.getElementById('invoiceStage1');
        var i2 = document.getElementById('invoiceStage2');
        var u1 = document.getElementById('upworkStage1');
        if (s0) s0.style.display = 'none';
        if (s1) s1.style.display = '';
        if (s2) s2.style.display = 'none';
        if (i1) i1.style.display = 'none';
        if (i2) i2.style.display = 'none';
        if (u1) u1.style.display = 'none';
        var form = document.getElementById('exportCustomForm');
        if (form) form.style.display = 'none';
        var invoiceForm = document.getElementById('invoiceCustomForm');
        if (invoiceForm) invoiceForm.style.display = 'none';
        scheduleExportMenuPosition();
    }}

    function exportBackToProjects(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        exportShowStage1();
    }}

    function selectExportProject(btn) {{
        if (!btn) return;
        var pid = btn.getAttribute('data-project-id');
        var name = btn.getAttribute('data-project-name');
        var lbd = btn.getAttribute('data-last-billed');
        var capFillDate = btn.getAttribute('data-cap-fill-date');

        var stage2 = document.getElementById('exportStage2');
        var title = document.getElementById('exportStage2Title');
        if (title) title.textContent = name;
        if (stage2) stage2.setAttribute('data-project-id', pid);

        var lbdBtn = document.getElementById('exportPresetLbd');
        var lbdRangeSpan = document.getElementById('exportPresetLbdRange');
        var lbdNameSpan = lbdBtn ? lbdBtn.querySelector('.export-preset-name') : null;
        if (lbdBtn && lbdRangeSpan) {{
            if (lbd) {{
                var lbdStart = addDays(lbd, 1);
                var menu = document.getElementById('exportMenu');
                var today = menu ? menu.getAttribute('data-today') : null;
                var lbdEnd = capFillDate || today;
                if (lbdEnd) {{
                    lbdBtn.setAttribute('data-start', lbdStart);
                    lbdBtn.setAttribute('data-end', lbdEnd);
                    lbdRangeSpan.textContent = formatRange(lbdStart, lbdEnd);
                    if (lbdNameSpan) {{
                        lbdNameSpan.textContent = capFillDate ? 'Unbilled (under cap)' : 'Since last billed';
                    }}
                    lbdBtn.style.display = '';
                }} else {{
                    lbdBtn.style.display = 'none';
                }}
            }} else {{
                lbdBtn.style.display = 'none';
            }}
        }}

        document.getElementById('exportStage1').style.display = 'none';
        stage2.style.display = '';
        var form = document.getElementById('exportCustomForm');
        if (form) form.style.display = 'none';
    }}

    function addDays(isoDate, days) {{
        // Parse YYYY-MM-DD as a local date and add days
        var parts = isoDate.split('-');
        var d = new Date(parseInt(parts[0],10), parseInt(parts[1],10)-1, parseInt(parts[2],10));
        d.setDate(d.getDate() + days);
        var y = d.getFullYear();
        var m = String(d.getMonth()+1).padStart(2,'0');
        var day = String(d.getDate()).padStart(2,'0');
        return y + '-' + m + '-' + day;
    }}

    function formatRange(startIso, endIso) {{
        var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        function fmt(iso) {{
            var p = iso.split('-');
            return months[parseInt(p[1],10)-1] + ' ' + parseInt(p[2],10);
        }}
        return fmt(startIso) + ' \u2013 ' + fmt(endIso);
    }}

    function exportPickPreset(btn) {{
        if (!btn) return;
        var start = btn.getAttribute('data-start');
        var end = btn.getAttribute('data-end');
        if (!start || !end) return;
        var stage2 = document.getElementById('exportStage2');
        var pid = stage2 ? stage2.getAttribute('data-project-id') : null;
        if (!pid) return;
        closeExportMenu();
        postAction('export_csv:' + pid + ':' + start + ':' + end);
    }}

    function exportToggleCustomForm(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var form = document.getElementById('exportCustomForm');
        if (!form) return;
        form.style.display = (form.style.display === 'none' || !form.style.display) ? '' : 'none';
    }}

    function exportSubmitCustom(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var stage2 = document.getElementById('exportStage2');
        var pid = stage2 ? stage2.getAttribute('data-project-id') : null;
        var start = document.getElementById('exportCustomStart').value;
        var end = document.getElementById('exportCustomEnd').value;
        if (!pid || !start || !end) return;
        if (start > end) {{ var t = start; start = end; end = t; }}
        closeExportMenu();
        postAction('export_csv:' + pid + ':' + start + ':' + end);
    }}

    function invoiceShowStage1() {{
        setExportMenuWide(false);
        var s0 = document.getElementById('exportStage0');
        var s1 = document.getElementById('invoiceStage1');
        var s2 = document.getElementById('invoiceStage2');
        var e1 = document.getElementById('exportStage1');
        var e2 = document.getElementById('exportStage2');
        var u1 = document.getElementById('upworkStage1');
        if (s0) s0.style.display = 'none';
        if (s1) s1.style.display = '';
        if (s2) s2.style.display = 'none';
        if (e1) e1.style.display = 'none';
        if (e2) e2.style.display = 'none';
        if (u1) u1.style.display = 'none';
        var form = document.getElementById('invoiceCustomForm');
        if (form) form.style.display = 'none';
        var exportForm = document.getElementById('exportCustomForm');
        if (exportForm) exportForm.style.display = 'none';
        scheduleExportMenuPosition();
    }}

    function upworkShowStage1() {{
        setExportMenuWide(false);
        var s0 = document.getElementById('exportStage0');
        var e1 = document.getElementById('exportStage1');
        var e2 = document.getElementById('exportStage2');
        var i1 = document.getElementById('invoiceStage1');
        var i2 = document.getElementById('invoiceStage2');
        var u1 = document.getElementById('upworkStage1');
        var u2 = document.getElementById('upworkStage2');
        if (s0) s0.style.display = 'none';
        if (e1) e1.style.display = 'none';
        if (e2) e2.style.display = 'none';
        if (i1) i1.style.display = 'none';
        if (i2) i2.style.display = 'none';
        if (u1) u1.style.display = '';
        if (u2) u2.style.display = 'none';
        var form = document.getElementById('exportCustomForm');
        if (form) form.style.display = 'none';
        var invoiceForm = document.getElementById('invoiceCustomForm');
        if (invoiceForm) invoiceForm.style.display = 'none';
        scheduleExportMenuPosition();
    }}

    function upworkBackToActions(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        exportShowStage0();
    }}

    function upworkBackToProjects(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        upworkShowStage1();
    }}

    function upworkSelectProject(btn) {{
        if (!btn) return;
        var pid = btn.getAttribute('data-project-id');
        var name = btn.getAttribute('data-project-name');
        var contractId = btn.getAttribute('data-upwork-contract-id');
        var menu = document.getElementById('exportMenu');
        var today = menu ? menu.getAttribute('data-today') : '';
        if (!pid || !today) return;
        if (contractId) {{
            closeExportMenu();
            postAction('open_upwork_diary:' + pid + ':' + today);
            return;
        }}
        var stage1 = document.getElementById('upworkStage1');
        var stage2 = document.getElementById('upworkStage2');
        var title = document.getElementById('upworkStage2Title');
        var input = document.getElementById('upworkContractInput');
        var note = document.getElementById('upworkContractNote');
        if (title) title.textContent = name || 'Project';
        if (input) {{
            input.value = '';
            input.setAttribute('data-project-id', pid);
            input.setAttribute('data-entry-date', today);
            window.setTimeout(function() {{ input.focus(); }}, 0);
        }}
        if (note) {{
            note.textContent = "Save the Upwork contract id once, then this shortcut can open today's work diary directly.";
        }}
        setExportMenuWide(true);
        if (stage1) stage1.style.display = 'none';
        if (stage2) stage2.style.display = '';
        scheduleExportMenuPosition();
    }}

    function submitUpworkContract(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var input = document.getElementById('upworkContractInput');
        if (!input) return;
        var pid = input.getAttribute('data-project-id');
        var entryDate = input.getAttribute('data-entry-date');
        var contractId = (input.value || '').trim();
        if (!pid || !entryDate || !contractId) return;
        if (!/^\\d+$/.test(contractId)) {{
            var note = document.getElementById('upworkContractNote');
            if (note) {{
                note.textContent = 'Contract ID must contain digits only.';
            }}
            input.focus();
            input.select();
            return;
        }}
        closeExportMenu();
        postAction('save_upwork_contract:' + pid + ':' + contractId + ':' + entryDate);
    }}

    function invoiceBackToProjects(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        invoiceShowStage1();
    }}

    function selectInvoiceProject(btn) {{
        if (!btn) return;
        var pid = btn.getAttribute('data-project-id');
        var name = btn.getAttribute('data-project-name');
        var lbd = btn.getAttribute('data-last-billed');
        var capFillDate = btn.getAttribute('data-cap-fill-date');

        var stage2 = document.getElementById('invoiceStage2');
        var title = document.getElementById('invoiceStage2Title');
        if (title) title.textContent = name;
        if (stage2) stage2.setAttribute('data-project-id', pid);

        var lbdBtn = document.getElementById('invoicePresetLbd');
        var lbdRangeSpan = document.getElementById('invoicePresetLbdRange');
        var lbdNameSpan = lbdBtn ? lbdBtn.querySelector('.export-preset-name') : null;
        if (lbdBtn && lbdRangeSpan) {{
            if (lbd) {{
                var lbdStart = addDays(lbd, 1);
                var menu = document.getElementById('invoiceMenu');
                var today = menu ? menu.getAttribute('data-today') : null;
                var lbdEnd = capFillDate || today;
                if (lbdEnd) {{
                    lbdBtn.setAttribute('data-start', lbdStart);
                    lbdBtn.setAttribute('data-end', lbdEnd);
                    lbdRangeSpan.textContent = formatRange(lbdStart, lbdEnd);
                    if (lbdNameSpan) {{
                        lbdNameSpan.textContent = capFillDate ? 'Unbilled (under cap)' : 'Since last billed';
                    }}
                    lbdBtn.style.display = '';
                }} else {{
                    lbdBtn.style.display = 'none';
                }}
            }} else {{
                lbdBtn.style.display = 'none';
            }}
        }}

        document.getElementById('invoiceStage1').style.display = 'none';
        stage2.style.display = '';
        var form = document.getElementById('invoiceCustomForm');
        if (form) form.style.display = 'none';
    }}

    function showStripeInlineLoading(message) {{
        closeAllPopupMenus();
        var detail = document.getElementById('stripeInlineLoadingDetail');
        if (detail && message) {{
            detail.textContent = message;
        }}
        var overlay = document.getElementById('stripeInlineLoading');
        if (overlay) {{
            overlay.style.display = 'flex';
        }}
    }}

    function invoiceSubmitRange(start, end) {{
        var stage2 = document.getElementById('invoiceStage2');
        var pid = stage2 ? stage2.getAttribute('data-project-id') : null;
        if (!pid || !start || !end) return;
        showStripeInlineLoading('Checking Stripe customer and creating a draft invoice.');
        window.requestAnimationFrame(function() {{
            postAction('stripe_invoice_prepare:' + pid + ':' + start + ':' + end);
        }});
    }}

    function invoicePickPreset(btn) {{
        if (!btn) return;
        var start = btn.getAttribute('data-start');
        var end = btn.getAttribute('data-end');
        if (!start || !end) return;
        invoiceSubmitRange(start, end);
    }}

    function invoiceToggleCustomForm(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var form = document.getElementById('invoiceCustomForm');
        if (!form) return;
        form.style.display = (form.style.display === 'none' || !form.style.display) ? '' : 'none';
    }}

    function invoiceSubmitCustom(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var start = document.getElementById('invoiceCustomStart').value;
        var end = document.getElementById('invoiceCustomEnd').value;
        if (!start || !end) return;
        if (start > end) {{ var t = start; start = end; end = t; }}
        invoiceSubmitRange(start, end);
    }}

    function submitStripeCustomerSelection() {{
        var select = document.getElementById('stripeCustomerSelect');
        if (!select || !select.value) return;
        var pid = select.getAttribute('data-project-id');
        var start = select.getAttribute('data-start');
        var end = select.getAttribute('data-end');
        showStripeInlineLoading('Creating a draft invoice in Stripe.');
        window.requestAnimationFrame(function() {{
            postAction('stripe_invoice_create:' + pid + ':' + start + ':' + end + ':' + select.value);
        }});
    }}

    function toggleMoreMenu(event) {{
        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}
        var group = document.getElementById('moreGroup');
        if (!group) return;
        closeRefreshMenu();
        closeExportMenu();
        group.classList.toggle('open');
    }}

    function runRefreshAction(action) {{
        closeRefreshMenu();
        postAction(action);
    }}

    function runExportAction(projectId) {{
        closeExportMenu();
        postAction('export_csv:' + projectId);
    }}

    function runMoreAction(action) {{
        closeMoreMenu();
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

    var EXPANDED_PROJECTS_KEY = 'dashboard.todayExpandedProjects';

    function loadExpandedProjects() {{
        try {{
            var raw = localStorage.getItem(EXPANDED_PROJECTS_KEY);
            if (!raw) return {{}};
            var parsed = JSON.parse(raw);
            return (parsed && typeof parsed === 'object') ? parsed : {{}};
        }} catch (e) {{
            return {{}};
        }}
    }}

    function saveExpandedProjects(state) {{
        try {{
            localStorage.setItem(EXPANDED_PROJECTS_KEY, JSON.stringify(state));
        }} catch (e) {{}}
    }}

    function toggleProject(headerEl) {{
        var row = headerEl.closest('.project-row.expandable');
        if (!row) return;
        var key = row.getAttribute('data-project-key');
        var willExpand = !row.classList.contains('expanded');
        row.classList.toggle('expanded', willExpand);

        var state = loadExpandedProjects();
        if (willExpand) {{
            state[key] = true;
        }} else {{
            delete state[key];
        }}
        saveExpandedProjects(state);

        scheduleReportHeight();
    }}

    function handleProjectKey(event, headerEl) {{
        if (event.key === 'Enter' || event.key === ' ') {{
            event.preventDefault();
            toggleProject(headerEl);
        }}
    }}

    function applyExpandedProjectsState() {{
        var state = loadExpandedProjects();
        var rows = document.querySelectorAll('.project-row.expandable');
        for (var i = 0; i < rows.length; i++) {{
            var key = rows[i].getAttribute('data-project-key');
            if (key && state[key]) {{
                rows[i].classList.add('expanded');
            }}
        }}
    }}

    function reportHeight() {{
        syncFooterClearance();
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
        var footer = document.querySelector('.footer');
        heightObserver = new ResizeObserver(function() {{
            scheduleReportHeight();
        }});

        heightObserver.observe(document.documentElement);
        heightObserver.observe(document.body);
        if (wrapper) {{
            heightObserver.observe(wrapper);
        }}
        if (footer) {{
            heightObserver.observe(footer);
        }}
    }}

    document.addEventListener('DOMContentLoaded', function() {{
        syncFooterClearance();
        startHeightObserver();
        applyExpandedProjectsState();
        scheduleReportHeight();
    }});
    document.addEventListener('click', function(event) {{
        var target = event.target;
        if (target && target.closest && target.closest('#refreshGroup, #exportGroup, #moreGroup, .stripe-state-overlay')) {{
            return;
        }}
        closeAllPopupMenus();
    }});
    document.addEventListener('keydown', function(event) {{
        if (event.key === 'Escape') {{
            closeAllPopupMenus();
        }}
    }});
    window.addEventListener('load', function() {{
        syncFooterClearance();
        scheduleReportHeight();
    }});
    window.addEventListener('resize', function() {{
        syncFooterClearance();
        scheduleReportHeight();
    }});
</script>
</body>
</html>"""

        return html

    def _render_stripe_invoice_state_html(self):
        """Render the current Stripe invoice workflow overlay, if any."""
        state = self._stripe_invoice_state or {}
        status = state.get('status')
        if not status:
            return ""

        title = _esc(state.get('title') or '')
        detail = _esc(state.get('detail') or '')
        note = '<div class="stripe-state-note">This creates a draft only. Review it in Stripe before sending.</div>'

        if status == 'choose_customer':
            options_html = []
            for customer in state.get('customers', []):
                options_html.append(
                    f'<option value="{_esc(customer.get("id") or "")}">{_esc(customer.get("display_name") or customer.get("id") or "")}</option>'
                )
            return f"""
            <div class="stripe-state-overlay">
                <div class="stripe-state-card">
                    <div class="stripe-state-title">Attach Stripe customer</div>
                    <div class="stripe-state-detail">
                        { _esc(state.get('project_name') or 'Project') } · { _esc(state.get('date_range_label') or '') }
                    </div>
                    <div class="stripe-state-note">Pick the Stripe customer now. The project will be linked for future invoices.</div>
                    <select class="stripe-customer-picker" id="stripeCustomerSelect"
                            data-project-id="{_esc(state.get('project_id') or '')}"
                            data-start="{_esc(state.get('start_iso') or '')}"
                            data-end="{_esc(state.get('end_iso') or '')}">
                        {"".join(options_html)}
                    </select>
                    <div class="stripe-state-actions">
                        <button class="stripe-state-btn" onclick="submitStripeCustomerSelection()">Create draft</button>
                        <button class="stripe-state-btn secondary" onclick="postAction('stripe_invoice_dismiss')">Cancel</button>
                    </div>
                </div>
            </div>"""

        if status == 'success':
            summary = state.get('summary')
            summary_html = (
                f'<div class="stripe-state-note">{_esc(summary)}</div>'
                if summary else
                ''
            )
            return f"""
            <div class="stripe-state-overlay">
                <div class="stripe-state-card success">
                    <div class="stripe-state-title">{title}</div>
                    <div class="stripe-state-detail">{detail}</div>
                    {summary_html}
                    {note}
                    <div class="stripe-state-actions">
                        <button class="stripe-state-btn success" onclick="postAction('stripe_invoice_open')">Open in Stripe</button>
                        <button class="stripe-state-btn secondary" onclick="postAction('stripe_invoice_dismiss')">Done</button>
                    </div>
                </div>
            </div>"""

        return f"""
        <div class="stripe-state-overlay">
            <div class="stripe-state-card error">
                <div class="stripe-state-title">{title or 'Draft invoice failed'}</div>
                <div class="stripe-state-detail">{detail}</div>
                <div class="stripe-state-actions">
                    <button class="stripe-state-btn secondary" onclick="postAction('stripe_invoice_dismiss')">Close</button>
                </div>
            </div>
        </div>"""

    @staticmethod
    def _format_block_time(dt):
        """Format a datetime as 'h:mma' / 'ha' (top-of-hour collapsed)."""
        s = dt.strftime("%-I:%M%p").lower()
        if s.endswith(":00am"):
            s = s[:-5] + "a"
        elif s.endswith(":00pm"):
            s = s[:-5] + "p"
        else:
            s = s.replace("am", "a").replace("pm", "p")
        return s

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
