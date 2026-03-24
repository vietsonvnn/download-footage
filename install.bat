@echo off
chcp 65001 >nul 2>&1
title VidGrab v2.2 — Installer
cd /d "%~dp0"

echo.
echo   ============================================
echo   VidGrab v2.2 — Auto Installer (Windows)
echo   ============================================
echo.

:: ─── 1. Check Python ────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not found.
    echo.
    echo   Install Python 3.10+ from:
    echo     https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: Check "Add Python to PATH" during install!
    echo.
    pause
    exit /b 1
)

:: Check Python version >= 3.10
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   [OK] Python %PYVER% found

:: ─── 2. Check ffmpeg ────────────────────────────────────────
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo   [WARN] ffmpeg not found — trying to install...
    winget install ffmpeg >nul 2>&1
    if errorlevel 1 (
        echo   [WARN] Could not auto-install ffmpeg.
        echo          Download from: https://ffmpeg.org/download.html
        echo          Or run: winget install ffmpeg
    ) else (
        echo   [OK] ffmpeg installed
    )
) else (
    echo   [OK] ffmpeg found
)

:: ─── 3. Create virtual environment ──────────────────────────
if not exist "venv" (
    echo.
    echo   [SETUP] Creating virtual environment...
    python -m venv venv
)
echo   [OK] Virtual environment ready

:: ─── 4. Install dependencies ────────────────────────────────
echo   [SETUP] Installing Python packages...
call venv\Scripts\activate.bat
pip install --quiet --upgrade pip
pip install --quiet flask yt-dlp requests browser_cookie3

echo   [OK] All dependencies installed

:: ─── 5. Create cookies directory ────────────────────────────
if not exist "cookies" mkdir cookies

:: ─── 6. Done ────────────────────────────────────────────────
echo.
echo   ============================================
echo   Installation complete!
echo   ============================================
echo.
echo   To start VidGrab, double-click: start.bat
echo.

set /p answer="  Start VidGrab now? [Y/n] "
if /i not "%answer%"=="n" (
    call "%~dp0start.bat"
)
pause
