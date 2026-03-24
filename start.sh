#!/bin/bash
# VidGrab v2.2 — Start Server (macOS / Linux)
cd "$(dirname "$0")"

# Auto-install if venv doesn't exist
if [ ! -d "venv" ]; then
    echo "  First run detected — running installer..."
    bash install.sh
    exit $?
fi

source venv/bin/activate

# Quick dep check
python3 -c "import flask, yt_dlp, requests" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "  [FIX] Missing packages — installing..."
    pip install --quiet flask yt-dlp requests browser_cookie3
fi

echo ""
echo "  Starting VidGrab v2.2..."
echo "  http://localhost:9123"
echo "  Press Ctrl+C to stop"
echo ""
python3 server.py
