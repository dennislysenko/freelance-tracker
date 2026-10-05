# Source of Truth - Freelance Tracker Features & Benefits

**Last Updated:** 2026-04-20

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
- **Google Calendar days off (optional)**: when a Google Calendar secret iCal URL is set (Settings → Integrations → "Google Calendar ICS URL", stored as `GOOGLE_CALENDAR_ICS_URL` in `.env`), the projection counts the month's *actual* days off instead of the flat `vacation_days_per_month` estimate. A business day counts as off when a calendar event's title matches one of the configurable `days_off_keywords` (Settings → Work Planning; case-insensitive substring, defaults include "vacation", "day off", "ooo", "out of office", "ceo day", "holiday", "pto"). Recurring events (e.g. a weekly "CEO Day") are expanded via RRULE and count once per occurrence; multi-day all-day events count each covered business day (weekend days never reduce workable days). The dashboard shows "N days off excluded (from calendar)" when the calendar drives the count. The flat `vacation_days_per_month` remains the fallback when no URL is configured or the feed is unreachable with no cache. Implemented in `calendar_days_off.py`. API cost: 0 Toggl calls; the ICS feed is fetched at most once per 6 hours and cached in `~/Library/Caches/TogglMenuBar/` (a stale cache is reused when a fetch fails).
- Formula for hourly projects: `(earnings ÷ worked days) × workable days`
- `fixed_monthly` projects are always treated as guaranteed income — only `hourly` and `hourly_with_cap` earnings are extrapolated by pace: `fixed_monthly_total + rev_share + project((variable earnings) by pace)`
- **Monthly rev share**: a manually-entered, variable rev-share amount for the current month (set in Settings → Work Planning, e.g. last month $3k, this month $7k). It is added to the projection as guaranteed income (not extrapolated by pace) and shown as a "$X rev share (this month)" breakdown line. Stored per month (`monthly_rev_share: {"YYYY-MM": amount}`), so it never carries a stale value forward — each new month starts blank until you enter the new amount; entering `0` clears it. No Toggl API calls.

#### Projection Diagnostics Export

Settings → Advanced → **Copy Projection Diagnostics** copies an **anonymized** JSON snapshot of the projection math to the clipboard, so a user can share *why* their on-pace estimate looks wrong without exposing rates or client names. Implemented in `diagnostics.py`; uses only already-cached data (0 Toggl API calls).

The blob contains the projection-relevant config (`projects`, `vacation_days_per_month`, `days_off_keywords`, `monthly_rev_share`, `retainer_hourly_rates`, `project_targets`), the full `calculate_monthly_projection()` result including its `trace` of raw intermediate terms (`current_total`, `variable_earnings`, `worked_day_dates`, cap state, etc.), and the `calculate_period_earnings("monthly")` per-project breakdown (rate, `rate_source`, hours, earnings — where misclassification shows up).

Anonymization (always on for the share button):
- Project names → stable labels (`Project A`, `Project B`, …). `monthly_rev_share` month keys are left intact.
- Every monetary value (rates **and** dollar amounts) is multiplied by a single undisclosed constant, which is then dropped. Because the projection math is linear in dollars, all ratios and the `projected_variable` vs `capped_ceiling` cap comparison are preserved while absolute rates/earnings are removed. The inter-project rate *ratio* necessarily survives — it is the math being debugged.
- Secrets are structurally excluded: API tokens live in `.env`, never in `preferences.json`. The `stripe_project_customers` / `upwork_contracts` mappings are dropped entirely (irrelevant to projection, and sensitive).

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
  - Pacing marker + status: compares capped progress against the billing-cycle window from `last_billed_date + 1 day` through the end of the following month, instead of the current calendar month. Pacing label uses early-cycle Bayesian shrinkage (see Pacing decision tree below).
  - Projection: `min(unbilled_hours + daily_avg_since_date × remaining_biz_days_in_billing_cycle, cap_hours) × rate`
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

#### Pacing decision tree (monthly project bars)

Applies to all tracked monthly projects (calendar-month and LBD-capped).

Inputs:
- `percentage` = hours / target × 100
- `calendar_pct` = elapsed time in cycle (calendar month, or LBD billing cycle if `last_billed_date` is set)
- `elapsed_days` = days into the cycle (1 on the first day)

Ratio with early-cycle Bayesian shrinkage:
```
raw_ratio       = percentage / max(calendar_pct, 0.1)
shrink_weight   = min(elapsed_days / 5, 1.0)        # 0.2 on day 1, 1.0 by day 5
effective_ratio = raw_ratio · shrink_weight + 1.0 · (1 − shrink_weight)
```
The shrinkage damps noisy ratios when the denominator is tiny at the start of a cycle. By day 5 it has no effect.

Banding (first match wins):
1. `percentage > 105` → **Over target** (red)
2. `percentage ≥ 100` → **Complete** (orange)
3. `percentage ≥ 95` → **Almost there** (orange)
4. `effective_ratio ≥ 1.5` → **Well ahead — ease off or bank hours** (green)
5. `effective_ratio ≥ 1.15` → **Ahead of pace** (green)
6. `effective_ratio ≥ 0.85` → **On pace** (green)
7. `effective_ratio ≥ 0.5` → **Behind — ramp up to stay on track** (blue)
8. else → **Way behind — needs attention** (blue)

**Late-cycle feasibility guard** (`hourly_with_cap` only): the proportional ratio above keeps crediting time that has not elapsed yet, so a cap that is mathematically out of reach can still read green in the final couple of days. As a counterpart to the early-cycle shrinkage, once the cycle is near its end (`calendar_pct ≥ 80`) and only the last business days remain (remaining business days in the cycle ≤ 2), if filling the cap would now require more than a full workday (>8h) per remaining business day, a green band is downgraded to **Behind — cap out of reach** (blue). It runs after banding, only ever turns green → blue, and is inert with 3+ business days left, so earlier-cycle pacing is unchanged. Remaining business days come from `get_lbd_remaining_business_days` (LBD projects) or the remaining weekdays in the month (calendar-month caps).

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
- Vacation day settings (fallback when no days-off calendar is configured)
- Work Planning tab: "Days Off Keywords" field (comma-separated) controlling which calendar events mark a day off (`days_off_keywords` key)
- Work Planning tab: "This Month's Rev Share" field for variable rev-share income, added to the current month's projection (`monthly_rev_share` key, month-scoped)
- Billing tab supports weekly local reminder rules like `Friday 14:00 → invoice Acme Inc`
- Cache TTL controls
- Project definitions with billing types (`projects` key)
- Integrations tab is a **grid of integration cells** grouped by purpose (Time tracking, Assistant, Billing & invoicing, Planning). Each cell shows the integration name and its status — green "✓ Active" when configured, "Configure integration" when not — so the whole tab fits on one screen with no scrolling
- Clicking a cell **drills into a detail pane** containing only that integration's fields and setup guidance, with a back arrow to the grid. Replaces the previous single long scrolling column of every credential
- Covers Toggl (token + workspace id), OpenAI (natural-language logging), Stripe (draft invoices), Project Mapping (Toggl project → Stripe customer / Upwork contract grid), and Google Calendar (days off). "Open Google Calendar Settings" and "Open OpenAI API Keys" buttons open the relevant provider page in the browser
- The active tab **and** the open integration are mirrored to Python (`settings_tab:` / `settings_intg:` bridge messages) and re-rendered on the next load. The popover is transient, so leaving to fetch a credential in a browser dismisses it; without this the user was dropped back on the first tab and had to re-navigate every time
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

### Natural-Language Time Logging (optional, bring-your-own OpenAI key)
- Lets the user type shorthand — "put 1 hour of Randonautica retainer at 9am for the past 2 days, no note" — and have it turned into proposed Toggl entries
- **Nothing is written without confirmation.** The parse produces a *proposal* (one row per entry, e.g. "mon aug 3, 9:00am · 1.00h · Randonautica - Retainer"), which the user applies or cancels
- Also classifies read questions ("how many hours on Acme this month?") into a local date-range query answered from the existing entry cache — **zero Toggl API calls**. The answer lists the total, a per-project breakdown, and per-entry rows (time range, project, note) for ranges of 7 days or less; longer ranges (max 92 days) get day totals only
- **Follow-ups**: the last 8 transcript turns are sent with each command, proposals spelled out with absolute dates and whether they were applied, so "round to the half hour mark" or "actually that was yesterday" refines the previous proposal into a new, complete one instead of reading as a command with no subject
- **Entry point**: a one-line input pinned to the top of the dashboard, shown only when an OpenAI key is configured (the absent key is the feature's off switch, so there is no separate preference). Submitting routes to a dedicated **Assistant view** — transcript, proposal cards, back arrow — so a long conversation never resizes the content-sized popover
- **Proposal cards** list one row per entry with date, time range, project, duration, and note. Rows that overlap time already in Toggl render unchecked, highlighted, and labelled "Overlaps time already logged"; clean rows stay checked. Apply writes only the checked rows, so re-running "the past 2 days" cannot silently double-log while genuinely overlapping work stays possible
- Applying invalidates the affected day caches (`invalidate_entry_days`) and triggers a dashboard refresh, so totals update immediately. Partial failures report how many entries actually landed, and a failed proposal stays retryable
- **Threading**: the OpenAI call and the Toggl writes on apply both run on a worker thread (`background_work.py`) so the menu bar stays responsive, with completions delivered back via `NSOperationQueue.mainQueue()`. A generation token discards replies from superseded commands. While writes are in flight the card shows "Logging to Toggl…" with its rows disabled, and repeat clicks are ignored
- Transcript text is selectable (the rest of the popover is not) so answers can be copied out
- Transcript state lives in Python (`assistant.py`), not the DOM, because `loadHTML_` reloads the document on every dashboard refresh and would otherwise wipe an in-progress conversation. Capped at 40 turns
- The view applies a proposal **by id**; entry data never travels back across the bridge, so a rendering bug cannot become a wrong time entry
- The utterance is **base64-encoded** across the bridge, which splits raw action strings on `:` and spaces — a note like "note: sprint planning, 2:30 standup" would otherwise be shredded
- Implemented in `nl_time.py` (parsing), `assistant.py` (session/write path), `assistant_view.py` (rendering); the write path reuses the long-standing `toggl_data.create_time_entry`
- Available projects are passed to the model as a JSON-schema **enum**, so it cannot propose a project that does not exist in the workspace. Ambiguous commands return an `unclear` intent asking for what is missing rather than guessing a project
- The model never performs timezone math: it returns a local calendar date plus a local wall-clock time, and `resolve_entries` attaches the machine's local zone to build the aware datetime `create_time_entry` requires
- `find_collisions` flags proposals overlapping an entry already in Toggl, so re-running "the past 2 days" warns instead of silently double-logging
- **Bring-your-own key**: set `OPENAI_API_KEY` in Settings → Integrations (stored in `.env`, never in `preferences.json`). With no key set, the feature is simply unavailable; every other feature is unaffected
- The key is **verified with a live call when saved** (only when it actually changed), so a bad key surfaces in the settings pane rather than mid-command. Errors are distinguished: rejected key (401), no billing credit (`insufficient_quota`), rate limit, and service/network failure each produce a different message
- Uses the OpenAI **Responses** API (`/v1/responses`), not chat/completions: a restricted project key with *Model capabilities: Request* grants Write on `/v1/responses` specifically, so this is the endpoint covered by the scoped key the setup guide tells users to mint
- Model is pinned to `gpt-4.1-mini`, overridable via `OPENAI_MODEL` in `.env`
- **Privacy**: the utterance and the user's Toggl *project names* (often client names) are sent to OpenAI under the user's own account and retention terms. This is stated in the Integrations tab. Nothing is sent unless the feature is used; no other credential leaves the machine
- API call cost:
  - Toggl: 0 calls to parse; **1 POST per entry** on apply (a "past 2 days" command applies 2 entries = 2 calls); read questions cost 0
  - OpenAI: 1 call per command, plus 1 on key save; billed to the user's own account
  - OpenAI calls are deliberately **not** recorded in the Toggl audit log, which stays Toggl-only so the documented Toggl call counts remain accurate

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
