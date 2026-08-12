#!/bin/bash
#
# Restart cat_finder if it isn't running, or if it's running but wedged.
# Intended to be run periodically (e.g. from cron).
#
# The camera pipeline can hang inside the kernel, leaving the process alive but
# frozen, which a pgrep check alone reports as healthy. A log that has stopped
# growing is the tell.

# Name of the Python script (without the .py extension)
SCRIPT_NAME="cat_finder"

# Resolve the directory this script lives in so it works regardless of cwd
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/$SCRIPT_NAME.log"

# Comfortably above the ~41s loop and the camera's startup, so a slow start is
# never mistaken for a hang.
STALE_SECS=600

PIDS=$(pgrep -f "$SCRIPT_NAME.py")

if [ -n "$PIDS" ] && [ -f "$LOG_FILE" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$LOG_FILE") ))

    if [ "$AGE" -gt "$STALE_SECS" ]; then
        echo "$SCRIPT_NAME looks hung - log has not grown in ${AGE}s, restarting"
        kill -9 $PIDS
        # Let it die before starting a replacement, so they don't race for the camera.
        for _ in $(seq 1 10); do
            pgrep -f "$SCRIPT_NAME.py" > /dev/null || break
            sleep 1
        done
        PIDS=""
    fi
fi

if [ -n "$PIDS" ]; then
    echo "$SCRIPT_NAME is running"
else
    echo "$SCRIPT_NAME is not running, starting it"
    cd "$SCRIPT_DIR" || exit 1
    # -u so the staleness check above sees output promptly.
    nohup python -u "$SCRIPT_DIR/$SCRIPT_NAME.py" >> "$LOG_FILE" 2>&1 &
fi
