#!/bin/bash

# Name of the Python script without the .py extension
SCRIPT_NAME="cat_finder"

# Directory containing cat_finder.py, .env, network.rpk, and labels.txt.
# The script uses relative paths, so it must be launched from this directory.
SCRIPT_DIR="$HOME/CatFinder"

# Check if the script is running
if pgrep -f "$SCRIPT_NAME.py" > /dev/null
then
    echo "$SCRIPT_NAME is running"
else
    echo "$SCRIPT_NAME is not running, starting it"
    cd "$SCRIPT_DIR" || exit 1
    nohup python3 "$SCRIPT_NAME.py" >> "$SCRIPT_DIR/cat_finder.log" 2>&1 &
fi
