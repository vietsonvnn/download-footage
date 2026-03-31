@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title VidGrab v2.2 — Video Downloader
cd /d "%~dp0"

:: Auto-install if venv doesn't exist
if not exist "venv" (
    echo   First run detected — running installer...
    call install.bat
    exit /b
)

:: ─── Kill any existing VidGrab server on port 9123 ──────────
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":9123 " 2^>nul') do (
    if not "%%p"=="0" taskkill /F /PID %%p >nul 2>&1
)

:: ─── Find Python (try python / py / python3) ───────────────
set PYTHON=
python --version >nul 2>&1 && set PYTHON=python
if "!PYTHON!"=="" py --version >nul 2>&1 && set PYTHON=py
if "!PYTHON!"=="" python3 --version >nul 2>&1 && set PYTHON=python3
if "!PYTHON!"=="" (
    echo   [ERROR] Python not found. Run install.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

:: ─── Ensure ffmpeg & deno are in PATH (winget installs outside default PATH) ──
set "WINGET_LINKS=%LOCALAPPDATA%\Microsoft\WinGet\Links"
if exist "!WINGET_LINKS!" set "PATH=!WINGET_LINKS!;!PATH!"

where ffmpeg >nul 2>&1
if errorlevel 1 (
    for /f "delims=" %%F in ('dir /s /b "%LOCALAPPDATA%\Microsoft\WinGet\Packages\*ffmpeg.exe" 2^>nul') do (
        for %%D in ("%%~dpF.") do set "PATH=%%~fD;!PATH!"
    )
)

where deno >nul 2>&1
if errorlevel 1 (
    for /f "delims=" %%F in ('dir /s /b "%LOCALAPPDATA%\Microsoft\WinGet\Packages\*deno.exe" 2^>nul') do (
        for %%D in ("%%~dpF.") do set "PATH=%%~fD;!PATH!"
    )
)

:: ─── Quick dep check ────────────────────────────────────────
!PYTHON! -c "import flask, yt_dlp, requests" 2>nul
if errorlevel 1 (
    echo   [FIX] Missing packages — installing...
    pip install --quiet flask yt-dlp requests browser_cookie3
)

echo.
echo   Starting VidGrab v2.2...
echo   http://localhost:9123
echo   Press Ctrl+C to stop
echo.
set PYTHONIOENCODING=utf-8
!PYTHON! server.py
pause
