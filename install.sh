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

IS_MAC=false
[[ "$OSTYPE" == "darwin"* ]] && IS_MAC=true

# ─── 1. Auto-install Homebrew (macOS only) ──────────────────
if $IS_MAC && ! command -v brew &>/dev/null; then
    echo "  [WARN] Homebrew not found — installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    echo "  [OK] Homebrew installed"
fi

# ─── 2. Check Python 3.10+ ──────────────────────────────────
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        major=$("$cmd" -c "import sys; print(sys.version_info[0])" 2>/dev/null)
        minor=$("$cmd" -c "import sys; print(sys.version_info[1])" 2>/dev/null)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ] 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  [WARN] Python 3.10+ not found — installing..."
    if $IS_MAC; then
        brew install python@3.13
        PYTHON=$(brew --prefix python@3.13)/bin/python3
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y python3 python3-venv
        PYTHON=python3
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3
        PYTHON=python3
    else
        echo "  [ERROR] Cannot auto-install Python. Install from: https://python.org/downloads"
        read -p "  Press Enter to exit..." && exit 1
    fi
    echo "  [OK] Python installed"
fi

echo "  [OK] Found $PYTHON ($($PYTHON --version 2>&1))"

# ─── 3. Check ffmpeg ────────────────────────────────────────
if command -v ffmpeg &>/dev/null; then
    echo "  [OK] ffmpeg found"
else
    echo "  [WARN] ffmpeg not found — installing..."
    if $IS_MAC; then
        brew install ffmpeg
    elif command -v apt-get &>/dev/null; then
        sudo apt-get install -y ffmpeg
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y ffmpeg
    else
        echo "  [WARN] Cannot auto-install ffmpeg. Download from: https://ffmpeg.org/download.html"
    fi
    echo "  [OK] ffmpeg installed"
fi

# ─── 4. Check deno (for YouTube JS challenges) ──────────────
if command -v deno &>/dev/null; then
    echo "  [OK] deno found"
else
    echo "  [WARN] deno not found — installing..."
    if $IS_MAC && command -v brew &>/dev/null; then
        brew install deno
    else
        curl -fsSL https://deno.land/install.sh | sh
        export DENO_INSTALL="$HOME/.deno"
        export PATH="$DENO_INSTALL/bin:$PATH"
    fi
    echo "  [OK] deno installed"
fi

# ─── 5. Create virtual environment ──────────────────────────
if [ ! -d "venv" ]; then
    echo ""
    echo "  [SETUP] Creating virtual environment..."
    $PYTHON -m venv venv
fi
echo "  [OK] Virtual environment ready"

# ─── 6. Install Python packages ─────────────────────────────
echo "  [SETUP] Installing Python packages..."
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet flask yt-dlp requests browser_cookie3
echo "  [OK] All dependencies installed"

# ─── 7. Create cookies directory ─────────────────────────────
mkdir -p cookies

# ─── 8. Done ─────────────────────────────────────────────────
echo ""
echo "  ============================================"
echo "  Installation complete!"
echo "  ============================================"
echo ""
echo "  To start VidGrab, run:"
echo "    bash start.sh"
echo ""
read -p "  Start VidGrab now? [Y/n] " answer
if [[ "$answer" != "n" && "$answer" != "N" ]]; then
    bash "$(dirname "$0")/start.sh"
fi
