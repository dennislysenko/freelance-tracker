#!/bin/bash
# Install Toggl Menu Bar as a LaunchAgent (runs on login)

PLIST_NAME="com.freelancetracker.menubar.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
SOURCE_PLIST="$(pwd)/$PLIST_NAME"
DEST_PLIST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "Installing Freelance Tracker as a system service..."

# Create LaunchAgents directory if it doesn't exist
mkdir -p "$LAUNCH_AGENTS_DIR"

# Copy plist to LaunchAgents
cp "$SOURCE_PLIST" "$DEST_PLIST"
echo "✓ Copied plist to $DEST_PLIST"

# Load the service
launchctl unload "$DEST_PLIST" 2>/dev/null
launchctl load "$DEST_PLIST"
echo "✓ Service loaded"

# Check status
if launchctl list | grep -q "com.freelancetracker.menubar"; then
    echo "✓ Service is running!"
    echo ""
    echo "The menu bar app will now:"
    echo "  - Start automatically on login"
    echo "  - Restart if it crashes"
    echo "  - Run in the background"
    echo ""
    echo "Logs are stored at:"
    echo "  ~/Library/Logs/freelancetracker-output.log"
    echo "  ~/Library/Logs/freelancetracker-error.log"
else
    echo "✗ Failed to start service"
    exit 1
fi
