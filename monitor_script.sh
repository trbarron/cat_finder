#!/bin/bash

# Name of the Python script without the .py extension
SCRIPT_NAME="cat_finder"

# Check if the script is running
if pgrep -f "$SCRIPT_NAME.py" > /dev/null
then
    echo "$SCRIPT_NAME is running"
else
    echo "$SCRIPT_NAME is not running, starting it"
    nohup python ~/CatFinder/$SCRIPT_NAME.py &
fi
