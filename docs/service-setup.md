# System Service Setup Guide

## Overview

Convert the Toggl Menu Bar app into a proper macOS system service that:
- **Starts automatically** on login
- **Restarts automatically** if it crashes
- **Uses standard macOS locations** for storage and preferences
- **Runs in the background** without needing a terminal

## Storage Locations (Centralized)

Following macOS conventions:

```
~/Library/Application Support/TogglMenuBar/
├── preferences.json          # User preferences

~/Library/Caches/TogglMenuBar/
├── projects.json             # Cached project data
├── daily_*.json              # Cached daily entries
├── weekly_*.json             # Cached weekly entries
└── monthly_*.json            # Cached monthly entries

~/Library/Logs/
├── toggl-menubar-output.log  # Standard output
└── toggl-menubar-error.log   # Error logs
```

## Preferences System

Preferences are stored in `~/Library/Application Support/TogglMenuBar/preferences.json`:

```json
{
  "refresh_interval": 1800,       // 30 minutes
  "show_notifications": true,
  "daily_goal": 400,
  "weekly_goal": 2000,
  "monthly_goal": 8000,
  "show_hours": true,
  "menu_bar_format": "💰 ${total:.0f}",
  "auto_start": true,
  "cache_ttl_projects": 86400,    // 24 hours
  "cache_ttl_today": 1800         // 30 minutes
}
```

## Installation

### Step 1: Install as LaunchAgent

```bash
cd /Users/dennis/dev/freelance-workflow
./install_service.sh
```

This will:
- Copy the LaunchAgent plist to `~/Library/LaunchAgents/`
- Load the service
- Start the menu bar app
- Configure it to start on login

### Step 2: Verify Installation

Check if the service is running:

```bash
launchctl list | grep com.toggl.menubar
```

You should see:
```
-	0	com.toggl.menubar
```

The `0` means it's running successfully.

### Step 3: Check the Menu Bar

Look at your menu bar (top right) - you should see `💰 $XXX`.

## Management Commands

### Start the Service
```bash
launchctl load ~/Library/LaunchAgents/com.toggl.menubar.plist
```

### Stop the Service
```bash
launchctl unload ~/Library/LaunchAgents/com.toggl.menubar.plist
```

### Restart the Service
```bash
launchctl unload ~/Library/LaunchAgents/com.toggl.menubar.plist
launchctl load ~/Library/LaunchAgents/com.toggl.menubar.plist
```

### Uninstall the Service
```bash
./uninstall_service.sh
```

### View Logs
```bash
# Standard output
tail -f ~/Library/Logs/toggl-menubar-output.log

# Errors
tail -f ~/Library/Logs/toggl-menubar-error.log
```

## How It Works

### LaunchAgent (com.toggl.menubar.plist)

The plist file tells macOS:
- **RunAtLoad**: Start the app when you log in
- **KeepAlive**: Restart if it crashes
- **StandardErrorPath**: Where to log errors
- **StandardOutPath**: Where to log output
- **WorkingDirectory**: Where to run from (for .env file)

### Persistence

The service will:
- Start automatically when you log in
- Keep running in the background
- Restart if it crashes
- Survive system sleep/wake

## Preferences Management

### View Preferences
```bash
cat ~/Library/Application\ Support/TogglMenuBar/preferences.json
```

### Edit Preferences
```bash
# Edit in your favorite editor
nano ~/Library/Application\ Support/TogglMenuBar/preferences.json

# Then restart the service
launchctl unload ~/Library/LaunchAgents/com.toggl.menubar.plist
launchctl load ~/Library/LaunchAgents/com.toggl.menubar.plist
```

### Reset to Defaults
```python
from preferences import reset_preferences
reset_preferences()
```

## Cache Management

### View Cache
```bash
ls -lh ~/Library/Caches/TogglMenuBar/
```

### Clear Cache
```bash
rm -rf ~/Library/Caches/TogglMenuBar/*
```

The cache will rebuild automatically on next refresh.

## Troubleshooting

### App Not Starting

1. **Check logs:**
   ```bash
   cat ~/Library/Logs/toggl-menubar-error.log
   ```

2. **Verify Python path:**
   ```bash
   which python
   # Should show: /Users/dennis/dev/freelance-workflow/venv/bin/python
   ```

3. **Check plist syntax:**
   ```bash
   plutil -lint ~/Library/LaunchAgents/com.toggl.menubar.plist
   ```

### App Crashing Repeatedly

Check error logs:
```bash
tail -n 50 ~/Library/Logs/toggl-menubar-error.log
```

Common issues:
- Missing .env file
- Invalid API token
- Network issues

### Service Not Loading

```bash
# Try loading with verbose output
launchctl load -w ~/Library/LaunchAgents/com.toggl.menubar.plist
```

### Can't See Menu Bar Icon

The app might be running but hidden. Check:
```bash
ps aux | grep menubar_app.py
```

If running, click on the menu bar (may be hidden under the notch on newer Macs).

## Updating the App

After making code changes:

```bash
# Restart the service
launchctl unload ~/Library/LaunchAgents/com.toggl.menubar.plist
launchctl load ~/Library/LaunchAgents/com.toggl.menubar.plist
```

Or use the shortcut:
```bash
./install_service.sh
```

## Uninstallation

### Complete Removal

```bash
# Uninstall service
./uninstall_service.sh

# Remove all data (optional)
rm -rf ~/Library/Application\ Support/TogglMenuBar
rm -rf ~/Library/Caches/TogglMenuBar
rm -f ~/Library/Logs/toggl-menubar-*.log
```

## Comparison: Manual vs Service

| Feature | Manual Run | LaunchAgent Service |
|---------|------------|---------------------|
| Auto-start on login | ❌ No | ✅ Yes |
| Survives logout | ❌ No | ✅ Yes |
| Auto-restart on crash | ❌ No | ✅ Yes |
| Terminal required | ✅ Yes | ❌ No |
| Logs saved | ❌ No | ✅ Yes |
| Standard storage | ❌ No | ✅ Yes |

## Security Considerations

### API Token Storage

Your API token is stored in `.env` in the project directory. This file should have restricted permissions:

```bash
chmod 600 .env
```

### Alternative: Keychain (Future Enhancement)

For better security, store the token in macOS Keychain:

```python
import keyring

# Store
keyring.set_password("TogglMenuBar", "api_token", "YOUR_TOKEN")

# Retrieve
token = keyring.get_password("TogglMenuBar", "api_token")
```

## Next Steps

1. **Install the service** with `./install_service.sh`
2. **Verify it works** - check menu bar and logs
3. **Customize preferences** - edit the JSON file
4. **Enjoy!** Your earnings tracker is now a proper macOS app

## Resources

- [launchd.info](https://www.launchd.info/) - LaunchAgent reference
- [Apple Developer Docs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
