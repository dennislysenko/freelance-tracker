# Source of Truth - Freelance Tracker Features & Benefits

**Last Updated:** 2026-04-10

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
- Preferences window no longer exposes dashboard collapse-state defaults; its fourth tab is `Advanced` and currently only exposes the API audit log
- Dashboard footer provides a `Refresh` split button with a drop-up (`Refresh Data` or one-off `Refresh Projects`), `Settings`, `Update`, and `Quit`
- Dashboard footer provides a single `Export/Invoice` forced drop-up that branches into `Export CSV` or `Create Stripe Invoice`
- Dashboard footer is rendered as a bottom drawer flush with the sheet edge, while the dashboard content scrolls above it with enough bottom padding to stay readable
- Dashboard shows a rate-limit warning when cached data is being used and an inline retry state when refresh fails
- Auto-refresh every 30 minutes
- Manual refresh available

### Daily View
- Today's total earnings and hours
- Per-project breakdown
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

Carryover applies to `hourly_with_cap` and `fixed_monthly / hour_tracking: required`. Balance is stored in `~/Library/Application Support/TogglMenuBar/retainer_carryover.json` and displayed in the monthly hours progress bar. Auto-calculated from cached time entries at end of month; can also be manually set or overridden via the Projects tab in preferences (the "Feb carryover h" field shown for applicable billing types):

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
- Native Preferences window: "Retainer Rates" tab replaced by "Projects" tab

### Smart Caching
- Minimizes API calls to respect Toggl rate limits
- Historical data cached permanently
- Today's data refreshed intelligently
- Manual `Refresh Now` invalidates only the active dashboard period caches: today (always), current week historical range when applicable, current month historical range when applicable, and one `last_billed_date` range per configured capped project
- Typically 2-4 API calls per day
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
- Cache TTL controls
- Project definitions with billing types (`projects` key)
- Integrations tab lets the user update the Toggl API token, Toggl workspace id, and Stripe API key after installation
- Integrations tab also maps Toggl projects to Stripe customers by fetching live Stripe customers and letting the user pick by name
- `fixed_monthly` projects are always fixed in projections — no toggle needed
- Legacy `retainer_hourly_rates` still supported

### Monitoring & Logging
- API audit log for transparency
- Output and error logs
- Status monitoring (memory, uptime)
- API call tracking per operation

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
- API call cost: 1 call per export at most (the entries for the range are cached, so back-to-back exports for different projects in the same range hit the cache)

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
  - Toggl: 1 call per invoice at most (reuses the same cached range fetch as CSV export)
  - Stripe: 1 customer-list call only when associating an unmapped project, then 2 write calls per invoice (draft invoice + invoice item)

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
