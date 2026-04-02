#!/usr/bin/env python3
"""Freelance Tracker - Menu Bar App

Uses NSPopover (like Claude Usage Tracker) to show a rich dashboard
when clicking the status bar icon.
"""

import objc
import rumps
import subprocess
import os
from datetime import datetime
from pathlib import Path
from toggl_data import get_daily_earnings, get_weekly_earnings, get_monthly_earnings, is_rate_limited, force_refresh_entries
from preferences import load_preferences
from preferences_window import PreferencesWindowController
from update_window import UpdateWindowController
from carryover import get_previous_month_balance

try:
    from dashboard_panel import DashboardPanelController
    DASHBOARD_IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    if exc.name != "WebKit":
        raise
    DashboardPanelController = None
    DASHBOARD_IMPORT_ERROR = exc

# Hide dock icon - must be set before app creation
from AppKit import NSBundle, NSObject
bundle = NSBundle.mainBundle()
info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
if info:
    info['LSUIElement'] = '1'


def _debug(msg):
    with open('/tmp/freelance_dashboard_debug.log', 'a') as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")


def create_progress_bar(percent, width=12):
    """Create a Unicode block-based progress bar."""
    filled = min(int(width * percent / 100), width)
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}]"


class StatusItemClickHandler(NSObject):
    """Handles clicks on the status bar item to toggle the popover."""

    def initWithApp_(self, app):
        self = objc.super(StatusItemClickHandler, self).init()
        if self is None:
            return None
        self._app = app
        return self

    @objc.typedSelector(b'v@:@')
    def togglePopover_(self, sender):
        _debug("togglePopover_ called")
        self._app.toggle_dashboard()


class FreelanceTrackerApp(rumps.App):
    def __init__(self):
        super(FreelanceTrackerApp, self).__init__(
            "Freelance Tracker",
            "💰 Loading...",
            quit_button=None
        )
        self.last_update = None
        self.prefs_controller = PreferencesWindowController()
        self.update_controller = UpdateWindowController()
        self._click_handler = None
        self._status_item_hooked = False
        self._dashboard_enabled = DashboardPanelController is not None
        self.dashboard = None

        if self._dashboard_enabled:
            # Set up dashboard popover with action callbacks
            self.dashboard = DashboardPanelController()
            self.dashboard.set_callbacks({
                'refresh': self._dashboard_refresh,
                'preferences': self._dashboard_preferences,
                'quit': self._dashboard_quit,
            })
        else:
            _debug(f"Dashboard disabled: missing optional dependency ({DASHBOARD_IMPORT_ERROR})")

        self.update_display()

    def _hook_status_item(self):
        """Replace the default menu with a click handler that shows the NSPopover."""
        if not self._dashboard_enabled or self._status_item_hooked:
            return

        try:
            nsstatusitem = self._nsapp.nsstatusitem

            # Remove the rumps menu - we'll use the popover instead
            nsstatusitem.setMenu_(None)

            # Set the button's target/action to our handler
            self._click_handler = StatusItemClickHandler.alloc().initWithApp_(self)
            button = nsstatusitem.button()
            button.setTarget_(self._click_handler)
            button.setAction_(self._click_handler.togglePopover_)
            # Must explicitly set which mouse events trigger the action
            button.sendActionOn_(1 << 1)  # NSLeftMouseDownMask

            self._status_item_hooked = True
            _debug(f"Popover hooked to status item. action={button.action()}, target={button.target()}")
        except Exception as e:
            _debug(f"Failed to hook status item: {e}")
            import traceback
            _debug(traceback.format_exc())

    def toggle_dashboard(self):
        """Toggle the dashboard popover relative to the status item button."""
        if not self._dashboard_enabled or self.dashboard is None:
            return
        try:
            button = self._nsapp.nsstatusitem.button()
            _debug(f"toggle_dashboard: button={button}")
            self.dashboard.toggle(status_item_button=button)
        except Exception as e:
            _debug(f"toggle_dashboard error: {e}")
            import traceback
            _debug(traceback.format_exc())

    def _dashboard_refresh(self):
        """Called from dashboard Refresh button — update in-place, don't dismiss."""
        force_refresh_entries()
        self.update_display()

    def _dashboard_preferences(self):
        """Called from dashboard Preferences button."""
        if self.dashboard is not None:
            self.dashboard.hide()
        self.prefs_controller.show_window()

    def _dashboard_quit(self):
        """Called from dashboard Quit button."""
        if self.dashboard is not None:
            self.dashboard.hide()
        rumps.quit_application()

    def calculate_api_calls(self, force_refresh=False):
        from preferences import CACHE_DIR, load_preferences

        calls = 0
        now = datetime.now().timestamp()

        if not force_refresh:
            projects_cache = CACHE_DIR / "projects.json"
            if projects_cache.exists():
                prefs = load_preferences()
                cache_ttl = prefs.get('cache_ttl_projects', 86400)
                cache_age = now - projects_cache.stat().st_mtime
                if cache_age >= cache_ttl:
                    calls += 1
            else:
                calls += 1

        if force_refresh:
            calls += 1
        else:
            today = datetime.now().date()
            today_cache = CACHE_DIR / f"daily_{today.isoformat()}.json"
            if today_cache.exists():
                prefs = load_preferences()
                cache_ttl = prefs.get('cache_ttl_today', 1800)
                cache_age = now - today_cache.stat().st_mtime
                if cache_age >= cache_ttl:
                    calls += 1
            else:
                calls += 1

        return calls

    def update_display(self):
        """Update the menu bar title and dashboard data."""
        try:
            next_refresh_calls = self.calculate_api_calls(force_refresh=True)
            daily = get_daily_earnings()
            weekly = get_weekly_earnings()
            monthly = get_monthly_earnings()

            self.last_update = datetime.now()
            self.title = f"💰 ${daily['total']:.0f}"

            if self._dashboard_enabled and self.dashboard is not None:
                self.dashboard.update_data(daily, weekly, monthly)
            else:
                self._update_fallback_menu(daily, weekly, monthly, next_refresh_calls)

        except Exception as e:
            self.title = "💰 Error"
            _debug(f"Error updating display: {e}")
            if not self._dashboard_enabled:
                self._show_fallback_error_menu(e)

    def _update_fallback_menu(self, daily, weekly, monthly, next_refresh_calls):
        """Render the legacy dropdown menu when the WebKit dashboard is unavailable."""
        menu_items = []

        if DASHBOARD_IMPORT_ERROR is not None:
            menu_items.extend([
                "⚠️ Rich dashboard unavailable",
                "  Using classic dropdown menu",
                rumps.separator,
            ])

        menu_items.extend([
            f"📅 TODAY - ${daily['total']:.2f} ({daily['hours']:.2f}h)",
            rumps.separator,
        ])

        all_daily_projects = daily.get('all_projects', daily.get('projects', []))
        if all_daily_projects:
            for project in all_daily_projects:
                if project.get('billable', True):
                    menu_items.append(
                        f"  {project['name']}: ${project['earnings']:.0f} ({project['hours']:.1f}h)"
                    )
                else:
                    menu_items.append(f"  {project['name']}: {project['hours']:.1f}h")
        else:
            menu_items.append("  No time logged today")

        menu_items.extend([
            rumps.separator,
            f"📊 This Week: ${weekly['total']:.2f}",
            rumps.separator,
            f"📊 THIS MONTH - ${monthly['total']:.2f}",
            rumps.separator,
        ])

        prefs = load_preferences()
        project_targets = prefs.get('project_targets', {})
        projects_config = prefs.get('projects', {})
        all_monthly_projects = monthly.get('all_projects', monthly.get('projects', []))

        if all_monthly_projects:
            projects_to_display = []
            for project in all_monthly_projects:
                is_billable = project.get('billable', True)
                has_target = project['name'] in project_targets
                has_def = project['name'] in projects_config
                if is_billable or has_target or has_def:
                    projects_to_display.append(project)

            if projects_to_display:
                menu_items.append("   Monthly Hours by Project:")
                for project in projects_to_display:
                    if project['hours'] <= 0:
                        continue

                    name = project['name']
                    is_billable = project.get('billable', True)
                    proj_def = projects_config.get(name, {})
                    billing_type = proj_def.get('billing_type')
                    hour_tracking = proj_def.get('hour_tracking')

                    carryover_balance = 0.0
                    prev_month_label = None
                    if (
                        billing_type == 'hourly_with_cap' or
                        (billing_type == 'fixed_monthly' and hour_tracking == 'required')
                    ):
                        carryover_balance, prev_month_label = get_previous_month_balance(name)

                    target = project_targets.get(name)
                    if not target and billing_type == 'fixed_monthly' and hour_tracking in ('required', 'soft'):
                        target = proj_def.get('target_hours')
                    elif not target and billing_type == 'hourly_with_cap':
                        target = proj_def.get('cap_hours')

                    if target:
                        effective_target = max(0.1, target - carryover_balance)
                        percentage = (project['hours'] / effective_target) * 100
                        progress_bar = create_progress_bar(percentage)
                        menu_items.append(
                            f"     {name}: {project['hours']:.1f}h / {effective_target:.1f}h ({percentage:.0f}%)"
                        )
                        menu_items.append(f"       {progress_bar}")
                        if carryover_balance != 0.0 and prev_month_label:
                            sign = "+" if carryover_balance > 0 else ""
                            menu_items.append(
                                f"       ↳ {sign}{carryover_balance:.1f}h carryover from {prev_month_label}"
                            )
                        cap_fill_date = project.get('cap_fill_date')
                        if billing_type == 'hourly_with_cap' and cap_fill_date:
                            fill_dt = datetime.strptime(cap_fill_date, '%Y-%m-%d')
                            menu_items.append(
                                f"       ↳ capped as of {fill_dt.strftime('%b %-d')}"
                            )
                    elif is_billable:
                        menu_items.append(
                            f"     {name}: {project['hours']:.1f}h (${project['earnings']:.0f})"
                        )
                menu_items.append(rumps.separator)

        projection = monthly.get('projection', {})
        if projection and projection.get('worked_days', 0) > 0:
            projected = projection['projected_earnings']
            worked = projection['worked_days']
            workable = projection['workable_days']
            vacation = projection['vacation_days']
            daily_avg = projection['daily_average']
            fixed_total = projection.get('fixed_monthly_total', 0)
            projected_variable = projection.get('projected_variable', 0)

            menu_items.append(f"📈 Month Projection: ${projected:.0f}")
            if fixed_total > 0:
                menu_items.append(f"   ${fixed_total:.0f} fixed + ${projected_variable:.0f} projected hourly")
            menu_items.append(f"   Worked {worked}/{workable} workable days")
            if vacation > 0:
                menu_items.append(f"   ({vacation} days off excluded)")
            if daily_avg > 0:
                menu_items.append(f"   Hourly daily average: ${daily_avg:.0f}")
            menu_items.append(rumps.separator)

        if is_rate_limited():
            menu_items.append("⚠️  Rate Limited (using cached data)")
            menu_items.append(rumps.separator)

        if self.last_update:
            menu_items.append(f"🕐 Last updated: {self.last_update.strftime('%I:%M %p')}")
            menu_items.append(rumps.separator)

        self.menu.clear()
        for item in menu_items:
            self.menu.add(item)

        self.menu.add(rumps.MenuItem(f"🔄 Refresh Now ({next_refresh_calls} API calls)", callback=self.refresh))
        self.menu.add(rumps.MenuItem("🔄 Refresh Projects (1 API call)", callback=self.refresh_projects))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("📋 View API Audit Log", callback=self.view_audit_log))
        self.menu.add(rumps.MenuItem("⚙️ Edit Preferences", callback=self.edit_preferences))
        self.menu.add(rumps.MenuItem("🆕 Update App", callback=self.update_app))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    def _show_fallback_error_menu(self, error):
        """Show a usable dropdown menu when refresh fails in fallback mode."""
        self.menu.clear()
        self.menu.add(f"Error: {error}")
        self.menu.add(rumps.separator)

        if self.last_update:
            self.menu.add(f"Last successful update: {self.last_update.strftime('%I:%M %p')}")
            self.menu.add(rumps.separator)

        retry_calls = self.calculate_api_calls(force_refresh=True)
        self.menu.add(rumps.MenuItem(f"🔄 Retry ({retry_calls} API calls)", callback=self.refresh))
        self.menu.add(rumps.MenuItem("🔄 Refresh Projects (1 API call)", callback=self.refresh_projects))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("📋 View API Audit Log", callback=self.view_audit_log))
        self.menu.add(rumps.MenuItem("⚙️ Edit Preferences", callback=self.edit_preferences))
        self.menu.add(rumps.MenuItem("🆕 Update App", callback=self.update_app))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    @rumps.timer(1)
    def _setup_hook(self, timer):
        """One-shot timer to hook the status item after rumps finishes launching."""
        timer.stop()
        self._hook_status_item()

    @rumps.timer(1800)
    def auto_refresh(self, _):
        self.update_display()

    def refresh(self, _):
        force_refresh_entries()
        self.update_display()

    def refresh_projects(self, _):
        from toggl_data import get_projects
        cache_file = Path.home() / "Library" / "Caches" / "TogglMenuBar" / "projects.json"
        if cache_file.exists():
            cache_file.unlink()
        try:
            get_projects()
        except Exception:
            pass

    def edit_preferences(self, _):
        self.prefs_controller.show_window()

    def update_app(self, _):
        self.update_controller.show_and_run()

    @rumps.clicked("📋 View API Audit Log")
    def view_audit_log(self, _):
        log_path = Path.home() / "Library" / "Logs" / "toggl-api-audit.log"
        terminal_command = f"tail -f {log_path}"
        applescript = f'''
        tell application "Terminal"
            activate
            do script "{terminal_command}"
        end tell
        '''
        try:
            subprocess.run(["osascript", "-e", applescript], check=True)
        except subprocess.CalledProcessError:
            pass


if __name__ == "__main__":
    FreelanceTrackerApp().run()
