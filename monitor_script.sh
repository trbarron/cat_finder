#!/bin/bash
#
# Restart the CatFinder service if it isn't running, OR if it's alive but hung.
#
# picamera2/libcamera on the Pi can wedge inside a kernel VCHIQ call to the
# VideoCore GPU firmware (bcm2835_isp -> vc_sm_cma). When that happens the
# python process never crashes and never exits - it just stops making
# progress forever. A plain `pgrep` check can't tell a wedged process from a
# healthy one, so it silently never gets restarted. To catch that case we
# also check how long it's been since the log file last grew: the main loop
# writes to it roughly every ~41s, so if it's been quiet far longer than
# that, treat the process as hung and kill/restart it.
#
# Intended to be run periodically (e.g. from cron).

# Name of the Python script (without the .py extension)
SCRIPT_NAME="cat_finder"

# Resolve the directory this script lives in so it works regardless of cwd
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/$SCRIPT_NAME.log"

# Normal cycle is ~41s; anything several minutes quiet with no log growth
# means the process is hung, not just between cycles.
STALE_SECS=300

PID=$(pgrep -f "$SCRIPT_NAME.py")

if [ -n "$PID" ] && [ -f "$LOG_FILE" ]; then
    NOW=$(date +%s)
    LAST_WRITE=$(stat -c %Y "$LOG_FILE")
    AGE=$((NOW - LAST_WRITE))

    if [ "$AGE" -gt "$STALE_SECS" ]; then
        echo "$SCRIPT_NAME (pid $PID) looks hung - log has not grown in ${AGE}s, restarting"
        kill -9 "$PID"
        # Wait for the process to actually die before we start a fresh one.
        for _ in $(seq 1 10); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 1
        done
        PID=""
    fi
fi

if [ -n "$PID" ]; then
    echo "$SCRIPT_NAME is running"
else
    echo "$SCRIPT_NAME is not running, starting it"
    cd "$SCRIPT_DIR" || exit 1
    nohup python "$SCRIPT_DIR/$SCRIPT_NAME.py" >> "$LOG_FILE" 2>&1 &
fi
