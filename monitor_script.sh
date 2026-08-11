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

# The main loop logs every ~41s, so a log this quiet means the process is wedged
# rather than just between cycles. Kept well above the cycle time (and above the
# camera's startup, which the IMX500 firmware upload can stretch out) so a slow
# start is never mistaken for a hang - the failure this catches lasted 15 days,
# so there is nothing to gain from reacting faster.
STALE_SECS=600

PIDS=$(pgrep -f "$SCRIPT_NAME.py")

if [ -n "$PIDS" ] && [ -f "$LOG_FILE" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$LOG_FILE") ))

    if [ "$AGE" -gt "$STALE_SECS" ]; then
        echo "$SCRIPT_NAME looks hung - log has not grown in ${AGE}s, restarting"
        kill -9 $PIDS
        # Wait for it to actually die before starting a fresh one, so the
        # replacement doesn't race the old process for the camera.
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
    # -u as a backstop: the staleness check above is only meaningful if the
    # script's output actually reaches the log promptly.
    nohup python -u "$SCRIPT_DIR/$SCRIPT_NAME.py" >> "$LOG_FILE" 2>&1 &
fi
