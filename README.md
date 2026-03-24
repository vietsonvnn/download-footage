# VidGrab v2.2 — Video Downloader

Download video from YouTube, Envato Elements, Storyblocks, and 1000+ other sites.

## Quick Start

### macOS / Linux

```bash
# First time — install & run:
bash install.sh

# After that — just run:
bash start.sh
```

Or double-click `install.sh` (first time) / `start.sh` (after install).

### Windows

```
# First time — install & run:
Double-click install.bat

# After that — just run:
Double-click start.bat
```

App opens automatically at **http://localhost:9123**

## Requirements

| Dependency | Required | Auto-installed? |
|-----------|----------|----------------|
| Python 3.10+ | Yes | No — install from [python.org](https://python.org/downloads) |
| ffmpeg | Yes | Yes (via Homebrew/winget) |
| deno | Recommended | Yes (for YouTube JS challenges) |
| flask, yt-dlp, requests | Yes | Yes (in venv) |

## Features

### Video Download
- Paste 1 or many URLs (one per line)
- Auto-detect URLs from messy text, auto-fix missing `https://`
- Quality: 720p / 1080p / 1440p / 4K / MP3
- Real-time progress with speed & ETA
- `Ctrl/Cmd + Enter` to quick download

### Premium Sites (Envato Elements & Storyblocks)
- Import cookies via JSON (from Cookie-Editor browser extension)
- Or auto-read cookies from Chrome/Firefox/Safari
- Download individual videos or **bulk download by keyword search**

**Bulk search download:**
```
# Paste a search URL — VidGrab auto-expands to all videos:
https://www.storyblocks.com/all-video/search/B2-Spirit
https://elements.envato.com/video/stock-video?q=ocean
```

### Cookie Import (for Premium Sites)
1. Install [Cookie-Editor](https://cookie-editor.com/) extension in Chrome/Firefox
2. Go to `elements.envato.com` or `storyblocks.com` (logged in)
3. Click Cookie-Editor icon > **Export** > Copy
4. In VidGrab Settings > **Paste JSON** > Save

## Usage

| Action | How |
|--------|-----|
| Single download | Paste URL > Download |
| Bulk download | Paste multiple URLs (one per line) > Download |
| Search download | Paste search page URL > Download (auto-expands) |
| Choose quality | Click 720p / 1080p / 1440p / 4K / MP3 chips |
| Settings | Click gear icon (top right) |
| Open folder | Click folder icon (top right) |

## Project Structure

```
vidgrab/
├── server.py        # Backend (Flask + yt-dlp + premium downloaders)
├── index.html       # Frontend (single-file web UI)
├── install.sh       # macOS/Linux auto-installer
├── install.bat      # Windows auto-installer
├── start.sh         # macOS/Linux launcher
├── start.bat        # Windows launcher
├── cookies/         # Stored cookie files (auto-created)
├── config.json      # User settings (auto-created)
└── venv/            # Python virtual environment (auto-created)
```

## Supported Sites

**Built-in premium support:**
- Envato Elements (`elements.envato.com`)
- Storyblocks (`storyblocks.com`)

**Via yt-dlp (1000+ sites):**
YouTube, Vimeo, Dailymotion, Twitter/X, Facebook, Instagram, TikTok, Twitch, Bilibili, and many more.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Python not found" | Install Python 3.10+ from [python.org](https://python.org) |
| YouTube 403 error | Update yt-dlp: `venv/bin/pip install -U yt-dlp` |
| YouTube "page needs reload" | Install deno: `brew install deno` (macOS) |
| Envato/Storyblocks 403 | Import fresh cookies in Settings |
| Port 9123 in use | Kill old process: `lsof -ti:9123 \| xargs kill` |
| macOS "externally managed" | Already handled — uses venv, not system Python |

## Settings

Default download folder: `~/Downloads/VidGrab/`

All settings configurable via the gear icon in the web UI:
- Download quality & format
- Download folder path
- Filename template
- Concurrent downloads (1-4)
- Cookie browser (Chrome/Firefox/Safari)
