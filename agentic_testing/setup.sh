#!/bin/bash
# Setup script for agentic_testing module

set -e

echo "Setting up agentic_testing module..."
echo ""

# Check Python 3.11
if ! command -v python3.11 &> /dev/null; then
    echo "Error: Python 3.11 not found"
    echo "  Install with: brew install python@3.11"
    exit 1
fi

echo "Found: $(python3.11 --version)"

# Create venv
echo ""
echo "Creating virtual environment..."
python3.11 -m venv venv
echo "Done: Virtual environment created"

# Install core dependencies
echo ""
echo "Installing core dependencies..."
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
echo "Done: Core dependencies installed"

# Install mutahunter
echo ""
echo "Installing mutahunter from GitHub..."
./venv/bin/pip install -q git+https://github.com/codeintegrity-ai/mutahunter.git
echo "Done: Mutahunter installed"

# Apply bug fix
echo ""
echo "Applying mutahunter bug fix..."
CONTROLLER_PATH="./venv/lib/python3.11/site-packages/mutahunter/core/controller.py"
if [ -f "$CONTROLLER_PATH" ]; then
    sed -i '' '51 a\
        self.unexpected_test_error_mutants = 0
' "$CONTROLLER_PATH"
    echo "Done: Bug fix applied"
else
    echo "Warning: Could not find controller.py, bug fix not applied"
fi

echo ""
echo "Setup complete."
echo ""
echo "To use:"
echo "  cd .."
echo "  python3 -m agentic_testing.cli"
echo ""
echo "With mutahunter:"
echo "  python3 -m agentic_testing.cli --mutation-engine mutahunter"
