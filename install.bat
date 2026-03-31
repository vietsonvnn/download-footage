@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title VidGrab v2.2 — Installer
cd /d "%~dp0"

echo.
echo   ============================================
echo   VidGrab v2.2 — Auto Installer (Windows)
echo   ============================================
echo.

:: ─── 1. Find Python (try python / py / python3) ──────────────
set PYTHON=
python --version >nul 2>&1  && set PYTHON=python
if "!PYTHON!"=="" py --version >nul 2>&1 && set PYTHON=py
if "!PYTHON!"=="" python3 --version >nul 2>&1 && set PYTHON=python3

if "!PYTHON!"=="" (
    echo   [WARN] Python not found — installing via winget...
    winget install Python.Python.3.10 --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo   [ERROR] Could not auto-install Python.
        echo          Install manually from: https://www.python.org/downloads/
        echo          IMPORTANT: Check "Add Python to PATH" during install!
        echo.
        pause
        exit /b 1
    )
    echo   [OK] Python installed.
    echo   [INFO] Please re-run this installer to complete setup.
    echo.
    pause
    exit /b 0
)

for /f "tokens=2 delims= " %%v in ('!PYTHON! --version 2^>^&1') do set PYVER=%%v
echo   [OK] Python !PYVER! found ^(!PYTHON!^)

:: ─── 2. Check ffmpeg ─────────────────────────────────────────
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo   [WARN] ffmpeg not found — installing via winget...
    winget install Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo   [WARN] Could not auto-install ffmpeg.
        echo          Run manually: winget install Gyan.FFmpeg
        echo          Or download:  https://ffmpeg.org/download.html
    ) else (
        echo   [OK] ffmpeg installed
    )
) else (
    echo   [OK] ffmpeg found
)

:: ─── 3. Check deno (for YouTube JS challenges) ───────────────
deno --version >nul 2>&1
if errorlevel 1 (
    echo   [WARN] deno not found — installing via winget...
    winget install DenoLand.Deno --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo   [WARN] Could not auto-install deno.
        echo          Run manually: winget install DenoLand.Deno
    ) else (
        echo   [OK] deno installed
    )
) else (
    echo   [OK] deno found
)

:: ─── 4. Create virtual environment ───────────────────────────
if not exist "venv" (
    echo.
    echo   [SETUP] Creating virtual environment...
    !PYTHON! -m venv venv
)
echo   [OK] Virtual environment ready

:: ─── 5. Install Python packages ──────────────────────────────
echo   [SETUP] Installing Python packages...
call venv\Scripts\activate.bat
pip install --quiet flask yt-dlp requests browser_cookie3
echo   [OK] All dependencies installed

:: ─── 6. Create cookies directory ─────────────────────────────
if not exist "cookies" mkdir cookies

:: ─── 7. Done ─────────────────────────────────────────────────
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
