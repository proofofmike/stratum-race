#!/usr/bin/env bash
# StratumRace standalone — quick start script
# Creates a virtual environment, installs dependencies, builds the frontend
# if needed, and starts the server.

set -e

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10

# --- Check Python ---
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is not installed or not in PATH." >&2
    echo "Please install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ from https://python.org" >&2
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt "$MIN_PYTHON_MAJOR" ] || { [ "$PYTHON_MAJOR" -eq "$MIN_PYTHON_MAJOR" ] && [ "$PYTHON_MINOR" -lt "$MIN_PYTHON_MINOR" ]; }; then
    echo "Error: Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required, but found Python ${PYTHON_VERSION}." >&2
    exit 1
fi

echo "✓ Python ${PYTHON_VERSION} detected"

# --- Navigate to project root ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# --- Create/activate virtual environment ---
VENV_DIR="$PROJECT_ROOT/.venv"

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    # Remove any leftover partial venv from a previous failed attempt
    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
    fi
    echo "Creating virtual environment at .venv/..."
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        echo "" >&2
        echo "Error: Failed to create virtual environment." >&2
        echo "The 'venv' module is not available for Python ${PYTHON_VERSION}." >&2
        echo "" >&2
        # Detect OS and give specific instructions
        if [ -f /etc/debian_version ]; then
            echo "On Debian/Ubuntu, install it with:" >&2
            echo "  sudo apt install -y python${PYTHON_VERSION}-venv" >&2
        elif [ -f /etc/redhat-release ]; then
            echo "On RHEL/Fedora/CentOS, install it with:" >&2
            echo "  sudo dnf install -y python${PYTHON_MAJOR}-libs" >&2
        elif [ -f /etc/arch-release ]; then
            echo "On Arch Linux, venv is included with python — ensure python is fully installed." >&2
        else
            echo "Install the Python venv module for your system, e.g.:" >&2
            echo "  sudo apt install python${PYTHON_VERSION}-venv  (Debian/Ubuntu)" >&2
            echo "  sudo dnf install python${PYTHON_MAJOR}-libs    (Fedora/RHEL)" >&2
        fi
        echo "" >&2
        echo "Then re-run: ./standalone/run.sh" >&2
        exit 1
    fi
    echo "✓ Virtual environment created"
fi

# Activate the venv
source "$VENV_DIR/bin/activate"
echo "✓ Virtual environment activated"

# --- Install Python dependencies ---
echo "Installing Python dependencies..."
if ! pip install -q -r standalone/packaging/requirements.txt; then
    echo "Error: Failed to install Python dependencies." >&2
    echo "Try: source .venv/bin/activate && pip install -r standalone/packaging/requirements.txt" >&2
    exit 1
fi
echo "✓ Dependencies installed"

# --- Check frontend build ---
NEEDS_BUILD=false

if [ ! -f "frontend/dist/index.html" ]; then
    NEEDS_BUILD=true
elif [ -f "frontend/src/App.vue" ]; then
    # Rebuild if any source file is newer than the dist
    NEWEST_SRC=$(find frontend/src frontend/index.html -type f -newer frontend/dist/index.html 2>/dev/null | head -1)
    if [ -n "$NEWEST_SRC" ]; then
        NEEDS_BUILD=true
    fi
fi

if [ "$NEEDS_BUILD" = true ]; then
    echo ""
    echo "⚠ Frontend not built. Building now (requires Node.js + npm)..."
    if ! command -v npm &>/dev/null; then
        echo "Error: npm is not installed. Install Node.js from https://nodejs.org" >&2
        echo "Then run: cd frontend && npm install && npm run build" >&2
        exit 1
    fi
    cd frontend
    npm install --silent
    npm run build
    cd "$PROJECT_ROOT"
    echo "✓ Frontend built"
else
    echo "✓ Frontend already built (up to date)"
fi

# --- Start the server ---
echo ""
echo "Starting StratumRace..."
echo ""
python -m standalone.main "$@"
