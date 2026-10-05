#!/bin/bash
# Install Toggl Menu Bar as a LaunchAgent (runs on login)
set -e

PLIST_NAME="com.freelancetracker.menubar.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_PLIST="$APP_DIR/$PLIST_NAME.template"
DEST_PLIST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "Installing Freelance Tracker as a system service..."

# Create LaunchAgents directory if it doesn't exist
mkdir -p "$LAUNCH_AGENTS_DIR"

# Fill this checkout's paths into the template (the repo holds no user paths).
# XML-escaped and validated before replacing the installed plist, so an odd
# path can never leave an empty or broken LaunchAgent behind.
TMP_PLIST="$(mktemp)"
"$APP_DIR/venv/bin/python" - "$TEMPLATE_PLIST" "$TMP_PLIST" "$APP_DIR" "$HOME" <<'PY'
import plistlib
import sys
from xml.sax.saxutils import escape

src, dst, app_dir, home = sys.argv[1:]
with open(src) as f:
    text = f.read().replace("__APP_DIR__", escape(app_dir)).replace("__HOME__", escape(home))
plistlib.loads(text.encode())
with open(dst, "w") as f:
    f.write(text)
PY
mv "$TMP_PLIST" "$DEST_PLIST"
echo "✓ Wrote plist to $DEST_PLIST"

# Load the service
launchctl unload "$DEST_PLIST" 2>/dev/null || true
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
