#!/bin/bash
# Uninstall Toggl Menu Bar LaunchAgent

PLIST_NAME="com.freelancetracker.menubar.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
DEST_PLIST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "Uninstalling Freelance Tracker service..."

# Unload the service
launchctl unload "$DEST_PLIST" 2>/dev/null
echo "✓ Service unloaded"

# Remove the plist
rm -f "$DEST_PLIST"
echo "✓ Plist removed"

echo "Service uninstalled successfully!"
echo ""
echo "You can still run the app manually with:"
echo "  source venv/bin/activate && python menubar_app.py"
