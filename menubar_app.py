#!/usr/bin/env python3
"""Freelance Tracker - Menu Bar App"""

import rumps
from datetime import datetime
from toggl_data import get_daily_earnings, get_weekly_earnings, get_monthly_earnings


class FreelanceTrackerApp(rumps.App):
    def __init__(self):
        super(FreelanceTrackerApp, self).__init__(
            "Freelance Tracker",
            "💰 Loading...",
            quit_button=None  # We'll add our own
        )
        self.last_update = None
        self.update_display()

    def update_display(self):
        """Update the menu bar and dropdown with current data."""
        try:
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

            # Add projects
            if daily['projects']:
                for project in daily['projects']:
                    menu_items.append(
                        f"  {project['name']}: ${project['earnings']:.0f} ({project['hours']:.1f}h)"
                    )
            else:
                menu_items.append("  No billable time logged today")

            # Add weekly/monthly summaries
            menu_items.extend([
                rumps.separator,
                f"📊 This Week: ${weekly['total']:.2f}",
                f"📊 This Month: ${monthly['total']:.2f}",
                rumps.separator,
            ])

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
            self.menu.add(rumps.MenuItem("⟳ Refresh Now", callback=self.refresh))
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

            self.menu.add(rumps.MenuItem("⟳ Retry", callback=self.refresh))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    @rumps.timer(1800)  # Auto-refresh every 30 minutes (1800 seconds)
    def auto_refresh(self, _):
        """Auto-refresh every 30 minutes."""
        print(f"Auto-refreshing at {datetime.now().strftime('%I:%M %p')}")
        self.update_display()

    @rumps.clicked("⟳ Refresh Now")
    def refresh(self, _):
        """Manual refresh."""
        rumps.notification(
            title="Freelance Tracker",
            subtitle="",
            message="Refreshing earnings data..."
        )
        self.update_display()

        # Show completion notification
        if self.last_update:
            rumps.notification(
                title="Freelance Tracker",
                subtitle="",
                message=f"Updated at {self.last_update.strftime('%I:%M %p')}"
            )


if __name__ == "__main__":
    FreelanceTrackerApp().run()
