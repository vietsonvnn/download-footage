@echo off
title VidGrab — Video Downloader
cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python not found. Install from https://python.org
    pause
    exit /b 1
)

:: Auto-install deps if missing
python -c "import flask" 2>nul || pip install flask yt-dlp

echo 🎬 Starting VidGrab...
python server.py
pause
