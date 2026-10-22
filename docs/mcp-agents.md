# Agents (MCP): let Claude Code or Codex read your Freelance Tracker

Freelance Tracker ships a small [MCP](https://modelcontextprotocol.io) server,
`mcp_server.py`, that gives coding agents access to the same numbers the
dashboard shows: hours, earnings, the month projection, per-project pacing, and
the billing rules behind them. Ask an agent "how is my month going and where
should I put more time?" and it answers from your real data.

Reading is the default and costs nothing: the six `get_*` tools never call
Toggl, they read the app's local cache, so the data is exactly as fresh as the
dashboard's last refresh (every response says `data_as_of`).

Writing is a separate switch that is **off until you turn it on**. With it on,
an agent can log, edit, or delete **one** time entry per request, and every
write is previewed and confirmed first. See [Letting an agent log and edit
time](#letting-an-agent-log-and-edit-time) below. It can never change your
projects, rates, or settings.

## 1. Enable it in the app

Open the dashboard → `⋯` → **Settings** → **Integrations** → **Agents (MCP)**.

- Tick **Enable MCP server** and Save. Until this is on, every tool returns a
  "disabled" error, so registering the server with an agent is harmless.
- Leave **Allow agents to log and edit time** off unless you want writes; see
  below.
- Optionally tick **Anonymize client names and dollar amounts** if the agent
  runs on a model you do not want client names sent to. Project names become
  `Project A`, `Project B`, … and every dollar value is rescaled by one hidden
  constant, so ratios and pacing still make sense.
- **Test server** spawns the server and lists its tools, so you can confirm
  the venv and script path work before touching any agent config.

The pane also shows whether the server is already registered in Claude Code
or Codex, and has copy buttons for the two snippets below (with your real
paths filled in).

## 2. Register it with your agent

Both registrations use the project's own virtualenv so the server sees the
same dependencies as the menu bar app. Replace `/Users/you/dev/freelance-workflow`
with your checkout path, or use the copy buttons in Settings.

### Claude Code

```bash
claude mcp add --scope user --transport stdio freelance-tracker -- \
  /Users/you/dev/freelance-workflow/venv/bin/python \
  /Users/you/dev/freelance-workflow/mcp_server.py

claude mcp list        # freelance-tracker: ✓ connected
```

User scope makes it available in every project. That is the point: a
progress check-in is not tied to this repo. A project-scoped `.mcp.json` is
not recommended because the paths are machine-specific and would be committed.

Inside Claude Code, `/mcp` lists the server and its tools, and the
`progress_checkin` prompt shows up as `/mcp__freelance-tracker__progress_checkin`.

### Codex

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.freelance-tracker]
command = "/Users/you/dev/freelance-workflow/venv/bin/python"
args = ["/Users/you/dev/freelance-workflow/mcp_server.py"]
```

Global rather than the repo's `.codex/config.toml`, for the same reason.

## 3. Use it

Example prompts:

- "Check in on my freelance month."
- "Which project needs hours this week, and how many per day?"
- "Explain why my projection dropped since last week."
- "What are the billing rules for Acme?"
- "How many hours did I log on Globex between the 1st and the 14th?"

The `progress_checkin` prompt tells the agent to lead with the ranked
`attention` list, quote hours per remaining business day rather than pacing
labels, treat fixed-monthly income as guaranteed, and be blunt when you are
behind.

### Tools

| Tool | What it returns |
|---|---|
| `get_overview` | Today / this week / this month totals and hours per project, plus the projection summary |
| `get_month_status` | Per-project month status with pacing numbers and a ranked `attention` list. Start here. |
| `get_projection` | The full projection including the raw `trace` terms and days-off dates |
| `get_project_rules` | Project definitions, targets, rev share, vacation settings, plus each rule in plain English |
| `get_time_entries` | Cached entries between two dates (max 92 days), grouped by day, optional project filter |
| `get_data_freshness` | Cache ages, whether today's data is past its TTL, whether the server is enabled and writable |

Three more appear only as write tools; they refuse until you enable writing:
`log_time`, `update_entry`, `delete_entry`.

`attention` ranks projects by urgency: cap out of reach or way behind first,
then behind, then capped billing cycles ending within three business days
with hours unfilled, then over-target (unbillable overflow), then well ahead
(hours that could move elsewhere).

### Pacing fields

Each tracked project carries a `pacing` object:

- `percentage`: hours ÷ carryover-adjusted target
- `calendar_pct`: how far through the cycle you are (calendar month, or the
  billing cycle for capped projects with a last-billed date)
- `pace_ratio`: `percentage / calendar_pct`, shrunk toward 1.0 in the first
  five days of a cycle
- `band` / `label`: the dashboard's band (`on_pace`, `behind`, …)
- `hours_needed`, `remaining_biz_days`, `hours_per_remaining_day`: what it
  would actually take to hit the target

## Letting an agent log and edit time

Tick **Allow agents to log and edit time** in Settings → Integrations → Agents
(MCP), then Save. Three tools become usable.

| Tool | What it does |
|---|---|
| `log_time` | Creates one new entry: date, local start time, minutes, project, optional note |
| `update_entry` | Changes one existing entry; pass only the fields you want different |
| `delete_entry` | Deletes one entry. Not undoable from the agent |

Three rules hold for all of them:

**One entry per call.** None of these tools takes a list or a date range. If
you want two entries changed, the agent does it twice, and you see each one.

**Every write is previewed first.** The agent's first call writes nothing. It
comes back with exactly what would change plus a short confirmation token:

```
Log Thu Oct 8 9:00am-10:30am (1.50h) to Acme Corp — sprint planning
⚠ overlaps entry 901: Thu Oct 8 12:00pm-1:00pm · Acme Corp
Call again with confirm=3f9a2b71 to write. Nothing has been written yet.
```

Only after you say yes does the agent call again with that token. Tokens are
single-use, expire in 10 minutes, and are tied to the exact change previewed —
a token for deleting one entry cannot delete a different one, and a token for a
one-hour entry cannot confirm an eight-hour one.

**Entries are addressed by id.** `get_time_entries` returns an `entry_id` for
each entry; that is what `update_entry` and `delete_entry` take. Ask "what did
I log yesterday" first, then "change the second one to 90 minutes".

Example prompts:

- "Log 90 minutes on Acme this morning starting at 9, note: sprint planning."
- "That last entry should have been 2 hours, not 1."
- "I double-logged Tuesday afternoon — delete the duplicate."

Anonymization and writing cannot both be on. An agent that sees "Project A"
instead of your client's name should not be editing billable hours, so turning
on anonymization blocks writes.

The menu bar total updates as soon as a write lands — the agent tells the
running app, which re-reads its cache straight away. You do not need to hit
Refresh.

Each confirmed write is one Toggl API call and is recorded in the audit log at
`~/Library/Logs/toggl-api-audit.log` alongside everything else the app does.
Previews cost nothing, and so does the refresh that follows a write.

## Troubleshooting

- **"The MCP server is disabled"**: enable it in Settings and Save.
- **"Write access is off"**: tick "Allow agents to log and edit time" in the
  same pane. Reading keeps working either way.
- **"That confirmation is unknown or has expired"**: the preview is older than
  10 minutes or was already used. Ask the agent to preview the change again.
- **"No cached time entry with id ..."**: the agent needs an id from
  `get_time_entries`, and the day has to be in the cache. Open the dashboard if
  it is an older day.
- **"… are not cached. Open the Freelance Tracker dashboard …"**: the agent
  asked for a day the app has never fetched. Open the dashboard (or press
  Refresh Now) and retry. Ranges the dashboard already shows are always warm.
- **Stale numbers**: check `get_data_freshness`. The server never refreshes on
  read; the app does. After a write it tells the app to refresh immediately,
  falling back to a once-a-minute check if that signal is missed.
- **Server fails to start**: run it by hand to see the error:
  `venv/bin/python mcp_server.py` (it waits for JSON-RPC on stdin; Ctrl-C to
  exit). Make sure `pip install -r requirements.txt` has been run in the venv.

## Privacy

The server makes no network calls. Whatever an agent reads goes to whichever
model that agent uses, under that provider's terms. Turn on anonymization if
that matters for your clients. API tokens never enter any response.
