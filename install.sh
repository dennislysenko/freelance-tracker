#!/bin/bash
# Freelance Tracker - One-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/dennislysenko/freelance-tracker/main/install.sh | bash

set -e

REPO="https://github.com/dennislysenko/freelance-tracker.git"
INSTALL_DIR="$HOME/.freelance-tracker"
PLIST_LABEL="com.freelancetracker.menubar"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  💰 Freelance Tracker Installer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 1. Preflight checks
if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ python3 is required but not found."
  echo "  Install it with: brew install python"
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "✗ git is required but not found."
  echo "  Install it with: brew install git"
  exit 1
fi

# 2. Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "→ Updating existing install..."
  git -C "$INSTALL_DIR" pull --quiet
  echo "✓ Updated to latest"
else
  echo "→ Installing to $INSTALL_DIR..."
  git clone --quiet "$REPO" "$INSTALL_DIR"
  echo "✓ Downloaded"
fi

# 3. Venv + deps
echo "→ Setting up Python environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo "✓ Dependencies installed"

# 4. Credentials (skip if .env already configured)
ENV_FILE="$INSTALL_DIR/.env"
if [ -f "$ENV_FILE" ] && grep -q "TOGGL_API_TOKEN=." "$ENV_FILE"; then
  echo "✓ Credentials already configured (delete $ENV_FILE to re-enter)"
else
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Toggl API Setup"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Find your token at:"
  echo "  https://track.toggl.com/profile → API Token"
  echo ""

  # Read from /dev/tty so prompts work when piped through bash
  exec < /dev/tty
  read -rp "  Toggl API Token: " TOGGL_API_TOKEN
  read -rp "  Workspace ID (press Enter to auto-detect): " TOGGL_WORKSPACE_ID
  echo ""

  printf "TOGGL_API_TOKEN=%s\nTOGGL_WORKSPACE_ID=%s\n" \
    "$TOGGL_API_TOKEN" "$TOGGL_WORKSPACE_ID" > "$ENV_FILE"
  echo "✓ Credentials saved"
fi

# 5. Generate plist with correct paths (no hardcoded user paths)
echo "→ Installing system service..."
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_DEST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/venv/bin/python</string>
        <string>$INSTALL_DIR/menubar_app.py</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/freelancetracker-error.log</string>

    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/freelancetracker-output.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
</dict>
</plist>
EOF

# 6. Load LaunchAgent
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

# 7. Verify + success
sleep 1
if launchctl list | grep -q "$PLIST_LABEL"; then
  echo "✓ Service running"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ✅ Installed! Check your menu bar for 💰"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  Logs:    tail -f ~/Library/Logs/freelancetracker-output.log"
  echo "  Uninstall: $INSTALL_DIR/uninstall_service.sh"
  echo ""
else
  echo "✗ Service failed to start. Check logs:"
  echo "  tail -f ~/Library/Logs/freelancetracker-error.log"
  exit 1
fi
