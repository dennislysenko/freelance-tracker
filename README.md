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
- ⚙️ **Settings** opens directly in the dashboard popover (`⋯` → `Settings`) with all six tabs (Caching, Work Planning, Projects, Billing, Integrations, Advanced). Dynamic add/remove rows, masked credential inputs with a show/hide toggle, and inline validation errors. API audit log access is under `Advanced`.
- 🔄 **Auto-refresh** every 30 minutes
- 📈 **Month projection** based on pace, accounting for planned days off
- 🧾 **Invoice-date pacing** for capped projects — paces from your last bill, not the 1st of the month
- 💼 **Project definitions** with billing types: hourly, capped hourly, and fixed monthly
- 🔁 **Carryover tracking** for capped and fixed-monthly projects across months
- 🚫 **Non-billable time** — entries you mark non-billable in Toggl (say, fixing your own bug) are shown next to your hours ("+1.5h non-billable") but never count toward earnings, targets, caps, carryover, the projection, CSV exports, invoices, or Gusto
- 🕐 **Last update timestamp**
- 🧹 **Clear All Caches** action in the dashboard refresh menu for heavy cache recovery
- 📂 **Open Cache Folder** action in the dashboard refresh menu for quick Finder access
- 📤 **Export/Invoice drop-up** in the dashboard footer — choose `Export CSV`, `Create Stripe Invoice`, or `Push to Upwork`. CSV and Stripe flows include `This week`, `Last week`, `Last month`, `Year to date`, plus custom dates
- ⏰ **Billing reminders** in Settings → `Billing` for local notifications on a weekly (`Friday 14:00`) or monthly (`Day 15`, `Last day of month`, `2nd-to-last day`) schedule
- 🗣️ **Natural-language time logging** (optional, bring your own OpenAI key) — type "1 hour of Acme retainer at 9am the past 2 days, no note", confirm the proposed entries, and they are written to Toggl. Also answers read questions about your hours from cache
- 🔌 **Integrations settings** so users can update their Toggl API token, workspace id, Stripe API key, Google Calendar ICS URL, and OpenAI API key after installation, plus save optional Upwork contract ids per project
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
- **Workable days**: Business days minus days off — from your Google Calendar when connected (see below), otherwise the flat vacation-days estimate (default: 4 days)
- **Daily average**: Current hourly earnings ÷ worked days
- **Projection**: Fixed monthly amounts + rev share + hourly earned so far + daily average × workable days still ahead. It never drops below what you've already earned, and on the last workday it equals your actual month.

For `hourly_with_cap` projects that set `last_billed_date`, the dashboard switches to billing-cycle pacing instead of calendar-month pacing: unbilled hours are measured from `last_billed_date + 1 day`, and the pace marker / capped projection run through the end of the following month. This keeps billing-ahead retainers from looking artificially behind just because the current calendar month is mostly over.

Example: If you earned $1,000 hourly in 4 worked days and have 12 workable days left (no days off among them), your hourly projection is $1,000 + $250 × 12 = $4,000.

**Monthly rev share**: If a contract pays you a variable rev share that you know (at least approximately) at the start of the month, enter it under **Settings → Work Planning → This Month's Rev Share**. The amount counts in full in **This Month** (like a fixed retainer's fee), where it shows as a "Rev share" row, and is added to that month's projection as guaranteed income — it is *not* extrapolated by pace — appearing as a "$X rev share (this month)" line in the projection breakdown. It is stored per month, so it never carries a stale value into the next month: each new month starts blank until you enter the new amount (enter `0` to clear it). No Toggl API calls are made.

**Projection diagnostics (debugging a weird estimate)**: If your on-pace projection looks wrong, open **Settings → Advanced → Copy Projection Diagnostics**. It copies an **anonymized** JSON snapshot of the projection math to your clipboard — your config plus the fully computed month (including the raw intermediate terms the pace is derived from) — that you can paste to someone (or back to Claude) to figure out why. Project names are replaced with labels (`Project A`, `Project B`, …) and every rate/amount is normalized so absolute dollars are removed while the ratios that drive the math are preserved. No API tokens or client mappings are included.

**Google Calendar days off**: Instead of a flat vacation estimate, the projection can count your *actual* days off from Google Calendar:

1. In Google Calendar settings, under **Settings for my calendars** (left sidebar), choose the calendar you put your vacation days on → scroll to **Integrate calendar** → copy the **Secret address in iCal format**.
2. Paste it into **Settings → Integrations → Google Calendar ICS URL** (stored in the local `.env`, never in `preferences.json`).
3. Optionally tune **Settings → Work Planning → Days Off Keywords** (comma-separated; defaults include `vacation`, `day off`, `ooo`, `out of office`, `ceo day`, `holiday`, `pto`).

Any business day with an event whose title contains one of the keywords counts as a day off — recurring events (like a weekly "CEO Day") count once per occurrence, and multi-day vacations count each covered weekday. The projection line shows "N days off excluded (from calendar)" when the calendar is driving the count. The feed is fetched at most every 6 hours and cached; if it can't be fetched and no cache exists, the app falls back to `vacation_days_per_month`.

To adjust the fallback vacation days, use **Settings → Work Planning → Vacation Days/Month** or edit preferences:
```json
{
  "vacation_days_per_month": 4  // Used only when no days-off calendar is configured
}
```

### Integrations

**Settings → Integrations** shows a grid of integration cells grouped by
purpose, each marked **✓ Active** or **Configure integration** at a glance.
Click one to drill into just that integration's fields and setup notes; the
back arrow returns to the grid. The tab you're on and the integration you have
open are both remembered, so if the popover dismisses while you're off grabbing
a key in your browser, reopening puts you right back where you were.

Credentials you can set after install:
- `TOGGL_API_TOKEN`
- `TOGGL_WORKSPACE_ID`
- `STRIPE_API_KEY`
- `GOOGLE_CALENDAR_ICS_URL` (optional — powers calendar-driven days off in the month projection)
- `OPENAI_API_KEY` (optional — powers natural-language time logging; see below)

The same tab also lets you map Toggl projects to Stripe customers by picking from the live Stripe customer list by name. If you try to create a Stripe invoice for an unmapped project, the dashboard will ask you to pick a customer right after you choose the date range, then save that association for next time.

That project-mapping grid also has an **Upwork Contract ID** column. Add the Upwork hourly contract id for a project billed through Upwork (or save it inline the first time you click the project under `Export/Invoice → Push to Upwork`).

### Upwork — add your hours as manual time

Upwork has no API for adding time and doesn't allow tools that drive the site, so the app does the arithmetic and a small browser extension (Chrome, or a Firefox-based browser such as Zen) fills the form for you:

- For each linked project the app works out the week's manual-time entries from Toggl in Upwork's **10-minute steps**: same-day entries that touch become one, starts land on 10-minute marks, and a week never adds up to more than Toggl (lone 15-minute entries come out 10, 20, 10, 20). Non-billable time is never included.
- `Export/Invoice → Push to Upwork` lists linked projects first with what is still to add, and opens the work diary on the first day that needs it. The extension shows a small panel there: click **Fill**, check the form, click **Save**. It never saves for you. Each saved entry is recorded so it's never offered again.
- The first time, the same menu walks you through installing the extension and pasting its pairing code. In Chrome: Developer mode → **Load unpacked** → `upwork_extension/`. In Zen or Firefox (set the browser in Settings → Integrations → Upwork): `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on…** → `upwork_extension/manifest.json`, then turn on its two site permissions in `about:addons` → the extension → **Permissions**. Firefox-based browsers only keep an unsigned add-on until you quit, so load it again after a browser restart, or sign it once with `python sign_upwork_extension.py` (Mozilla API keys in the Keychain; see `upwork_extension/README.md`) and install the signed `.xpi` for good.
- Upwork locks each week on **Monday 12:00 UTC**. The project's monthly row shows "↳ Upwork: 2h 10m to add before Mon 7:00 AM", the `Export/Invoice` button gets an orange dot, and you get a reminder on Friday afternoon and Sunday evening while anything is waiting (Settings → Integrations → Upwork to turn reminders off or change the browser).
- If you change an entry in Toggl after adding it to Upwork, the app flags it for you to fix in Upwork; it never edits Upwork time.
- Agents logging time through the MCP server are told to use 10-minute steps for Upwork projects (15 for everything else).

### Gusto — push contractor hours automatically

If you are paid as a contractor through Gusto, the app can copy one Toggl
project's hours into your Gusto timesheet: each Toggl entry becomes a Gusto
shift on its real date, with the description as the note.

1. Settings → Integrations → **Gusto**: tick **Push hours to Gusto**, pick the
   Toggl project, Save.
2. Click **Log in to Gusto**. A Chrome window opens; log in with your passkey.
   Leave the window open (closing it ends Gusto's session).
3. Every Monday at 9:00 the previous week is pushed. On the last business day of
   the month (when the month's hours are due by 3 PM), the app pushes again at
   1 PM, including that day's finished entries, and retries until 3 PM. It also
   pushes at 1 PM the business day before, so a missed cutoff-day run leaves
   only that day's hours outstanding. You can
   also push any time from **Export/Invoice → Push to Gusto**. An orange dot on
   Export/Invoice means last week or the month isn't in Gusto yet, or something
   needs you (passkey, a Toggl entry edited after it was pushed).

Only billable entries are pushed; an entry marked non-billable after it was
pushed is flagged for you to fix in Gusto.

If the project has a monthly hour cap (say 30h), the app never pushes past it.
A Push to Gusto click includes today's finished entries, and the entry that
crosses the cap is split: the part up to the cap goes to Gusto, the rest is
held back (Toggl isn't changed), and you get an alert saying how much. The
project's monthly row shows when the cap was reached ("cap reached Wed Nov 25,
2:00 PM · 0.25h over"). Move the extra to next month in Toggl, or send it anyway with
**Export/Invoice → Push surplus to Gusto**. Under the project's bar the
dashboard always shows how many hours are in Gusto for the month, as of the
last push ("Gusto: 14.5h of 30h pushed for Nov · as of Mon Nov 16, 9:00 AM").

The app only ever adds shifts; it never edits or deletes anything in Gusto. It
uses Gusto's web app in a visible Chrome window (Gusto has no API contractors
can use), so a Gusto change can break it; the sync then fails with an error
rather than logging wrong hours. Terminal: `python gusto_sync.py dry-run`
shows what would be pushed without sending anything.

### Agents (MCP) — let Claude Code or Codex read your numbers

The app ships an [MCP](https://modelcontextprotocol.io) server so a coding agent can
answer "how is my month going, and where should I put more hours?" from your real
Toggl data: hours, earnings, the month projection, per-project pacing, and your
billing rules. Reading never calls Toggl — it reads the app's cache.

Optionally, the agent can also **log, edit, or delete time** for you. That is a
separate switch, off by default, and it works on **one entry at a time**: the agent
first shows you exactly what it would change and has to come back with your
confirmation before anything is written.

1. Settings → Integrations → **Agents (MCP)** → tick **Enable MCP server** → Save.
2. Click **Copy Claude Code command** (or **Copy Codex config**) and run / paste it.
3. In your agent: "Check in on my freelance month."

If you push hours to Gusto, the agent can also check what is actually in your
Gusto timesheet against Toggl ("are all my October hours in Gusto?"). That read
opens the Gusto Chrome window and may ask for your passkey; it never changes
anything in Gusto.

Full setup, tool list, and troubleshooting: [docs/mcp-agents.md](docs/mcp-agents.md).

### Natural-Language Time Logging (optional)

Bring your own OpenAI key and you can type entries in plain English instead of
filling in a form:

```
put 1 hour of Globex retainer at 9am for the past 2 days, no note
```

The app proposes the entries it would create — one row per day, with the
resolved date, time, duration, and project — and **writes nothing until you hit
Apply**. If a proposal overlaps time you already logged, it says so, so
re-running the same command does not silently double-log you.

You can also ask read questions ("how many hours on Acme this month?"). Those
are answered from the existing local cache and cost **zero Toggl API calls**.

Costs are billed to your own OpenAI account and are tiny — a few hundred tokens
per command, realistically well under $1/month.

**How it looks.** The input sits at the top of the dashboard. Submitting takes
you to the Assistant view, which shows what it plans to log:

```
Proposed 2 entries · 1 overlap existing time
 [x] Mon Aug 3 · 9:00am-10:00am    Globex Retainer · 1.00h
 [ ] Tue Aug 4 · 9:00am-10:00am    Globex Retainer · 1.00h
     Overlaps time already logged
                             [ Dismiss ]  [ Apply ]
```

Rows that overlap time you already logged start **unchecked** and flagged, so
re-running the same command does not double-log you — but you can tick them
back on if you really did work two things at once. Apply writes only the
checked rows.

**Setting up a tightly scoped key.** The point here is to bound the blast radius
if the key ever leaks:

1. At [platform.openai.com](https://platform.openai.com), go to **Settings →
   Projects** and create a new project (e.g. `freelance-tracker`). A key can
   never reach outside the project that owns it.
2. In that project's **Limits**, set a hard monthly budget — **$5** is generous
   — plus an email alert. This is the control that actually matters: a leaked
   key with a $5 cap is a $5 problem.
3. Under **API keys**, create a key owned by that project (*not* a user key or
   a service-account key) with **Restricted** permissions. Set **Model
   capabilities** to **Request** — expanding that group shows it grants
   **Write** on `/v1/responses`, which is the only endpoint this app calls —
   and leave everything else (Assistants, Files, Fine-tuning, Vector stores,
   Admin) on **None**.
4. Paste it into **Settings → Integrations → OpenAI API Key**. Saving verifies
   the key with a live call, so you find out immediately if it is wrong or has
   no billing credit attached.

> **Note:** a brand-new OpenAI account with no payment method produces a valid
> key that still fails with "no available credit". The app names that case
> specifically rather than reporting a generic error.

**Privacy.** When you use this feature, what you type plus your Toggl *project
names* are sent to OpenAI under your own account and its data-retention terms.
Project names are frequently client names, so this is worth a conscious choice.
Nothing is sent unless you use the feature, and no other credential ever leaves
your Mac. Leave the key blank and the feature is simply unavailable — everything
else works exactly as before.

The model is pinned to `gpt-4.1-mini`. Set `OPENAI_MODEL` in `.env` to use a
different one.

### Reordering Menu Bar Icons

To position the 💰 icon in your preferred spot:

Hold `Cmd` and drag the icon left or right in the menu bar.

## Files

```
freelance-workflow/
├── menubar_app.py              # Main menu bar app
├── dashboard_panel.py          # WebKit popover (canonical UI)
├── settings_view.py            # In-popover preferences view (HTML/CSS/JS)
├── settings_handler.py         # Save/validate logic for the in-popover view
├── preferences_window.py       # Deprecated AppKit preferences window (fallback only)
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
├── com.freelancetracker.menubar.plist.template  # LaunchAgent config (paths filled in by install_service.sh)
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
    "Acme Inc": 80,         // Target 80 hours/month
    "Hooli": 30        // Target 30 hours/month
  },
  "retainer_hourly_rates": {      // Optional $/hr overrides for retainers/non-billable projects
    "Retainer Project": 150,      // Used when Toggl project has no billable rate
    "Support Retainer": 95
  },
  "billing_reminders": [
    {
      "enabled": true,
      "project_name": "Acme Inc",
      "task": "invoice",
      "weekday": "friday",              // weekly schedule
      "time": "14:00"
    },
    {
      "enabled": true,
      "project_name": "Retainer",
      "task": "invoice",
      "day_of_month": -1,               // monthly: -1=last day, -2/-3 from end, 1-28 for nth day
      "time": "09:00"
    }
  ],
  "vacation_days_per_month": 4    // vacation days to exclude from projection
}
```

After editing, restart: `./restart_service.sh`

Billing reminders are local macOS notifications generated by the app itself while it is running. They do not call Toggl, Stripe, or any external scheduling service.

Use the **Send Test Notification** button on the Preferences → `Billing` tab to fire a sample reminder immediately and confirm macOS notification permissions are granted.

Reminders post under the **Python** app identity (the LaunchAgent runs the interpreter directly). If you use a macOS Focus, add `Python` to that Focus's allowed apps — otherwise reminders are silently suppressed. Full Do Not Disturb suppresses them entirely.

### Project Targets

Set monthly hour targets for each project to track progress:

1. Edit preferences: `nano ~/Library/Application\ Support/TogglMenuBar/preferences.json`
2. Add `project_targets` with your project names and target hours
3. Restart: `./restart_service.sh`

The menu will now show progress like:
```
Acme Inc: 45.2h / 80h (57%)
Hooli: 22.5h / 30h (75%)
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
