# Freelance Tracker

A macOS menu bar app that tracks your daily, weekly, and monthly freelance earnings from Toggl Track in real-time.

## Features

- 💰 **Live earnings display** in menu bar (daily, weekly, monthly)
- 🪟 **Resilient dashboard startup** with automatic fallback to the classic dropdown if the rich WebKit panel is unavailable
- ↕️ **Adaptive dashboard height** so short project lists do not leave large empty space
- 📂 **Collapsible dashboard sections** for Today, This Week, and This Month, with expanded/collapsed state saved across launches
- 📋 **Click-to-copy today descriptions** from expanded time blocks in the Today breakdown
- 📉 **This Week collapsed by default** to keep the dashboard tighter unless you need that breakdown
- 🆕 **Dashboard update shortcut** in the main popover footer
- ⚙️ **Settings button** in the main popover, with API audit log access under the Preferences `Advanced` tab
- 🔄 **Auto-refresh** every 30 minutes
- 📈 **Month projection** based on pace, accounting for planned days off
- 💼 **Project definitions** with billing types: hourly, capped hourly, and fixed monthly
- 🔁 **Carryover tracking** for capped and fixed-monthly projects across months
- 🕐 **Last update timestamp**
- 🧹 **Clear All Caches** action in the dashboard refresh menu for heavy cache recovery
- 📂 **Open Cache Folder** action in the dashboard refresh menu for quick Finder access
- 📤 **Export/Invoice drop-up** in the dashboard footer — choose `Export CSV`, `Create Stripe Invoice`, or `Open Upwork Diary`. CSV and Stripe flows include `This week`, `Last week`, `Last month`, `Year to date`, plus custom dates
- 🔌 **Integrations settings** so users can update their Toggl API token, workspace id, and Stripe API key after installation, plus save optional Upwork contract ids per project
- 💾 **Smart caching** to minimize Toggl API calls
  Dashboard, CSV export, Stripe draft invoices, capped billing-cycle calculations, and auto carryover all reuse the same shared day-based Toggl entry cache
- 🚀 **Runs as macOS system service** (auto-starts on login, restarts on crash)
- 📍 **Standard macOS storage** locations

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/dennislysenko/freelance-tracker/main/install.sh | bash
```

You'll be prompted for your [Toggl API token](https://track.toggl.com/profile). The app installs to `~/.freelance-tracker` and starts automatically on login.

**Prerequisites:** macOS, Python 3, Git. Install missing tools with [Homebrew](https://brew.sh).

## Updating

Click **🆕 Update App** in the menu bar — a small progress window will show the update steps and restart the app automatically.

Or from the terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/dennislysenko/freelance-tracker/main/update.sh | bash
```

<details>
<summary>Manual setup (for contributors)</summary>

### 1. Get Your Toggl API Token

1. Go to https://track.toggl.com
2. Log in to your account
3. Click your **profile picture** (bottom left)
4. Click **Profile Settings**
5. Scroll down to **API Token**
6. Click to reveal and **copy your API token**

### 2. Clone & Install

```bash
git clone https://github.com/dennislysenko/freelance-tracker.git
cd freelance-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure API Token

```bash
cp .env.example .env
nano .env
```

```
TOGGL_API_TOKEN=your_token_here
TOGGL_WORKSPACE_ID=your_workspace_id  # Optional, auto-detected
```

### 4. Set Project Rates in Toggl

This app calculates earnings based on hourly rates set in Toggl:

1. Go to https://track.toggl.com
2. Navigate to **Projects**
3. For each billable project, set:
   - **Hourly rate** (e.g., $150/hr)
   - Mark as **Billable**

### 5. Test It

```bash
source venv/bin/activate
python menubar_app.py
```

Check your menu bar for the 💰 icon showing your daily earnings!

### 6. Install as System Service

```bash
./install_service.sh
```

Now it will:
- ✅ Start automatically on login
- ✅ Auto-refresh every 30 minutes
- ✅ Restart if it crashes
- ✅ Run in the background

</details>

## Management Scripts

### Restart the App
```bash
./restart_service.sh
```
Use this after making code changes.

### Check Status
```bash
./status_service.sh
```
Shows: running status, memory usage, uptime, logs, cache size.

### View Logs
```bash
./logs.sh              # View output log (live)
./logs.sh errors       # View error log (live)
./logs.sh all          # View both logs (live)
./logs.sh clear        # Clear all logs
```

### Uninstall
```bash
./uninstall_service.sh
```

## User Interface

The 💰 menu bar title is always visible. Clicking it opens the **dashboard popover** — a WebKit-rendered panel implemented in `dashboard_panel.py`. This is the canonical UI: TODAY / This Week / THIS MONTH sections (each collapsible with persisted state), monthly project progress bars with pacing markers, month projection, the footer Refresh / Settings / Update / Quit / Export/Invoice controls, and the rate-limit / refresh-error inline states. The footer is rendered as a bottom drawer flush with the sheet while the rest of the content continues to scroll.

Within the Today section, expanding a project reveals its time blocks. Clicking a time-block description copies just that description to the clipboard and briefly swaps the label to `Copied`.

`Refresh` keeps the dashboard and billing outputs in sync: it invalidates the shared day-based Toggl entry shards for the visible dashboard ranges plus active capped billing-cycle ranges, so exporting or invoicing after a refresh uses the same underlying entry data the dashboard just rendered. The refresh drop-up also includes `Clear All Caches`, which removes all cached Toggl entry shards, project metadata, and legacy cache files before repopulating the current dashboard state, plus `Open Cache Folder`, which reveals `~/Library/Caches/TogglMenuBar/` in Finder.

If the WebKit bridge is unavailable on a given machine (missing PyObjC, etc.), the app falls back to a minimal rumps dropdown menu so it still launches. The fallback is intentionally bare-bones and is **not** where new features live — see `CLAUDE.md` for the policy.

### Month Projection

The app automatically calculates your projected monthly earnings based on:
- **Worked days**: Days with earnings-contributing time entries this month (Toggl billable rates or configured retainer overrides)
- **Workable days**: Business days minus vacation days (default: 4 days)
- **Daily average**: Current earnings ÷ worked days
- **Projection**: Daily average × workable days

Example: If you earned $1,000 in 4 worked days, with 20 business days and 4 vacation days (16 workable days), your projection is $4,000.

To adjust vacation days, edit preferences:
```json
{
  "vacation_days_per_month": 4  // Change this to your typical vacation days
}
```

### Integrations

Open **Settings → Integrations** to update credentials after install:
- `TOGGL_API_TOKEN`
- `TOGGL_WORKSPACE_ID`
- `STRIPE_API_KEY`

The same tab also lets you map Toggl projects to Stripe customers by picking from the live Stripe customer list by name. If you try to create a Stripe invoice for an unmapped project, the dashboard will ask you to pick a customer right after you choose the date range, then save that association for next time.

That project-mapping grid also has an **Upwork Contract ID** column. Add the Upwork hourly contract id for any project where you want a fast work-diary shortcut. The dashboard can also save that id inline the first time you click an unmapped project from `Export/Invoice → Open Upwork Diary`, then open the diary immediately.

The current Upwork integration is intentionally a deep-link shortcut, not a direct API write. Upwork’s documented GraphQL docs expose work-diary reads, but they do not document a manual-time creation mutation, so the app currently opens the contract-specific work diary URL instead of attempting an unsupported write.

### Reordering Menu Bar Icons

To position the 💰 icon in your preferred spot:

Hold `Cmd` and drag the icon left or right in the menu bar.

## Files

```
freelance-workflow/
├── menubar_app.py              # Main menu bar app
├── stress_storage.py           # SQLite database layer
├── toggl_data.py               # Toggl API integration
├── toggl_earnings.py           # CLI version
├── preferences.py              # Settings management
├── mock_data.py                # Test data (not used in production)
│
├── install_service.sh          # Install as system service
├── uninstall_service.sh        # Remove system service
├── restart_service.sh          # Restart the app
├── status_service.sh           # Check app status
├── logs.sh                     # View logs
│
├── com.freelancetracker.menubar.plist  # LaunchAgent config
├── .env                        # API credentials (gitignored)
├── requirements.txt            # Python dependencies
│
└── docs/
    ├── menubar-app-plan.md     # Implementation plan
    ├── api-integration.md      # API reference
    ├── architecture.md         # Technical docs
    └── service-setup.md        # Service installation guide
```

## Storage Locations

Following macOS conventions:

```
~/Library/Application Support/TogglMenuBar/
├── preferences.json            # User settings
└── freelance_tracker.db        # SQLite database (stress events, time entries)

~/Library/Caches/TogglMenuBar/
├── entries/by_day/*.json       # Shared cached Toggl time-entry shards by local day
└── *.json                      # Cached API metadata / legacy compatibility files

~/Library/Logs/
├── freelancetracker-output.log # App output
└── freelancetracker-error.log  # Errors
```

## Common Tasks

### After Making Code Changes
```bash
./restart_service.sh
```

### Check If It's Running
```bash
./status_service.sh
```

### See What It's Doing
```bash
./logs.sh
```

### Something Wrong?
```bash
# Check error log
./logs.sh errors

# Restart the service
./restart_service.sh

# Still not working? Reinstall
./install_service.sh
```

### Clear Cache
```bash
rm -rf ~/Library/Caches/TogglMenuBar/*
./restart_service.sh
```

## Preferences

Edit `~/Library/Application Support/TogglMenuBar/preferences.json`:

```json
{
  "refresh_interval": 1800,       // 30 minutes
  "show_notifications": true,
  "daily_goal": 400,
  "weekly_goal": 2000,
  "monthly_goal": 8000,
  "show_hours": true,
  "menu_bar_format": "💰 ${total:.0f}",
  "cache_ttl_projects": 86400,    // 24 hours
  "cache_ttl_today": 1800,        // 30 minutes
  "project_targets": {            // Optional monthly hour targets
    "Client A": 80,               // Target 80 hours/month
    "Globex": 30          // Target 30 hours/month
  },
  "retainer_hourly_rates": {      // Optional $/hr overrides for retainers/non-billable projects
    "Client A Retainer": 150,     // Used when Toggl project has no billable rate
    "Ops Retainer": 95
  },
  "vacation_days_per_month": 4    // vacation days to exclude from projection
}
```

After editing, restart: `./restart_service.sh`

### Project Targets

Set monthly hour targets for each project to track progress:

1. Edit preferences: `nano ~/Library/Application\ Support/TogglMenuBar/preferences.json`
2. Add `project_targets` with your project names and target hours
3. Restart: `./restart_service.sh`

The menu will now show progress like:
```
Client A: 45.2h / 80h (57%)
Globex: 22.5h / 30h (75%)
```

Projects with configured monthly hour targets stay visible in the dashboard's "This Month" tracked-hours section even when they are still at `0.0h`, so you can see outstanding retainers before logging the first entry.

### Retainer Dollar Tracking

If a retainer project in Toggl has no billable hourly rate, add it to `retainer_hourly_rates`:

1. Edit preferences: `nano ~/Library/Application\ Support/TogglMenuBar/preferences.json`
2. Add the exact Toggl project name under `retainer_hourly_rates` with your effective hourly value
3. Restart: `./restart_service.sh`

Example:
```json
{
  "retainer_hourly_rates": {
    "Acme Retainer": 150
  }
}
```

After this, time logged to that project contributes to menu bar dollar totals and projection calculations.

You can manage these rates in the app via `⚙️ Edit Preferences` → `Retainer Rates`.

## CLI Version

Still works! For detailed reports:

```bash
source venv/bin/activate

# Daily report
python toggl_earnings.py

# Weekly report
python toggl_earnings.py --period weekly

# Monthly report
python toggl_earnings.py --period monthly
```

## Troubleshooting

**App not showing in menu bar?**
```bash
./status_service.sh  # Check if running
./logs.sh errors     # Check for errors
```

**"Service not running"?**
```bash
./install_service.sh
```

**Auto-refresh not working?**
- Check logs: `./logs.sh`
- Should see "Auto-refreshing at [time]" every 30 minutes

**Wrong earnings amount?**
```bash
# Clear cache and restart
rm -rf ~/Library/Caches/TogglMenuBar/*
./restart_service.sh
```

## Development

### Run in Development Mode
```bash
source venv/bin/activate
python menubar_app.py
# Ctrl+C to stop
```

### Install/Update Dependencies
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### Check Service Status
```bash
launchctl list | grep freelancetracker
```

## Credits

- Menu bar app built with [rumps](https://github.com/jaredks/rumps)
- Uses [Toggl Track API v9](https://engineering.toggl.com/docs/)

---

**Need help?** Check the docs in the `docs/` folder.
