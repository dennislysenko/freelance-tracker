# Agent Guide - Freelance Tracker

Quick reference for AI agents working on this project.

> **Note on this file:** `AGENTS.md` is the real file. `CLAUDE.md` is a symlink to it (`CLAUDE.md -> AGENTS.md`). Edit `AGENTS.md` and both Claude Code and other agentic tooling will see the same content. Do not replace the symlink with a separate copy or the two will drift out of sync.

## CRITICAL: Primary Interface

**The WebKit dashboard popover in `dashboard_panel.py` is the canonical user interface.** It is what the user actually sees and interacts with.

When adding or changing any user-facing UI:
- All net-new UI work goes in `dashboard_panel.py` (the WebKit popover with embedded HTML/CSS/JS)
- Do NOT add features to the rumps fallback menu in `menubar_app.py` (`_update_fallback_menu`, `_show_fallback_error_menu`, `_build_export_fallback_menu`)
- The fallback menu exists ONLY as an emergency path when WebKit/PyObjC is unavailable, and must remain a minimal degraded view — never the place to ship new features
- The menu bar title (the `💰 $X` string set via `self.title`) is fine to update for status; that is not the same as adding UI

If a feature needs to surface in both for accessibility reasons, ask the user first — default is dashboard-only.

## CRITICAL: Source of Truth

**Before making any feature changes, READ `docs/SOT.md` first.**

This is the master features & benefits document. When you:
- Add a new feature
- Modify existing functionality
- Change user-facing behavior

You MUST:
1. Read `docs/SOT.md` to understand current feature set
2. Update `docs/SOT.md` to reflect your changes
3. Update README.md with user-facing changes
4. Implement the feature
5. Update API call counts (see below) if relevant

## CRITICAL: API Call Minimization

**Toggl API has rate limits. Minimizing API calls is CRITICAL.**

Current API usage per operation:
- **Refresh Now**: 0-2 calls (0 if cached, 1-2 if stale)
  - Projects: 1 call (cached for 24h)
  - Time entries: 1 call (cached for 30min)
- **Refresh Projects**: 1 call (forces project refresh)

**When adding new features:**
1. ALWAYS check if data can be cached
2. UPDATE the API call counts in menu items
3. UPDATE this documentation with new call patterns
4. Use the audit log to verify call counts: `~/Library/Logs/toggl-api-audit.log`

**Cache TTLs:**
- Projects: 24 hours (`cache_ttl_projects`)
- Today's entries: 30 minutes (`cache_ttl_today`)
- Historical entries: Permanent (immutable)

## CRITICAL: Preferences UX & Delivery

When introducing or changing any user-facing preference:

1. Add/update the key in `preferences.py` defaults and validation
2. Expose it in the native preferences UI (`preferences_window.py`) unless explicitly internal-only
3. Ensure load/save/reset flows preserve the value
4. Update `docs/SOT.md` and `README.md` with user-facing behavior

When delivering completed feature work to the user:

1. Run relevant tests/checks
2. Restart the app service: `./restart_service.sh`
3. Verify runtime status: `./status_service.sh`
4. Report restart/status outcome in the handoff message

## Common Commands

### Service Management
```bash
./restart_service.sh          # Restart after code changes
./status_service.sh           # Check if running, memory, uptime
./install_service.sh          # Install as system service
./uninstall_service.sh        # Remove system service
```

### Logs
```bash
./logs.sh                     # View output log (live)
./logs.sh errors              # View error log (live)
./logs.sh clear               # Clear logs
```

### Testing
```bash
source venv/bin/activate && python menubar_app.py     # Test manually
source venv/bin/activate && python toggl_earnings.py  # Test CLI
```

### Cache
```bash
rm -rf ~/Library/Caches/TogglMenuBar/*    # Clear cache
```

## Typical Workflow

1. Make code changes
2. Test: `source venv/bin/activate && python menubar_app.py`
3. Restart: `./restart_service.sh`
4. Verify: `./status_service.sh`
5. Check: `./logs.sh`

## Key Files

- `dashboard_panel.py` - **Canonical UI**: WebKit popover with embedded HTML/CSS/JS (the dashboard the user actually sees)
- `menubar_app.py` - Menu bar host process; owns the 💰 status item, refresh loop, and a minimal rumps fallback menu used only when WebKit is unavailable
- `toggl_data.py` - Toggl API integration (GET/POST/PUT, caching, audit logging)
- `toggl_earnings.py` - CLI version
- `preferences.py` - Settings
- `preferences_window.py` - Native preferences UI
- `.env` - API credentials (never commit!)

## File Locations

```
~/Library/Application Support/TogglMenuBar/preferences.json  # Settings
~/Library/Caches/TogglMenuBar/                               # Cache
~/Library/Logs/freelancetracker-*.log                        # Logs
```

## Quick Reference

| User Says | Run |
|-----------|-----|
| "Restart the app" | `./restart_service.sh` |
| "Check status" | `./status_service.sh` |
| "Show logs" | `./logs.sh` |
| "Test changes" | `source venv/bin/activate && python menubar_app.py` |
| "Clear cache" | `rm -rf ~/Library/Caches/TogglMenuBar/*` |
