#!/bin/bash
# ============================================================
# VidGrab v2.2 — Auto Install & Run (macOS / Linux)
# Double-click this file or run: bash install.sh
# ============================================================
set -e
cd "$(dirname "$0")"

echo ""
echo "  ============================================"
echo "  VidGrab v2.2 — Auto Installer"
echo "  ============================================"
echo ""

# ─── 1. Check Python 3.10+ ──────────────────────────────────
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
        major=$("$cmd" -c "import sys; print(sys.version_info[0])" 2>/dev/null)
        minor=$("$cmd" -c "import sys; print(sys.version_info[1])" 2>/dev/null)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ] 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  [ERROR] Python 3.10+ not found."
    echo ""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "  Install with Homebrew:"
        echo "    brew install python@3.13"
    else
        echo "  Install from: https://python.org/downloads"
    fi
    echo ""
    read -p "  Press Enter to exit..."
    exit 1
fi

echo "  [OK] Found $PYTHON ($($PYTHON --version 2>&1))"

# ─── 2. Check ffmpeg ────────────────────────────────────────
if command -v ffmpeg &>/dev/null; then
    echo "  [OK] ffmpeg found"
else
    echo "  [WARN] ffmpeg not found — installing..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &>/dev/null; then
            brew install ffmpeg
        else
            echo "  [ERROR] Homebrew not found. Install ffmpeg manually:"
            echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            echo "    brew install ffmpeg"
        fi
    else
        if command -v apt-get &>/dev/null; then
            sudo apt-get install -y ffmpeg
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y ffmpeg
        else
            echo "  [ERROR] Install ffmpeg manually: https://ffmpeg.org/download.html"
        fi
    fi
fi

# ─── 3. Check deno (for YouTube JS challenges) ──────────────
if command -v deno &>/dev/null; then
    echo "  [OK] deno found"
else
    echo "  [WARN] deno not found — installing..."
    if [[ "$OSTYPE" == "darwin"* ]] && command -v brew &>/dev/null; then
        brew install deno
    else
        curl -fsSL https://deno.land/install.sh | sh
    fi
fi

# ─── 4. Create virtual environment ──────────────────────────
if [ ! -d "venv" ]; then
    echo ""
    echo "  [SETUP] Creating virtual environment..."
    $PYTHON -m venv venv
fi

echo "  [OK] Virtual environment ready"

# ─── 5. Install dependencies ────────────────────────────────
echo "  [SETUP] Installing Python packages..."
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet flask yt-dlp requests browser_cookie3

echo "  [OK] All dependencies installed"

# ─── 6. Create cookies directory ─────────────────────────────
mkdir -p cookies

# ─── 7. Done ─────────────────────────────────────────────────
echo ""
echo "  ============================================"
echo "  Installation complete!"
echo "  ============================================"
echo ""
echo "  To start VidGrab, run:"
echo "    bash start.sh"
echo ""
echo "  Or double-click start.sh"
echo ""
read -p "  Start VidGrab now? [Y/n] " answer
if [[ "$answer" != "n" && "$answer" != "N" ]]; then
    bash "$(dirname "$0")/start.sh"
fi
