# Toggl Earnings Menu Bar App - Simplified Implementation Plan

## Overview

Build a macOS menu bar app that displays your daily Toggl earnings. We'll build it incrementally: first the UI with mock data, then hook in the real logic.

## Technology Stack

**Python + rumps** (no Swift needed!)
- Reuse your existing `toggl_earnings.py` logic
- rumps makes menu bar apps trivial
- Fast development, easy to iterate

## Two-Phase Approach

### Phase 1: Mock UI (Get Something Working Fast)
Build the menu bar interface with hardcoded data to validate the UI/UX before integrating real API calls.

### Phase 2: Real Integration
Connect the working UI to your actual Toggl data from `toggl_earnings.py`.

---

## Phase 1: Mock UI (30 minutes)

**Goal:** Get a working menu bar app showing "$400" with fake data

### Step 1.1: Install rumps

```bash
cd /Users/dennis/dev/freelance-workflow
source venv/bin/activate
pip install rumps
```

### Step 1.2: Create mock data script

Create `mock_data.py`:

```python
"""Mock data for testing the menu bar UI."""

def get_daily_earnings():
    """Return mock daily earnings data."""
    return {
        "total": 400.00,
        "hours": 5.25,
        "projects": [
            {
                "name": "Client A",
                "earnings": 225.00,
                "hours": 1.5,
                "rate": 150
            },
            {
                "name": "Client B",
                "earnings": 150.00,
                "hours": 1.25,
                "rate": 120
            },
            {
                "name": "Client C",
                "earnings": 25.00,
                "hours": 0.25,
                "rate": 100
            }
        ]
    }


def get_weekly_earnings():
    """Return mock weekly earnings data."""
    return {
        "total": 1200.00,
        "hours": 18.5
    }


def get_monthly_earnings():
    """Return mock monthly earnings data."""
    return {
        "total": 4800.00,
        "hours": 72.0
    }
```

### Step 1.3: Create basic menu bar app

Create `menubar_app.py`:

```python
#!/usr/bin/env python3
"""Toggl Earnings Menu Bar App - Mock Version."""

import rumps
from mock_data import get_daily_earnings, get_weekly_earnings, get_monthly_earnings


class TogglMenuBar(rumps.App):
    def __init__(self):
        super(TogglMenuBar, self).__init__("Toggl", "💰 Loading...")
        self.update_display()

    def update_display(self):
        """Update the menu bar and dropdown with current data."""
        # Get mock data
        daily = get_daily_earnings()
        weekly = get_weekly_earnings()
        monthly = get_monthly_earnings()

        # Update menu bar title
        self.title = f"💰 ${daily['total']:.0f}"

        # Build dropdown menu
        menu_items = [
            f"📅 TODAY - ${daily['total']:.2f} ({daily['hours']:.2f}h)",
            rumps.separator,
        ]

        # Add projects
        for project in daily['projects']:
            menu_items.append(
                f"  {project['name']}: ${project['earnings']:.0f} ({project['hours']:.1f}h)"
            )

        # Add weekly/monthly summaries
        menu_items.extend([
            rumps.separator,
            f"📊 This Week: ${weekly['total']:.2f}",
            f"📊 This Month: ${monthly['total']:.2f}",
            rumps.separator,
        ])

        # Update menu
        self.menu.clear()
        for item in menu_items:
            self.menu.add(item)

        # Add action buttons
        self.menu.add(rumps.MenuItem("⟳ Refresh", callback=self.refresh))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    @rumps.clicked("⟳ Refresh")
    def refresh(self, _):
        """Refresh the data."""
        rumps.notification(
            title="Toggl Earnings",
            subtitle="",
            message="Refreshed earnings data"
        )
        self.update_display()


if __name__ == "__main__":
    TogglMenuBar().run()
```

### Step 1.4: Test it!

```bash
source venv/bin/activate
python menubar_app.py
```

**Expected result:**
- Menu bar shows: `💰 $400`
- Clicking shows dropdown with mock data
- Refresh button shows notification

### Step 1.5: Iterate on UI

Now you can easily tweak:
- Menu bar icon/text format
- Dropdown layout
- What information to show
- Colors, spacing, etc.

**Once you're happy with the UI, move to Phase 2.**

---

## Phase 2: Real Integration (1-2 hours)

**Goal:** Replace mock data with real Toggl data

### Step 2.1: Refactor existing script

Extract reusable functions from `toggl_earnings.py` into `toggl_data.py`:

```python
"""Toggl API integration - extracted from toggl_earnings.py."""

import os
import json
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("TOGGL_API_TOKEN")
BASE_URL = "https://api.track.toggl.com/api/v9"
CACHE_DIR = Path.home() / ".toggl_cache"
CACHE_DIR.mkdir(exist_ok=True)


def get_daily_earnings():
    """
    Fetch today's earnings from Toggl.
    Returns same structure as mock_data.get_daily_earnings()
    """
    # Implementation here - extract from toggl_earnings.py
    # Return format:
    # {
    #     "total": 400.00,
    #     "hours": 5.25,
    #     "projects": [...]
    # }
    pass


def get_weekly_earnings():
    """Fetch this week's earnings."""
    pass


def get_monthly_earnings():
    """Fetch this month's earnings."""
    pass
```

### Step 2.2: Update menubar_app.py

Simply change the import:

```python
# Before:
from mock_data import get_daily_earnings, get_weekly_earnings, get_monthly_earnings

# After:
from toggl_data import get_daily_earnings, get_weekly_earnings, get_monthly_earnings
```

That's it! The UI stays the same, just uses real data now.

### Step 2.3: Add auto-refresh

Add a timer to refresh every 30 minutes:

```python
class TogglMenuBar(rumps.App):
    def __init__(self):
        super(TogglMenuBar, self).__init__("Toggl", "💰 Loading...")
        self.update_display()

    @rumps.timer(1800)  # 1800 seconds = 30 minutes
    def auto_refresh(self, _):
        """Auto-refresh every 30 minutes."""
        self.update_display()
```

### Step 2.4: Add error handling

Wrap data fetching in try/except:

```python
def update_display(self):
    """Update the menu bar and dropdown with current data."""
    try:
        # Get real data
        daily = get_daily_earnings()
        weekly = get_weekly_earnings()
        monthly = get_monthly_earnings()

        # Update menu bar title
        self.title = f"💰 ${daily['total']:.0f}"

        # ... rest of the code ...

    except Exception as e:
        # Show error in menu bar
        self.title = "💰 Error"
        self.menu.clear()
        self.menu.add(f"Error: {str(e)}")
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("⟳ Retry", callback=self.refresh))
```

### Step 2.5: Test with real data

```bash
source venv/bin/activate
python menubar_app.py
```

Should now show your actual Toggl earnings!

---

## Project Structure

```
freelance-workflow/
├── toggl_earnings.py          # Original CLI script
├── menubar_app.py             # Menu bar app (Phase 1 & 2)
├── mock_data.py               # Mock data (Phase 1 only)
├── toggl_data.py              # Real Toggl integration (Phase 2)
├── requirements.txt           # Add: rumps>=0.4.0
├── .env                       # API credentials
└── venv/                      # Virtual environment
```

## Quick Start Checklist

### Phase 1: Mock UI ✓
- [ ] Install rumps: `pip install rumps`
- [ ] Create `mock_data.py` with hardcoded data
- [ ] Create `menubar_app.py` with UI
- [ ] Run and test: `python menubar_app.py`
- [ ] Iterate on UI until you like it

### Phase 2: Real Data ✓
- [ ] Extract functions from `toggl_earnings.py` to `toggl_data.py`
- [ ] Update `menubar_app.py` to use real data
- [ ] Add auto-refresh timer
- [ ] Add error handling
- [ ] Test with real Toggl data

## Expected Timeline

- **Phase 1:** 30 minutes (UI with mock data)
- **Phase 2:** 1-2 hours (real integration)
- **Total:** 1.5-2.5 hours from zero to working app

## Next Steps After This Works

Once you have it working:
1. **Package as .app** (optional - can run from terminal for now)
2. **Add settings dialog** for API token
3. **Add notifications** for daily goals
4. **Add more features** (start/stop timer, etc.)

But first: Get Phase 1 working in the next 30 minutes!

## Tips

- **Don't skip Phase 1** - Validate the UI first with mock data
- **Keep it simple** - The mock approach lets you iterate fast
- **Test often** - Run `python menubar_app.py` after each change
- **Use notifications** - Great for debugging without print statements

## Troubleshooting

**Menu bar icon doesn't appear:**
```bash
# Make sure you're in the virtual environment
source venv/bin/activate

# Check rumps is installed
pip list | grep rumps
```

**App crashes:**
```bash
# Run from terminal to see errors
python menubar_app.py
```

**Want to quit the app:**
- Click menu bar icon → Quit
- Or: `killall Python` from terminal

---

Ready to start? Begin with Phase 1 - create `mock_data.py` and `menubar_app.py`!
