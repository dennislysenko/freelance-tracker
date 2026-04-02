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
from dashboard_panel import DashboardPanelController
from carryover import get_previous_month_balance

# Hide dock icon - must be set before app creation
from AppKit import NSBundle, NSObject
bundle = NSBundle.mainBundle()
info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
if info:
    info['LSUIElement'] = '1'


def _debug(msg):
    with open('/tmp/freelance_dashboard_debug.log', 'a') as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")


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

        # Set up dashboard popover with action callbacks
        self.dashboard = DashboardPanelController()
        self.dashboard.set_callbacks({
            'refresh': self._dashboard_refresh,
            'preferences': self._dashboard_preferences,
            'quit': self._dashboard_quit,
        })

        self.update_display()

    def _hook_status_item(self):
        """Replace the default menu with a click handler that shows the NSPopover."""
        if self._status_item_hooked:
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
        try:
            button = self._nsapp.nsstatusitem.button()
            _debug(f"toggle_dashboard: button={button}")
            self.dashboard.toggle(status_item_button=button)
        except Exception as e:
            _debug(f"toggle_dashboard error: {e}")
            import traceback
            _debug(traceback.format_exc())

    def _dashboard_refresh(self):
        """Called from dashboard Refresh button."""
        self.dashboard.hide()
        force_refresh_entries()
        self.update_display()

    def _dashboard_preferences(self):
        """Called from dashboard Preferences button."""
        self.dashboard.hide()
        self.prefs_controller.show_window()

    def _dashboard_quit(self):
        """Called from dashboard Quit button."""
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
            daily = get_daily_earnings()
            weekly = get_weekly_earnings()
            monthly = get_monthly_earnings()

            self.last_update = datetime.now()
            self.title = f"💰 ${daily['total']:.0f}"

            # Feed data to the dashboard popover
            self.dashboard.update_data(daily, weekly, monthly)

        except Exception as e:
            self.title = "💰 Error"
            _debug(f"Error updating display: {e}")

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
