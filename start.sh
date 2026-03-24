#!/bin/bash
# VidGrab — Double-click to start (macOS/Linux)
cd "$(dirname "$0")"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Install from https://python.org"
    read -p "Press Enter to exit..."
    exit 1
fi

# Auto-install deps if missing
python3 -c "import flask" 2>/dev/null || pip3 install flask yt-dlp

echo "🎬 Starting VidGrab..."
python3 server.py
