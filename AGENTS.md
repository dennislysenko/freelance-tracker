# Agent Guide - Freelance Tracker

Quick reference for AI agents working on this project.

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
4. Check: `./logs.sh`

## Key Files

- `menubar_app.py` - Main menu bar app
- `toggl_data.py` - API integration
- `toggl_earnings.py` - CLI version
- `preferences.py` - Settings
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
