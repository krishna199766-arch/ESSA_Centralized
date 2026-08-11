#!/bin/bash
# macOS double-click launcher.
# .command files open in Terminal automatically when double-clicked in Finder.

cd "$(dirname "$0")"

echo "=========================================="
echo "  Taqua Silks — Starting..."
echo "=========================================="
echo ""

# Find a working Python
PY=""
for cand in python3 python /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v $cand >/dev/null 2>&1; then
        PY=$cand
        break
    fi
done

if [ -z "$PY" ]; then
    echo "ERROR: Python 3 is not installed."
    echo "Install it from https://www.python.org/downloads/  or run:  brew install python"
    echo ""
    read -p "Press Enter to close..."
    exit 1
fi

echo "Using: $($PY --version)"
echo ""
echo "Installing dependencies (first run only)..."
$PY -m pip install -q -r requirements.txt --break-system-packages 2>/dev/null \
  || $PY -m pip install -q -r requirements.txt

echo ""
echo "Starting server — your browser will open at http://localhost:8000"
echo "Login:  admin / admin123"
echo "Press Ctrl+C in this window to stop the server."
echo ""

$PY launch.py

echo ""
read -p "Server stopped. Press Enter to close this window..."
