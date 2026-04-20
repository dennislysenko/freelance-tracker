# Source of Truth - Freelance Tracker Features & Benefits

**Last Updated:** 2026-04-16

Master reference for all features and benefits. Agents must update this file when adding or modifying functionality.

---

## What It Does

Freelance Tracker is a macOS menu bar app that shows real-time Toggl earnings at a glance. Click the menu bar icon to open the dashboard popover with detailed breakdowns of today, this week, this month, and projected monthly earnings.

## Primary Interface

The **WebKit dashboard popover** (`dashboard_panel.py`) is the canonical user interface — every user-facing feature documented in this file lives there. The rumps dropdown menu in `menubar_app.py` is a degraded fallback that only renders when the WebKit bridge is unavailable, and is intentionally feature-incomplete. New features must be added to the dashboard popover, not the fallback menu.

---

## Core Features

### Real-Time Earnings Display
- Menu bar icon showing daily total (e.g., "💰 $400")
- Click to see detailed breakdown
- Rich popover dashboard uses WebKit when available; if the WebKit bridge is missing, the app falls back to the classic dropdown menu instead of failing to launch
- Dashboard popover auto-sizes to the current content for short project lists, while preserving scrolling for taller dashboards
- `This Week` is collapsed by default; section collapse state is then persisted per user across launches
- Preferences are edited in the dashboard popover itself via the `⋯` → `Settings` menu. Clicking opens an in-popover Preferences view with the same six tabs (`Caching`, `Work Planning`, `Projects`, `Billing`, `Integrations`, `Advanced`) and the back chevron returns to the dashboard. The native AppKit Preferences window is retained only as the fallback path for when the WebKit bridge is unavailable.
- Preferences view relaxes the native quirks where doing so has no persistence impact: add/remove rows instead of fixed-count grids, masked credential inputs with a show/hide eye toggle, and inline validation errors at the top of the panel in addition to modal alerts for bulk issues
- Reminder configuration lives in the `Billing` tab and diagnostics stay in `Advanced`
- Dashboard footer provides a `Refresh` split button with a drop-up (`Refresh Data`, one-off `Refresh Projects`, `Clear All Caches`, or `Open Cache Folder`), `Settings`, `Update`, and `Quit`
- Dashboard footer provides a single `Export/Invoice` forced drop-up that branches into `Export CSV`, `Create Stripe Invoice`, or `Open Upwork Diary`
- Dashboard footer is rendered as a bottom drawer flush with the sheet edge, while the dashboard content scrolls above it with enough bottom padding to stay readable
- Dashboard shows a rate-limit warning when cached data is being used and an inline retry state when refresh fails
- Local billing reminders can be configured in Preferences to fire weekly macOS notifications without touching Toggl or Stripe APIs
- Auto-refresh every 30 minutes
- Manual refresh available

### Daily View
- Today's total earnings and hours
- Per-project breakdown
- In the Today section, expanded time-block descriptions can be copied to the clipboard by clicking the description text; the dashboard briefly shows `Copied` inline as feedback
- Billable projects show earnings and hours
- Projects with a defined `billing_type` contribute earnings even if not billable in Toggl

### Weekly & Monthly Summaries
- Current week total with per-project breakdown in the dashboard
- Current month total
- Monthly hours breakdown by project
- Today / This Week / This Month sections can each be collapsed or expanded from the dashboard, and their state persists across app relaunches

### Month Projection
- Intelligent forecast based on current performance
- Accounts for business days vs worked days
- Configurable vacation days
- Formula for hourly projects: `(earnings ÷ worked days) × workable days`
- `fixed_monthly` projects are always treated as guaranteed income — only `hourly` and `hourly_with_cap` earnings are extrapolated by pace: `fixed_monthly_total + project((variable earnings) by pace)`

### Project Definitions

Every project can have an optional definition in the `projects` preference key. Projects without a definition default to `billing_type: hourly` using the rate from Toggl.

#### Billing Types

**`hourly`** (default)
- Standard Toggl hourly rate
- No cap, no carryover
- Projection: extrapolated by pace

**`hourly_with_cap`**
- Billed at hourly rate up to a monthly cap (hours × rate)
- Under cap: bill actual hours worked (no penalty)
- Over cap: overflow hours carry forward as a credit to next month, reducing available cap
- Projection: `min(pace_projected_hours, cap_hours) × rate` — projection line notes "(capped at $X)" when clamped
- Monthly progress bar (without `last_billed_date`): numerator = current month hours, denominator = `cap_hours - carryover` (carryover adjusts the denominator)
- Optional `last_billed_date` (YYYY-MM-DD): when set, unbilled hours are counted from that date + 1 day through today, crossing month boundaries. Replaces carryover-based tracking for projects with non-calendar billing cycles. Fetches the full date range from the Toggl API (1 call, cached daily).
  - Monthly progress bar: numerator = all unbilled hours since last_billed_date, denominator = raw `cap_hours` (no carryover adjustment)
  - Projection: `min(unbilled_hours + daily_avg_since_date × remaining_biz_days, cap_hours) × rate`
  - Carryover store is cleared when last_billed_date is saved; manual carryover field hidden in UI
- **Timesheet last day**: shown below the progress bar when over 100%. Walks through actual time entries chronologically and finds the last date where cumulative hours were at or just under the cap. Displayed as "↳ timesheet last day: Mar 15". Tells you what date to put on a timesheet as your last worked day to bill as close to 100% as possible without exceeding the cap. Works for both `last_billed_date` and calendar-month modes.

**`fixed_monthly`**
- Guaranteed monthly amount regardless of hours worked
- Three hour-tracking modes:
  - `hour_tracking: required` — expected hours per month; over/under rolls forward as carryover balance
  - `hour_tracking: soft` — display-only target; effective hourly rate = `monthly_amount / target_hours`; no carryover. Monthly earnings capped at `monthly_amount`. Daily/weekly show $0 for hours beyond `target_hours` (checked against monthly total).
  - `hour_tracking: none` — freeform; no hour tracking
- Effective hourly rate for daily/weekly display: `monthly_amount / target_hours` (required/soft), or `monthly_amount / working_days` per day (none)
- These projects are assumed to not have a billable rate configured in Toggl

#### Project Definition Schema

```json
"projects": {
  "Client A": {
    "billing_type": "hourly_with_cap",
    "hourly_rate": 150,
    "cap_hours": 20,
    "last_billed_date": "2025-02-15"  // optional — unbilled hours counted from Feb 16 onward
  },
  "Client B": {
    "billing_type": "fixed_monthly",
    "monthly_amount": 2000,
    "hour_tracking": "required",
    "target_hours": 10
  },
  "Client C": {
    "billing_type": "fixed_monthly",
    "monthly_amount": 3000,
    "hour_tracking": "soft",
    "target_hours": 30
  },
  "Client D": {
    "billing_type": "fixed_monthly",
    "monthly_amount": 4000,
    "hour_tracking": "none"
  }
}
```

#### Carryover

Carryover applies to `hourly_with_cap` and `fixed_monthly / hour_tracking: required`. Balance is stored in `~/Library/Application Support/TogglMenuBar/retainer_carryover.json` and displayed in the monthly hours progress bar. Auto-calculated from the shared cached Toggl entry data for the previous month; can also be manually set or overridden via the Projects tab in preferences (the "Feb carryover h" field shown for applicable billing types). Manual overrides are preserved and are not overwritten by auto recomputation:

```
Client B: 8.5h / 12h (71%)     ← denominator adjusted by carryover
[████████░░░░░░░░]
↳ -2h carryover from Feb
```

| Billing type | Under | Over |
|---|---|---|
| `fixed_monthly / required` | hours owed roll forward (target increases next month) | hours credited forward (target decreases next month) |
| `hourly_with_cap` | bill actual hours (no penalty) | overflow hours credited to next month (cap decreases next month) |

#### Project Hour Targets
- Set monthly hour targets for any project (also configurable via `project_targets` for non-project-definition projects)
- Visual progress bars with Unicode blocks on separate line
- Progress tracking with percentages inline with hours
- Example: "Client A: 45.2h / 80h (57%)" followed by "[██████░░░░░░]" on next line
- In the dashboard month section, target-bearing projects stay visible at `0.0h / target` even before any hours are logged that month
- For `fixed_monthly / required` and `hourly_with_cap`, the target denominator is adjusted by carryover balance

#### Legacy: Retainer Hourly Overrides
- `retainer_hourly_rates` preference key is still supported as a fallback for projects without a definition
- Migrate to `projects` with `billing_type: fixed_monthly` for full functionality
- Preferences view (both in-popover and the fallback AppKit window): `Retainer Rates` tab replaced by `Projects` tab

### Smart Caching
- Minimizes API calls to respect Toggl rate limits
- Raw Toggl time entries are cached once in shared day-based shards under `~/Library/Caches/TogglMenuBar/entries/by_day/`
- Dashboard, CSV export, Stripe draft invoices, capped `last_billed_date` calculations, and auto carryover all read from the same shared entry cache
- Historical day shards remain cached until explicitly refreshed; today's shard still uses the configurable `cache_ttl_today`
- Manual `Refresh Now` invalidates the visible dashboard ranges, active capped-project billing-cycle ranges, and the previous-month range needed for auto carryover when applicable, so dashboard and billing outputs stay in sync after Toggl edits
- `Clear All Caches` removes all cached Toggl entry shards, project metadata, and legacy cache files, then immediately repopulates the currently needed data. This is a heavier recovery action than `Refresh Data` and can cost additional API calls on the next reload
- `Open Cache Folder` reveals `~/Library/Caches/TogglMenuBar/` in Finder for manual inspection or cleanup
- Typical API call cost:
  - background / on-demand reads: 0-1 calls when required day shards are already cached, otherwise one call per missing merged range
  - manual `Refresh Now`: typically 2-5 calls depending on overlapping dashboard, billing-cycle, and carryover ranges
  - `Clear All Caches`: variable; the next dashboard render and any later export/invoice flows will fetch whatever data is no longer cached
- Running Toggl entries and any non-positive durations are excluded from earnings/hour totals so live timers cannot corrupt dashboard totals

### System Service
- Runs as macOS LaunchAgent
- Auto-starts on login (optional)
- No dock icon
- Standard macOS storage locations
- Auto-restarts on crashes

### Preferences
- JSON-based configuration
- Configurable refresh intervals
- Customizable goals and targets
- Vacation day settings
- Billing tab supports weekly local reminder rules like `Friday 14:00 → invoice Acme Inc`
- Cache TTL controls
- Project definitions with billing types (`projects` key)
- Integrations tab lets the user update the Toggl API token, Toggl workspace id, and Stripe API key after installation
- Integrations tab also maps Toggl projects to Stripe customers by fetching live Stripe customers and letting the user pick by name
- The same project-mapping grid can store optional Upwork contract ids per Toggl project; those ids power the dashboard shortcut that opens the correct Upwork work diary for today
- `fixed_monthly` projects are always fixed in projections — no toggle needed
- Legacy `retainer_hourly_rates` still supported

### Monitoring & Logging
- API audit log for transparency
- Output and error logs
- Status monitoring (memory, uptime)
- API call tracking per operation

### Billing Reminders
- Billing reminders are local notifications generated by the menu bar app while it is running as a LaunchAgent
- Reminder rules currently support:
  - project name
  - task type (`invoice`)
  - schedule: either a weekday (fires weekly) **or** a `day_of_month` (fires monthly). `day_of_month` accepts `1`–`28` for nth-day-of-month and `-1`/`-2`/`-3` for last / 2nd-to-last / 3rd-to-last day
  - local time in `HH:MM` 24-hour format
- Notifications are deduped per reminder per local day, so a `Friday 14:00` reminder will alert once each Friday (and a `day_of_month: -1` reminder once on the last day of each month) even though the app checks on a 60-second loop
- Preferences → `Billing` tab includes a `Send Test Notification` button that posts an example reminder immediately so the user can verify macOS notification permissions and preview the copy
- Billing tab help text warns that notifications post as "Python" (because the LaunchAgent runs the interpreter directly, no `.app` bundle): any active Focus must allowlist Python, and full Do Not Disturb will suppress reminders entirely
- Reminder delivery uses 0 Toggl API calls and 0 third-party API calls

### CLI Version
- Standalone command-line tool
- Daily, weekly, monthly reports
- Works independently of menu bar app

### Hours CSV Export
- Footer `Export/Invoice` drop-up in the dashboard popover includes an `Export CSV` path that lists every project that has a resolvable billing rate
- Click a project to export a billing-ready CSV with columns: `Description, Start date, Start time, End date, End time, Duration, Time Billed (hours), Hourly Rate (USD), Money Billed (USD)` plus a `---- Total ----` row
- Output format is byte-compatible with the standalone `process_toggl_hours.py` script (per-project, one CSV per export)
- Range selection respects the project's billing cycle:
  - `hourly_with_cap` projects with `last_billed_date` set and unbilled hours that exceed `cap_hours`: the "Since last billed" preset is replaced with an **"Unbilled (under cap)"** preset whose range is `last_billed_date + 1` through the project's `cap_fill_date` (the last day on which cumulative unbilled hours stay at or under the cap). The day that first pushes cumulative hours over the cap is excluded entirely — no partial-day splitting — so the exported CSV total never exceeds the cap.
  - `hourly_with_cap` projects with `last_billed_date` set whose unbilled hours are still within the cap: the "Since last billed" preset runs from `last_billed_date + 1` through today, unchanged.
  - All other projects: presets include `This week`, `Last week`, `Last month`, `Year to date`, plus a custom range
- Hourly rate uses the project's effective rate from `get_effective_project_rate` (the same rate used everywhere else in the app)
- Output saved to `~/Downloads/{project_slug}_{range}_hours.csv`, then revealed in Finder; a notification confirms the export
- Also available as a submenu in the fallback dropdown menu when the WebKit dashboard is unavailable
- API call cost: 0-1 calls per export range. Exports reuse the same shared day-based entry cache as the dashboard, so exporting right after a refresh or a prior export usually hits cached day shards only

### Stripe Draft Invoice Creation
- Footer `Export/Invoice` drop-up includes a `Create Stripe Invoice` workflow that mirrors the CSV flow: choose a project, choose a billing range, and create a Stripe draft invoice
- Uses the same project/range presets as CSV export:
  - `hourly_with_cap` projects with `last_billed_date` set and unbilled hours over the cap: "Unbilled (under cap)" preset from `last_billed_date + 1` through the project's `cap_fill_date`, same cap-safe semantics as the CSV export
  - `hourly_with_cap` projects with `last_billed_date` set and unbilled hours still under the cap: "Since last billed" preset from `last_billed_date + 1` through today
  - All other projects: this week, last week, last month, year to date, or a custom date range
- If the project has not been linked to a Stripe customer yet, the dashboard fetches Stripe customers and prompts the user to pick one by name immediately after date selection; the mapping is then saved for future invoices
- While creating the invoice, the dashboard shows an in-place loading state instead of silently dismissing
- Success state is explicit that the app created a **draft** invoice only, and offers an `Open in Stripe` button so the user can review and send it manually
- The draft invoice footer contains an hours breakdown line for each billed Toggl entry in the selected range
- Output creates one Stripe draft invoice plus one attached invoice item for the selected project/range
- API call cost:
  - Toggl: 0-1 calls per invoice range (reuses the same shared day-based entry cache as the dashboard and CSV export)
  - Stripe: 1 customer-list call only when associating an unmapped project, then 2 write calls per invoice (draft invoice + invoice item)

### Upwork Work Diary Shortcut
- Footer `Export/Invoice` drop-up includes an `Open Upwork Diary` workflow that lists Toggl projects and shows whether each one is linked to an Upwork contract id
- Clicking a linked project opens `https://www.upwork.com/nx/workdiary/` for **today's local date** with that project’s saved `contractId` and `tz=mine`
- Unlinked projects remain visible with a `Needs contract` badge; clicking one opens an inline contract-id form inside the same dashboard flow, and `Save & Open Diary` persists the mapping before opening Upwork
- Contract ids are configured in the Preferences `Integrations` tab alongside the Stripe customer mapping grid
- Current implementation is a deep-link shortcut, not an Upwork API write path; Upwork’s documented public GraphQL docs expose work-diary reads but do not document a manual-time creation mutation
- API call cost:
  - Toggl: 0 calls
  - Upwork: 0 app-side API calls; the app only opens the diary URL in the browser

---

## Key Benefits

### Productivity
- Instant visibility into daily earnings
- No need to log into Toggl web interface
- Always know where you stand financially
- Motivating real-time feedback

### Financial Planning
- Accurate monthly projections
- Accounts for vacation time
- Project-level hour tracking
- Trend visibility (week/month)

### Efficiency
- Minimal API usage (respects rate limits)
- Smart caching reduces wait times
- Background updates don't block
- Low resource footprint

### Native macOS Experience
- True menu bar integration
- No dock icon clutter
- Standard storage locations
- Auto-start capability
- Clean, native UI

### Developer-Friendly
- Simple JSON configuration
- Comprehensive logging
- Easy management scripts
- Clean, maintainable code
- Well-documented

### Reliability
- Auto-restart on failures
- Graceful error handling
- Offline capability (uses cache)
- Service management built-in

---

**When adding or changing features:**
1. Update this file first
2. Implement the feature
3. Update README.md with user-facing changes
