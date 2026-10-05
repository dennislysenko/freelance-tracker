# MCP Server Plan — Agents reading hours, projections, and project rules

**Status:** implemented 2026-09-15 (see docs/mcp-agents.md for the user guide)
**Date:** 2026-09-15

## Goal

Let coding agents (Claude Code, Codex, anything speaking MCP) read the same
numbers the dashboard shows — hours, earnings, monthly projection, per-project
pacing, and the project billing rules — so the user can ask an agent "how am I
doing this month, and where should I put more time?" and get an answer grounded
in real data rather than a guess.

v1 is **read-only**. Nothing an agent does through this server writes to Toggl,
preferences, or carryover. Writes (logging time, editing rules) are a separate
later phase with its own confirmation contract.

## Architecture decision: stdio server over the shared cache

`mcp_server.py` is a standalone stdio MCP server started by the agent host
(Claude Code / Codex spawn it per session). It runs from the project venv,
imports the existing modules (`toggl_data`, `preferences`, `carryover`,
`calendar_days_off`, the new `pacing`), and reads the same on-disk state the
menu bar app uses:

- `~/Library/Application Support/TogglMenuBar/preferences.json`
- `~/Library/Application Support/TogglMenuBar/retainer_carryover.json`
- `~/Library/Caches/TogglMenuBar/entries/by_day/*.json` and the projects cache
- the app-local `.env` for the Toggl token (only needed to *identify* the
  workspace; see cache-only mode below)

Rejected alternative: a localhost socket/HTTP endpoint inside `menubar_app.py`
with the MCP server as a thin proxy. It would keep a single Toggl caller, but it
adds a server thread to an app that is deliberately main-thread-only (the
OpenAI worker is the sole exception), stops working whenever the app is not
running, and doubles the surface to test. The stdio design has none of those
costs; the one thing it needs is a guarantee that the second process never
spends Toggl API budget. That is the cache-only mode.

### Cache-only mode (0 Toggl API calls, always)

`toggl_data` gets a module flag `CACHE_ONLY` (set from env
`FREELANCE_TRACKER_CACHE_ONLY=1`, which the MCP server sets before import).
When on:

- `refresh_entry_ranges` and the project fetch in `get_projects` raise
  `CacheMissError(days=[...])` instead of calling Toggl.
- A **stale today shard is served, not refetched.** `_missing_entry_ranges`
  must treat "shard exists but today's TTL expired" as present. Every tool
  response carries `data_as_of` (oldest shard mtime in the range) so the agent
  can say "as of 10:40".
- Only a genuinely absent day shard raises. The server turns that into a tool
  error: "Day X not cached. Open the dashboard or press Refresh Now in
  Freelance Tracker, then retry." Absent shards are rare for the ranges the
  dashboard already keeps warm (this month, this week, active LBD cycles,
  previous month for carryover).

Two more side-effect traps the server must avoid:

- Call `calculate_period_earnings("monthly")` + `calculate_monthly_projection()`
  directly (as `diagnostics.py` does). Do **not** call `get_monthly_earnings`,
  which writes the carryover file via `_try_calculate_last_month_carryover`.
- stdio MCP owns stdout. `preferences.load_preferences` and others `print()` on
  error; the server must `sys.stdout = sys.stderr` before importing any project
  module, then hand the real stdout to the MCP transport.

`is_rate_limited()` is in-process state, so the server cannot see the app's
flag. It reports cache ages instead (see `get_data_freshness`).

## Prerequisite refactor: `pacing.py`

The pacing decision tree (percentage vs calendar progress, early-cycle Bayesian
shrinkage, banding, late-cycle cap-feasibility guard) lives inline in
`DashboardPanelController._generate_html` (`dashboard_panel.py` ~L930–1015).
It cannot be reused from there. Extract it into a pure module:

```python
# pacing.py
def compute_pacing(hours, target, carryover_balance, billing_type, proj_def, today=None) -> PacingResult
```

`PacingResult` (dict) fields: `effective_target`, `percentage`, `calendar_pct`,
`elapsed_days`, `pace_ratio` (post-shrinkage), `raw_ratio`, `band`
(`over_target | complete | almost | well_ahead | ahead | on_pace | behind |
way_behind | cap_out_of_reach`), `label`, `color`, `cycle_start`, `cycle_end`
(LBD projects) , `remaining_biz_days`, `hours_needed`,
`hours_per_remaining_day`.

The dashboard then consumes `label` / `color` / `percentage` exactly as today.
Rendering must stay byte-identical; `tests/test_dashboard_panel.py` plus a new
`tests/test_pacing.py` table test over every band and the guard pin it.

This extraction is also the place to act on the standing feedback that the
labels read too rosy: the MCP exposes `pace_ratio` and `hours_per_remaining_day`
as numbers, so agents can be blunt even if the dashboard copy stays cheerful.
Changing the copy itself is not part of this plan.

## The server

### Dependency

`mcp>=1.2` (Python SDK, FastMCP) added to `requirements.txt`. Python 3.14 in
the venv is supported. No new native builds.

### Tools (v1, all read-only, all 0 Toggl calls)

| Tool | Returns | Backed by |
|---|---|---|
| `get_overview` | today / week / month totals and hours, projected month, fixed vs variable split, rev share, days worked / workable, days-off source | `calculate_period_earnings` ×3, `calculate_monthly_projection` |
| `get_month_status` | per project: hours, target, carryover-adjusted target, percentage, full `PacingResult`, billing cycle window, cap fill date, rate source, earnings; plus an `attention` list ranked by severity | monthly earnings + `pacing.compute_pacing` |
| `get_projection` | full projection dict including `trace` (raw intermediate terms) and days-off dates | `calculate_monthly_projection` |
| `get_project_rules` | `projects` definitions, `project_targets`, `retainer_hourly_rates`, this month's rev share, vacation days / keywords, and a plain-English rendering of each rule ("Acme: hourly with 20h cap, billed from Sep 16, cycle ends Oct 31") | `preferences.load_preferences` |
| `get_time_entries` | entries for a date range (max 92 days), optional project filter, grouped by day; description, start, duration | `get_entries_for_range` under cache-only |
| `get_data_freshness` | per-range oldest shard mtime, projects cache age, whether today's shard is past TTL, MCP enabled flag | cache file stats |

`attention` in `get_month_status` is computed server-side so every agent ranks
the same way. Order, first match wins per project:

1. `cap_out_of_reach` or `way_behind`
2. `behind`
3. LBD cycle ends within 3 business days and `hours_needed > 0`
4. `over_target` (unbillable overflow accumulating)
5. `well_ahead` (hours could move to a behind project)

Each item: `project`, `reason`, `band`, `hours_needed`,
`hours_per_remaining_day`, `remaining_biz_days`. The month projection being
capped (`is_projection_capped`) is a top-level note, not per project.

### Prompt

One MCP prompt, `progress_checkin`, that tells the host model how to discuss
the data: lead with `attention`, quote hours-per-remaining-day rather than
labels, treat `fixed_monthly` income as guaranteed, never suggest logging time
(v1 has no write path). Hosts that support prompts show it as a slash-style
entry; Claude Code exposes it as `/mcp__freelance-tracker__progress_checkin`.

### Out of scope for v1

- Write tools (`log_time`, `set_last_billed_date`). The in-app assistant's
  contract is propose → confirm → apply with collision checks; an agent write
  path needs the same and should reuse `assistant.AssistantSession`. Phase 2.
- A `refresh` tool that spends Toggl calls. Phase 2 behind a separate
  preference, default off.
- Streamable HTTP transport. stdio covers both target hosts.

## Enable UI (dashboard popover, Settings → Integrations)

Per the project rule, this goes in the in-popover settings (`settings_view.py`
/ `settings_handler.py`), not the rumps menu. New cell **"Agents (MCP)"** in a
new group **Agents**, drilling into a detail pane with:

- **Toggle "Enable MCP server"** → preference `mcp_enabled` (default `false`).
  The server checks it on startup and on every tool call; when off, every tool
  returns "Disabled in Freelance Tracker → Settings → Integrations → Agents".
  This is what makes the toggle real without any process management: hosts
  still spawn the server, it just declines.
- **Toggle "Anonymize client names and amounts"** → `mcp_anonymize` (default
  `false`). Reuses `diagnostics._anonymize_names` / `_scale_money`. Off by
  default because "how am I doing on Acme" is the point; on is for users who
  route agents through a model they do not want client names sent to.
- Status line: enabled/disabled, and best-effort detection of registrations
  (`~/.claude.json` `mcpServers.freelance-tracker`, `~/.codex/config.toml`
  `[mcp_servers.freelance-tracker]`) shown as "Registered in Claude Code ✓ /
  Codex ✗". Read-only detection; the app never edits those files.
- **Copy Claude Code command** button → clipboard via existing
  `_dashboard_copy_text`:
  `claude mcp add --scope user --transport stdio freelance-tracker -- <venv>/bin/python <repo>/mcp_server.py`
- **Copy Codex config** button → TOML block for `~/.codex/config.toml`.
- **Test server** button → spawns `mcp_server.py` as a subprocess, runs
  `initialize` + `tools/list` with the SDK's stdio client on the background
  worker (`background_work.run_in_background`), shows "6 tools, data as of
  10:40" or the error. Same pattern as the OpenAI key validation.
- Privacy copy: what leaves the machine is whatever the agent's model reads;
  the server itself makes no network calls.

Preference plumbing: add both keys to `DEFAULT_PREFERENCES` and
`validate_preferences` (bool), to `_SETTINGS_KEYS` in `settings_handler.py`,
and a checkbox for each on the fallback AppKit window's Integrations tab
(`preferences_window.py`) so the fallback can still turn it off.

## Agent setup instructions (`docs/mcp-agents.md`, linked from README)

**Claude Code**

```bash
claude mcp add --scope user --transport stdio freelance-tracker -- \
  /Users/you/dev/freelance-workflow/venv/bin/python \
  /Users/you/dev/freelance-workflow/mcp_server.py
claude mcp list        # should show freelance-tracker: connected
```

User scope so it is available from any project, since progress check-ins are
not tied to this repo. A project-scoped `.mcp.json` is not recommended: the
paths are machine-specific and the file would be committed.

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.freelance-tracker]
command = "/Users/you/dev/freelance-workflow/venv/bin/python"
args = ["/Users/you/dev/freelance-workflow/mcp_server.py"]
```

Global rather than the repo's `.codex/config.toml`, for the same reason.

**Using it**: example prompts ("Check in on my freelance month", "Which
project needs hours this week?", "Explain why my projection dropped"), a note
that the data is as fresh as the dashboard's last refresh, and that the agent
cannot log time through this server. Optional: a personal
`~/.claude/skills/freelance-checkin` skill wrapping the `progress_checkin`
prompt.

## Documentation updates

- `docs/SOT.md`: new "Agent access (MCP server)" section: tools, cache-only
  guarantee, the two preferences, API cost (0 Toggl calls for every tool),
  privacy note. Update the Integrations tab description with the new cell.
- `README.md`: short "Agents (MCP)" subsection under Integrations linking to
  `docs/mcp-agents.md`.
- `AGENTS.md`: add `mcp_server.py` and `pacing.py` to Key Files; note the
  cache-only rule for any future tool.

## Testing

- `tests/test_pacing.py`: every band boundary, shrinkage at days 1/3/5, LBD
  cycle window, feasibility guard on/off, calendar-month vs LBD.
- `tests/test_dashboard_panel.py`: existing rendering tests must pass
  unchanged after the extraction.
- `tests/test_toggl_data_cache_only.py`: absent shard raises `CacheMissError`;
  stale today shard is served; no `requests` call ever happens (monkeypatch
  `requests.get` to fail loudly).
- `tests/test_mcp_server.py`: each tool with the data layer stubbed (same
  fixture style as `test_diagnostics.py`); `mcp_enabled=false` short-circuits;
  anonymize flag; `attention` ranking; stdout never written to.
- Manual: `claude mcp list` shows connected; `/mcp` in Claude Code lists the
  six tools; `codex` session can call `get_month_status`.

## Delivery order and rough effort

| Phase | Work | Size |
|---|---|---|
| 1 | Extract `pacing.py`, table tests, dashboard byte-identical | ~0.5 day |
| 2 | Cache-only mode in `toggl_data` + tests | ~0.25 day |
| 3 | `mcp_server.py` tools, prompt, attention ranking, tests | ~0.75 day |
| 4 | Settings cell, preferences, copy/test bridge actions, fallback checkbox | ~0.5 day |
| 5 | SOT, README, `docs/mcp-agents.md`, AGENTS.md | ~0.25 day |

Phases 1 and 2 are independent and each is a mergeable PR on its own. Phase 3
depends on both. Phase 4 depends on 3 only for the Test button.

## Decisions made in this plan (flag if you disagree)

- stdio server over shared cache, not a socket into the app.
- The server never calls Toggl. Stale data plus a timestamp beats spending the
  rate-limit budget from a second process.
- Registration is user/global scope, not per-repo.
- Anonymization is opt-in.
- No write tools in v1.
