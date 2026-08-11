#!/bin/bash
# macOS / Linux launcher
cd "$(dirname "$0")"

echo "Checking dependencies..."
pip3 install -q -r requirements.txt 2>/dev/null || pip install -q -r requirements.txt

echo "Starting Taqua Silks..."
python3 launch.py || python launch.py
