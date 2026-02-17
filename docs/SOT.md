# Source of Truth - Freelance Tracker Features & Benefits

**Last Updated:** 2026-02-17

Master reference for all features and benefits. Agents must update this file when adding or modifying functionality.

---

## What It Does

Freelance Tracker is a macOS menu bar app that shows real-time Toggl earnings at a glance. Click the menu bar icon to see detailed breakdowns of today, this week, this month, and projected monthly earnings.

---

## Core Features

### Real-Time Earnings Display
- Menu bar icon showing daily total (e.g., "💰 $400")
- Click to see detailed breakdown
- Auto-refresh every 30 minutes
- Manual refresh available

### Daily View
- Today's total earnings and hours
- Per-project breakdown
- Billable projects show earnings and hours
- Non-billable projects show hours only unless a retainer hourly override is configured

### Weekly & Monthly Summaries
- Current week total
- Current month total
- Monthly hours breakdown by project

### Month Projection
- Intelligent forecast based on current performance
- Accounts for business days vs worked days
- Configurable vacation/PTO days
- Formula: (earnings ÷ worked days) × workable days

### Project Hour Targets
- Set monthly hour targets for any project
- Supports billable and non-billable projects
- Visual progress bars with Unicode blocks on separate line
- Progress tracking with percentages inline with hours
- Example: "Client A: 45.2h / 80h (57%)" followed by "[██████░░░░░░]" on next line
- Non-billable projects only appear in monthly view if target is configured

### Retainer Hourly Overrides
- It is assumed that retainer projects are not configured with an hourly rate in Toggl
- Configure `retainer_hourly_rates` in preferences to assign an hourly value to retainer/non-billable projects
- Retainer projects with overrides contribute to daily, weekly, and monthly dollar totals
- Retainer projects with overrides are treated as earning work for monthly projection worked-day calculations
- Native Preferences window includes a `Retainer Rates` tab for editing overrides
- No additional Toggl API calls required (local preference only)

### Retainer Domain Rules (For Upcoming Features)
- Business model goal: retainers are fixed monthly invoice amounts, not purely hours × rate
- Expected future behavior: monthly over/under delivery hours can roll forward between months (carryover balance)
- Current implementation: retainer projects are represented as hourly-value earnings via `retainer_hourly_rates` in the app
- It is assumed that retainer projects are not configured with an hourly rate in Toggl
- Status: this section documents business rules for future implementation; carryover logic is not yet implemented in app totals

### Smart Caching
- Minimizes API calls to respect Toggl rate limits
- Historical data cached permanently
- Today's data refreshed intelligently
- Typically 2-4 API calls per day

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
- Retainer hourly overrides by project name

### Monitoring & Logging
- API audit log for transparency
- Output and error logs
- Status monitoring (memory, uptime)
- API call tracking per operation

### CLI Version
- Standalone command-line tool
- Daily, weekly, monthly reports
- Works independently of menu bar app

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
