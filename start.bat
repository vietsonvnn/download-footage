@echo off
chcp 65001 >nul 2>&1
title VidGrab v2.2 — Video Downloader
cd /d "%~dp0"

:: Auto-install if venv doesn't exist
if not exist "venv" (
    echo   First run detected — running installer...
    call install.bat
    exit /b
)

call venv\Scripts\activate.bat

:: Quick dep check
python -c "import flask, yt_dlp, requests" 2>nul
if errorlevel 1 (
    echo   [FIX] Missing packages — installing...
    pip install --quiet flask yt-dlp requests browser_cookie3
)

echo.
echo   Starting VidGrab v2.2...
echo   http://localhost:9123
echo   Press Ctrl+C to stop
echo.
python server.py
pause
