#!/bin/bash
# Freelance Tracker - Updater
# Run directly: ~/.freelance-tracker/update.sh
# Or via curl: curl -fsSL https://raw.githubusercontent.com/dennislysenko/freelance-tracker/main/update.sh | bash

set -e

# Determine install dir: prefer standard one-liner location,
# fall back to the directory this script lives in
INSTALL_DIR="$HOME/.freelance-tracker"
if [ ! -d "$INSTALL_DIR/.git" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
  if [ -d "$SCRIPT_DIR/.git" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
  fi
fi
if [ ! -d "$INSTALL_DIR/.git" ]; then
  echo "Could not find Freelance Tracker install directory."
  echo "Expected: $HOME/.freelance-tracker"
  exit 1
fi

PLIST_LABEL="com.freelancetracker.menubar"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo ""
echo "=================================="
echo "  Freelance Tracker Updater"
echo "=================================="
echo ""

echo "Pulling latest code..."
git -C "$INSTALL_DIR" pull
echo "Done."
echo ""

echo "Updating dependencies..."
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo "Done."
echo ""

echo "Restarting service..."
launchctl unload "$PLIST_DEST" 2>/dev/null || true
sleep 1
launchctl load "$PLIST_DEST"
sleep 1

if launchctl list | grep -q "$PLIST_LABEL"; then
  echo "=================================="
  echo "  Updated! Check your menu bar."
  echo "=================================="
  echo ""
else
  echo "Service failed to start. Check logs:"
  echo "  tail -f ~/Library/Logs/freelancetracker-error.log"
  exit 1
fi
