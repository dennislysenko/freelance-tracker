#!/usr/bin/env python3
"""Freelance Tracker - Menu Bar App"""

import rumps
import subprocess
import os
from datetime import datetime
from pathlib import Path
from toggl_data import get_daily_earnings, get_weekly_earnings, get_monthly_earnings, is_rate_limited, force_refresh_entries
from preferences import load_preferences
from preferences_window import PreferencesWindowController
from carryover import get_previous_month_balance

# Hide dock icon - must be set before app creation
from AppKit import NSBundle
bundle = NSBundle.mainBundle()
info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
if info:
    info['LSUIElement'] = '1'


def create_progress_bar(percent, width=12):
    """Create a Unicode block-based progress bar.

    Args:
        percent: Progress percentage (0-100)
        width: Width of the bar in characters (default: 12)

    Returns:
        String like "[████████░░░░]"
    """
    filled = min(int(width * percent / 100), width)
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}]"


class FreelanceTrackerApp(rumps.App):
    def __init__(self):
        super(FreelanceTrackerApp, self).__init__(
            "Freelance Tracker",
            "💰 Loading...",
            quit_button=None  # We'll add our own
        )
        self.last_update = None
        self.prefs_controller = PreferencesWindowController()
        self.update_display()

    def calculate_api_calls(self, force_refresh=False):
        """
        Calculate how many API calls will be made.

        Args:
            force_refresh: If True, calculate for manual refresh (always fetches entries, never projects)
                          If False, calculate for startup (uses cache if fresh for both)
        """
        from preferences import CACHE_DIR, load_preferences

        calls = 0
        now = datetime.now().timestamp()

        # Check projects cache (only for startup, never for manual refresh)
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

        # Check today's entries cache
        if force_refresh:
            # Manual refresh: always fetch entries
            calls += 1
        else:
            # Startup: only fetch if cache is stale
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
        """Update the menu bar and dropdown with current data."""
        try:
            # Calculate expected API calls for next manual refresh
            next_refresh_calls = self.calculate_api_calls(force_refresh=True)

            # Get real data from Toggl
            daily = get_daily_earnings()
            weekly = get_weekly_earnings()
            monthly = get_monthly_earnings()

            # Update timestamp
            self.last_update = datetime.now()

            # Update menu bar title
            self.title = f"💰 ${daily['total']:.0f}"

            # Build dropdown menu
            menu_items = [
                f"📅 TODAY - ${daily['total']:.2f} ({daily['hours']:.2f}h)",
                rumps.separator,
            ]

            # Add projects (show all - billable and non-billable)
            all_daily_projects = daily.get('all_projects', daily.get('projects', []))
            if all_daily_projects:
                for project in all_daily_projects:
                    if project.get('billable', True):
                        menu_items.append(
                            f"  {project['name']}: ${project['earnings']:.0f} ({project['hours']:.1f}h)"
                        )
                    else:  # Non-billable
                        menu_items.append(
                            f"  {project['name']}: {project['hours']:.1f}h"
                        )
            else:
                menu_items.append("  No time logged today")

            # Add weekly summary
            menu_items.extend([
                rumps.separator,
                f"📊 This Week: ${weekly['total']:.2f}",
                rumps.separator,
            ])

            # Add monthly summary with breakdown
            menu_items.append(f"📊 THIS MONTH - ${monthly['total']:.2f}")
            menu_items.append(rumps.separator)

            # Load preferences for targets and project definitions
            prefs = load_preferences()
            project_targets = prefs.get('project_targets', {})
            projects_config = prefs.get('projects', {})

            # Show monthly projects (billable + non-billable with targets)
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
                        if project['hours'] > 0:
                            name = project['name']
                            is_billable = project.get('billable', True)
                            proj_def = projects_config.get(name, {})
                            billing_type = proj_def.get('billing_type')
                            hour_tracking = proj_def.get('hour_tracking')

                            # Determine if this project has a carryover-adjusted target
                            carryover_balance = 0.0
                            if (billing_type == 'fixed_monthly' and hour_tracking == 'required') or \
                               billing_type == 'hourly_with_cap':
                                carryover_balance, prev_month_label = get_previous_month_balance(name)

                            # Determine target denominator
                            target = project_targets.get(name)
                            if not target and billing_type == 'fixed_monthly' and \
                               hour_tracking in ('required', 'soft'):
                                target = proj_def.get('target_hours')
                            elif not target and billing_type == 'hourly_with_cap':
                                target = proj_def.get('cap_hours')

                            if target:
                                # Adjust for carryover: positive = over-delivered (reduces target),
                                # negative = under-delivered (increases target)
                                effective_target = target - carryover_balance
                                effective_target = max(0.1, effective_target)  # avoid division by zero
                                percentage = (project['hours'] / effective_target) * 100
                                progress_bar = create_progress_bar(percentage)
                                menu_items.append(
                                    f"     {name}: {project['hours']:.1f}h / {effective_target:.1f}h ({percentage:.0f}%)"
                                )
                                menu_items.append(f"       {progress_bar}")
                                if carryover_balance != 0.0:
                                    sign = "+" if carryover_balance > 0 else ""
                                    menu_items.append(
                                        f"       ↳ {sign}{carryover_balance:.1f}h carryover from {prev_month_label}"
                                    )
                            elif is_billable:
                                menu_items.append(
                                    f"     {name}: {project['hours']:.1f}h (${project['earnings']:.0f})"
                                )
                    menu_items.append(rumps.separator)

            # Add month projection
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
                menu_items.append(f"   ({vacation} vacation days excluded)")
                if daily_avg > 0:
                    menu_items.append(f"   Hourly daily average: ${daily_avg:.0f}")
                menu_items.append(rumps.separator)

            # Add rate limit warning if applicable
            if is_rate_limited():
                menu_items.append("⚠️  Rate Limited (using cached data)")
                menu_items.append(rumps.separator)

            # Add last update time
            if self.last_update:
                time_str = self.last_update.strftime("%I:%M %p")
                menu_items.append(f"🕐 Last updated: {time_str}")
                menu_items.append(rumps.separator)

            # Update menu
            self.menu.clear()
            for item in menu_items:
                self.menu.add(item)

            # Add action buttons
            self.menu.add(rumps.MenuItem(f"🔄 Refresh Now ({next_refresh_calls} API calls)", callback=self.refresh))
            self.menu.add(rumps.MenuItem("🔄 Refresh Projects (1 API call)", callback=self.refresh_projects))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("📋 View API Audit Log", callback=self.view_audit_log))
            self.menu.add(rumps.MenuItem("⚙️ Edit Preferences", callback=self.edit_preferences))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

        except Exception as e:
            # Show error in menu bar
            self.title = "💰 Error"
            self.menu.clear()
            self.menu.add(f"Error: {str(e)}")
            self.menu.add(rumps.separator)

            # Show last successful update time if available
            if self.last_update:
                time_str = self.last_update.strftime("%I:%M %p")
                self.menu.add(f"Last successful update: {time_str}")
                self.menu.add(rumps.separator)

            retry_calls = self.calculate_api_calls(force_refresh=True)
            self.menu.add(rumps.MenuItem(f"🔄 Retry ({retry_calls} API calls)", callback=self.refresh))
            self.menu.add(rumps.MenuItem("🔄 Refresh Projects (1 API call)", callback=self.refresh_projects))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("📋 View API Audit Log", callback=self.view_audit_log))
            self.menu.add(rumps.MenuItem("⚙️ Edit Preferences", callback=self.edit_preferences))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    @rumps.timer(1800)  # Auto-refresh every 30 minutes (1800 seconds)
    def auto_refresh(self, _):
        """Auto-refresh every 30 minutes."""
        print(f"Auto-refreshing at {datetime.now().strftime('%I:%M %p')}")
        self.update_display()

    def refresh(self, _):
        """Manual hard refresh - always fetches fresh entries."""
        rumps.notification(
            title="Freelance Tracker",
            subtitle="",
            message="Refreshing earnings data..."
        )

        # Force refresh time entries (bypasses cache)
        force_refresh_entries()

        # Update display with refreshed data
        self.update_display()

        # Show completion notification
        if self.last_update:
            rumps.notification(
                title="Freelance Tracker",
                subtitle="",
                message=f"Updated at {self.last_update.strftime('%I:%M %p')}"
            )

    @rumps.clicked("🔄 Refresh Projects (1 API call)")
    def refresh_projects(self, _):
        """Force refresh projects from Toggl API."""
        from toggl_data import get_projects
        cache_file = Path.home() / "Library" / "Caches" / "TogglMenuBar" / "projects.json"

        # Delete the cache to force refresh
        if cache_file.exists():
            cache_file.unlink()

        rumps.notification(
            title="Freelance Tracker",
            subtitle="",
            message="Refreshing projects..."
        )

        try:
            # This will fetch fresh data
            get_projects()
            rumps.notification(
                title="Freelance Tracker",
                subtitle="",
                message="Projects refreshed successfully"
            )
        except Exception as e:
            rumps.notification(
                title="Freelance Tracker",
                subtitle="Error",
                message=f"Failed to refresh projects: {str(e)}"
            )

    @rumps.clicked("📋 View API Audit Log")
    def view_audit_log(self, _):
        """Open the API audit log in a new terminal window."""
        log_path = Path.home() / "Library" / "Logs" / "toggl-api-audit.log"

        # Command to view the log with tail -f (live updates)
        terminal_command = f"tail -f {log_path}"

        applescript = f'''
        tell application "Terminal"
            activate
            do script "{terminal_command}"
        end tell
        '''

        try:
            subprocess.run(["osascript", "-e", applescript], check=True)
        except subprocess.CalledProcessError as e:
            rumps.notification(
                title="Freelance Tracker",
                subtitle="Error",
                message=f"Failed to open audit log: {str(e)}"
            )

    def edit_preferences(self, _):
        """Open native preferences window."""
        self.prefs_controller.show_window()


if __name__ == "__main__":
    FreelanceTrackerApp().run()
