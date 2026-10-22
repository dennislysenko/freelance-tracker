#!/bin/bash
# Check status of Freelance Tracker service

PLIST_NAME="com.freelancetracker.menubar.plist"
SERVICE_NAME="com.freelancetracker.menubar"

echo "Freelance Tracker Service Status"
echo "================================="
echo ""

# Check if service is loaded
if launchctl list | grep -q "$SERVICE_NAME"; then
    echo "✓ Service is RUNNING"

    # Get PID
    PID=$(launchctl list | grep "$SERVICE_NAME" | awk '{print $1}')
    if [ "$PID" != "-" ]; then
        echo "  PID: $PID"

        # Get process info
        ps -p "$PID" -o rss=,vsz=,pcpu=,etime= 2>/dev/null | while read rss vsz cpu time; do
            echo "  Memory: $((rss / 1024)) MB"
            echo "  CPU: ${cpu}%"
            echo "  Uptime: $time"
        done
    fi
else
    echo "✗ Service is NOT running"
    echo ""
    echo "To start it, run:"
    echo "  ./install_service.sh"
    exit 1
fi

echo ""
echo "LaunchAgent:"
echo "  ~/Library/LaunchAgents/$PLIST_NAME"

echo ""
echo "Logs:"
if [ -f ~/Library/Logs/freelancetracker-output.log ]; then
    LOG_SIZE=$(du -h ~/Library/Logs/freelancetracker-output.log | cut -f1)
    echo "  Output: ~/Library/Logs/freelancetracker-output.log ($LOG_SIZE)"
else
    echo "  Output: No log file yet"
fi

if [ -f ~/Library/Logs/freelancetracker-error.log ]; then
    LOG_SIZE=$(du -h ~/Library/Logs/freelancetracker-error.log | cut -f1)
    ERRORS=$(wc -l < ~/Library/Logs/freelancetracker-error.log | tr -d ' ')
    echo "  Errors: ~/Library/Logs/freelancetracker-error.log ($LOG_SIZE, $ERRORS lines)"
else
    echo "  Errors: No errors logged"
fi

echo ""
echo "Cache:"
if [ -d ~/Library/Caches/TogglMenuBar ]; then
    CACHE_SIZE=$(du -sh ~/Library/Caches/TogglMenuBar 2>/dev/null | cut -f1)
    CACHE_FILES=$(find ~/Library/Caches/TogglMenuBar -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "  ~/Library/Caches/TogglMenuBar ($CACHE_SIZE, $CACHE_FILES files)"
else
    echo "  No cache yet"
fi

echo ""
echo "Recent log entries:"
echo "-------------------"
if [ -f ~/Library/Logs/freelancetracker-output.log ]; then
    tail -n 5 ~/Library/Logs/freelancetracker-output.log
else
    echo "No logs yet"
fi
