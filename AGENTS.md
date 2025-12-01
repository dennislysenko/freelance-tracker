# Agent Guide - Freelance Tracker

Quick reference for AI agents working on this project.

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
