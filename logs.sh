#!/bin/bash
# View Freelance Tracker logs

OUTPUT_LOG="$HOME/Library/Logs/freelancetracker-output.log"
ERROR_LOG="$HOME/Library/Logs/freelancetracker-error.log"

# Parse arguments
case "$1" in
    error|errors)
        echo "Viewing error log (Ctrl+C to exit):"
        echo "===================================="
        if [ -f "$ERROR_LOG" ]; then
            tail -f "$ERROR_LOG"
        else
            echo "No error log file found at: $ERROR_LOG"
        fi
        ;;

    all)
        echo "Viewing all logs (output + errors, Ctrl+C to exit):"
        echo "===================================================="
        if [ -f "$OUTPUT_LOG" ] && [ -f "$ERROR_LOG" ]; then
            tail -f "$OUTPUT_LOG" "$ERROR_LOG"
        elif [ -f "$OUTPUT_LOG" ]; then
            tail -f "$OUTPUT_LOG"
        else
            echo "No log files found"
        fi
        ;;

    clear)
        echo "Clearing logs..."
        > "$OUTPUT_LOG" 2>/dev/null
        > "$ERROR_LOG" 2>/dev/null
        echo "✓ Logs cleared"
        ;;

    *)
        echo "Viewing output log (Ctrl+C to exit):"
        echo "====================================="
        if [ -f "$OUTPUT_LOG" ]; then
            tail -f "$OUTPUT_LOG"
        else
            echo "No output log file found at: $OUTPUT_LOG"
            echo ""
            echo "The service may not be running yet. Start it with:"
            echo "  ./install_service.sh"
        fi
        ;;
esac
