#!/bin/bash
# Restart Freelance Tracker service

PLIST_NAME="com.freelancetracker.menubar.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
DEST_PLIST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "Restarting Freelance Tracker..."

# Unload the service
launchctl unload "$DEST_PLIST" 2>/dev/null
echo "✓ Service stopped"

# Wait a moment
sleep 1

# Load the service
launchctl load "$DEST_PLIST"
echo "✓ Service started"

# Check status
sleep 1
if launchctl list | grep -q "com.freelancetracker.menubar"; then
    echo "✓ Freelance Tracker is running!"
    echo ""
    echo "Check your menu bar for the 💰 icon"
else
    echo "✗ Failed to start service"
    echo "Check logs at:"
    echo "  ~/Library/Logs/freelancetracker-error.log"
    exit 1
fi
