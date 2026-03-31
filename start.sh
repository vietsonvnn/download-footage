#!/bin/bash
# VidGrab v2.2 — Start Server (macOS / Linux)
cd "$(dirname "$0")"

# Auto-install if venv doesn't exist
if [ ! -d "venv" ]; then
    echo "  First run detected — running installer..."
    bash install.sh
    exit $?
fi

# ─── Kill any existing VidGrab server on port 9123 ──────────
lsof -ti:9123 2>/dev/null | xargs kill -9 2>/dev/null

# ─── Find Python ────────────────────────────────────────────
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "  [ERROR] Python not found. Run install.sh first."
    exit 1
fi

source venv/bin/activate

# Quick dep check
$PYTHON -c "import flask, yt_dlp, requests" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "  [FIX] Missing packages — installing..."
    pip install --quiet flask yt-dlp requests browser_cookie3
fi

echo ""
echo "  Starting VidGrab v2.2..."
echo "  http://localhost:9123"
echo "  Press Ctrl+C to stop"
echo ""
$PYTHON server.py
