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
from toggl_data import (
    get_daily_earnings,
    get_weekly_earnings,
    get_monthly_earnings,
    is_rate_limited,
    force_refresh_entries,
    estimate_manual_refresh_entry_api_calls,
)
from preferences import load_preferences, save_preferences
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
                'refresh_projects': self._dashboard_refresh_projects,
                'settings': self._dashboard_preferences,
                'update_app': self._dashboard_update_app,
                'quit': self._dashboard_quit,
                'export_csv': self._dashboard_export_csv,
                'stripe_invoice_prepare': self._dashboard_prepare_stripe_invoice,
                'stripe_invoice_create': self._dashboard_create_stripe_invoice,
                'stripe_invoice_open': self._dashboard_open_stripe_invoice,
                'stripe_invoice_dismiss': self._dashboard_dismiss_stripe_invoice,
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

    def _dashboard_refresh_projects(self):
        """Called from dashboard Projects button — refresh project cache and update in-place."""
        self.refresh_projects(None)

    def _dashboard_preferences(self):
        """Called from dashboard Settings button."""
        if self.dashboard is not None:
            self.dashboard.hide()
        self.prefs_controller.show_window()

    def _dashboard_quit(self):
        """Called from dashboard Quit button."""
        if self.dashboard is not None:
            self.dashboard.hide()
        rumps.quit_application()

    def _dashboard_update_app(self):
        """Called from dashboard Update button."""
        if self.dashboard is not None:
            self.dashboard.hide()
        self.update_app(None)

    def _build_exportable_projects(self):
        """Return list of projects eligible for CSV export or Stripe invoicing."""
        from toggl_data import get_projects, get_effective_project_rate
        prefs = load_preferences()
        retainer_rates = prefs.get('retainer_hourly_rates', {})
        projects_config = prefs.get('projects', {})
        stripe_project_customers = prefs.get('stripe_project_customers', {})
        out = []
        try:
            projects = get_projects()
        except Exception as exc:
            _debug(f"_build_exportable_projects: get_projects failed: {exc}")
            return out
        for pid, info in projects.items():
            rate, _src = get_effective_project_rate(info, retainer_rates, projects_config)
            if rate is None or rate <= 0:
                continue
            name = info.get('name', 'Unknown')
            defn = projects_config.get(name, {})
            lbd = defn.get('last_billed_date') if defn.get('billing_type') == 'hourly_with_cap' else None
            out.append({
                'id': str(pid),
                'name': name,
                'last_billed_date': lbd or '',
                'stripe_customer_id': stripe_project_customers.get(name, ''),
            })
        out.sort(key=lambda p: p['name'].lower())
        return out

    def _dashboard_export_csv(self, project_id, start_iso, end_iso):
        """Export hours CSV for one project given explicit ISO dates from the dashboard."""
        from datetime import date as _date
        try:
            start_d = _date.fromisoformat(start_iso)
            end_d = _date.fromisoformat(end_iso)
        except ValueError as exc:
            _debug(f"Export failed: bad dates {start_iso}/{end_iso}: {exc}")
            rumps.alert("Export failed", f"Invalid date range: {start_iso} to {end_iso}")
            return
        self._run_export(project_id, start_d, end_d)

    def _dashboard_prepare_stripe_invoice(self, project_id, start_iso, end_iso):
        """Resolve customer mapping or prompt the dashboard to associate one before invoicing."""
        from datetime import date as _date
        from toggl_data import get_projects, get_effective_project_rate
        from stripe_invoice import list_customers

        try:
            start_d = _date.fromisoformat(start_iso)
            end_d = _date.fromisoformat(end_iso)
        except ValueError as exc:
            self.dashboard.set_stripe_invoice_state({
                'status': 'error',
                'title': 'Draft invoice failed',
                'detail': f"Invalid date range: {start_iso} to {end_iso}",
            })
            self.dashboard.refresh_contents()
            _debug(f"Stripe invoice prepare failed: {exc}")
            return

        prefs = load_preferences()
        projects = get_projects()
        info = projects.get(str(project_id))
        if not info:
            self.dashboard.set_stripe_invoice_state({
                'status': 'error',
                'title': 'Draft invoice failed',
                'detail': f"Unknown project id {project_id}",
            })
            self.dashboard.refresh_contents()
            return

        project_name = info.get('name', 'Unknown')
        rate, _ = get_effective_project_rate(
            info,
            prefs.get('retainer_hourly_rates', {}),
            prefs.get('projects', {}),
        )
        if not rate:
            self.dashboard.set_stripe_invoice_state({
                'status': 'error',
                'title': 'Draft invoice failed',
                'detail': f"No billable rate configured for {project_name}",
            })
            self.dashboard.refresh_contents()
            return

        customer_id = prefs.get('stripe_project_customers', {}).get(project_name, '')
        if customer_id:
            self._dashboard_create_stripe_invoice(project_id, start_iso, end_iso, customer_id)
            return

        try:
            customers = list_customers()
        except Exception as exc:
            self.dashboard.set_stripe_invoice_state({
                'status': 'error',
                'title': 'Stripe customer lookup failed',
                'detail': str(exc),
            })
            self.dashboard.refresh_contents()
            return

        if not customers:
            self.dashboard.set_stripe_invoice_state({
                'status': 'error',
                'title': 'Stripe customer lookup failed',
                'detail': 'No Stripe customers were found for the configured Stripe API key.',
            })
            self.dashboard.refresh_contents()
            return

        self.dashboard.set_stripe_invoice_state({
            'status': 'choose_customer',
            'project_id': str(project_id),
            'project_name': project_name,
            'start_iso': start_d.isoformat(),
            'end_iso': end_d.isoformat(),
            'date_range_label': f"{start_d.strftime('%b %-d, %Y')} to {end_d.strftime('%b %-d, %Y')}",
            'customers': customers,
        })
        self.dashboard.refresh_contents()

    def _dashboard_create_stripe_invoice(self, project_id, start_iso, end_iso, customer_id=None):
        """Create a Stripe draft invoice for a project and explicit date range."""
        from datetime import date as _date
        from toggl_data import get_projects, get_effective_project_rate
        from stripe_invoice import create_draft_invoice_for_project_range

        try:
            start_d = _date.fromisoformat(start_iso)
            end_d = _date.fromisoformat(end_iso)
        except ValueError as exc:
            self.dashboard.set_stripe_invoice_state({
                'status': 'error',
                'title': 'Draft invoice failed',
                'detail': f"Invalid date range: {start_iso} to {end_iso}",
            })
            self.dashboard.refresh_contents()
            _debug(f"Stripe invoice failed: {exc}")
            return

        try:
            projects = get_projects()
            info = projects.get(str(project_id))
            if not info:
                raise RuntimeError(f"Unknown project id {project_id}")

            prefs = load_preferences()
            project_name = info.get('name', 'Unknown')
            rate, _ = get_effective_project_rate(
                info,
                prefs.get('retainer_hourly_rates', {}),
                prefs.get('projects', {}),
            )
            if not rate:
                raise RuntimeError(f"No billable rate configured for {project_name}")

            chosen_customer_id = customer_id or prefs.get('stripe_project_customers', {}).get(project_name, '')
            if not chosen_customer_id:
                raise RuntimeError(f"No Stripe customer configured for {project_name}")

            created = create_draft_invoice_for_project_range(
                project_id=project_id,
                project_name=project_name,
                customer_id=chosen_customer_id,
                hourly_rate=rate,
                start_d=start_d,
                end_d=end_d,
            )
            self._save_stripe_customer_mapping(project_name, chosen_customer_id)
            self.dashboard.set_stripe_invoice_state({
                'status': 'success',
                'title': 'Draft invoice created',
                'detail': (
                    f"{project_name} · {created['date_range_label']} · "
                    f"{created['total_hours']:.2f}h · ${created['amount_usd']:.2f}"
                ),
                'summary': created['summary'],
                'dashboard_url': created['dashboard_url'],
            })
            self.dashboard.refresh_contents()
        except Exception as exc:
            _debug(f"Stripe invoice failed: {exc}")
            self.dashboard.set_stripe_invoice_state({
                'status': 'error',
                'title': 'Draft invoice failed',
                'detail': str(exc),
            })
            self.dashboard.refresh_contents()

    def _dashboard_open_stripe_invoice(self):
        """Open the dashboard URL from the current Stripe invoice success state."""
        state = self.dashboard.get_stripe_invoice_state()
        url = (state or {}).get('dashboard_url')
        if url:
            subprocess.run(["open", url], check=False)

    def _dashboard_dismiss_stripe_invoice(self):
        """Clear any active Stripe invoice status overlay in the dashboard."""
        self.dashboard.clear_stripe_invoice_state()
        self.dashboard.refresh_contents()

    def _save_stripe_customer_mapping(self, project_name, customer_id):
        """Persist a discovered Stripe customer mapping so future invoices skip re-selection."""
        prefs = load_preferences()
        stripe_project_customers = dict(prefs.get('stripe_project_customers', {}))
        if stripe_project_customers.get(project_name) == customer_id:
            return
        stripe_project_customers[project_name] = customer_id
        prefs['stripe_project_customers'] = stripe_project_customers
        save_preferences(prefs)

    def _fallback_export_csv(self, project_id):
        """Used by the rumps fallback menu when WebKit isn't available.

        Auto-resolves the range for hourly_with_cap+last_billed_date projects;
        otherwise prompts via the native NSAlert date dialog.
        """
        from toggl_data import get_projects
        from datetime import date as _date, timedelta as _td

        info = get_projects().get(str(project_id))
        if not info:
            rumps.alert("Export failed", f"Unknown project id {project_id}")
            return
        project_name = info.get('name', 'Unknown')

        prefs = load_preferences()
        projects_config = prefs.get('projects', {})
        defn = projects_config.get(project_name, {})

        start_d = end_d = None
        if defn.get('billing_type') == 'hourly_with_cap' and defn.get('last_billed_date'):
            try:
                last_billed = datetime.strptime(defn['last_billed_date'], '%Y-%m-%d').date()
                start_d = last_billed + _td(days=1)
                end_d = _date.today()
            except (TypeError, ValueError):
                pass

        if start_d is None:
            from hours_csv_export import previous_month_range
            from date_range_dialog import prompt_date_range
            default_start, default_end = previous_month_range()
            picked = prompt_date_range(
                default_start, default_end,
                message_text=f"Export hours: {project_name}",
            )
            if picked is None:
                return
            start_d, end_d = picked

        self._run_export(project_id, start_d, end_d)

    def _run_export(self, project_id, start_d, end_d):
        """Shared export pipeline used by both the dashboard and fallback paths."""
        from toggl_data import get_projects, get_effective_project_rate
        import hours_csv_export

        if self.dashboard is not None:
            self.dashboard.hide()

        try:
            info = get_projects().get(str(project_id))
            if not info:
                rumps.alert("Export failed", f"Unknown project id {project_id}")
                return

            prefs = load_preferences()
            rate, _ = get_effective_project_rate(
                info,
                prefs.get('retainer_hourly_rates', {}),
                prefs.get('projects', {}),
            )
            if not rate:
                rumps.alert("Export failed", f"No billable rate for {info.get('name')}")
                return

            project_name = info.get('name', 'Unknown')
            path = hours_csv_export.export_project_range(
                project_id, project_name, rate, start_d, end_d
            )
            subprocess.run(["open", "-R", str(path)], check=False)
            try:
                rumps.notification("Exported hours CSV", project_name, str(path))
            except Exception:
                pass
        except Exception as exc:
            _debug(f"Export failed: {exc}")
            import traceback
            _debug(traceback.format_exc())
            rumps.alert("Export failed", str(exc))

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
            calls += estimate_manual_refresh_entry_api_calls()
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
                self.dashboard.set_last_updated(self.last_update)
                self.dashboard.set_rate_limited(is_rate_limited())
                self.dashboard.set_error_message(None)
                self.dashboard.set_exportable_projects(self._build_exportable_projects())
                self.dashboard.update_data(daily, weekly, monthly)
            else:
                self._update_fallback_menu(daily, weekly, monthly, next_refresh_calls)

        except Exception as e:
            self.title = "💰 Error"
            _debug(f"Error updating display: {e}")
            if self._dashboard_enabled and self.dashboard is not None:
                self.dashboard.set_rate_limited(is_rate_limited())
                self.dashboard.set_error_message(str(e))
                self.dashboard.refresh_contents()
            else:
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
        export_menu = self._build_export_fallback_menu()
        if export_menu is not None:
            self.menu.add(export_menu)
        self.menu.add(rumps.MenuItem("📋 View API Audit Log", callback=self.view_audit_log))
        self.menu.add(rumps.MenuItem("⚙️ Edit Preferences", callback=self.edit_preferences))
        self.menu.add(rumps.MenuItem("🆕 Update App", callback=self.update_app))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    def _build_export_fallback_menu(self):
        """Build the rumps submenu used when WebKit dashboard is unavailable."""
        projects = self._build_exportable_projects()
        if not projects:
            return None
        parent = rumps.MenuItem("📤 Export Hours CSV")
        for p in projects:
            pid = p['id']
            name = p['name']
            item = rumps.MenuItem(
                name,
                callback=lambda _, pid=pid: self._fallback_export_csv(pid),
            )
            parent.add(item)
        return parent

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
        self.update_display()

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
