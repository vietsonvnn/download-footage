#!/usr/bin/env python3
"""
VidGrab v2.2 — Lightweight Video Downloader
Flask backend + yt-dlp engine | macOS & Windows
Hardened by 4 QA agents + smart URL sanitizer
"""

import atexit
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_from_directory

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None

# ─── Ensure ffmpeg & deno are discoverable (winget installs outside default PATH) ───
def _ensure_winget_tools_in_path():
    if platform.system() != "Windows":
        return
    import glob
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return
    for tool in ("ffmpeg.exe", "deno.exe"):
        try:
            subprocess.run([tool.replace(".exe", ""), "--version"], capture_output=True, timeout=3)
            continue  # already in PATH
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        for exe in glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "**", tool), recursive=True):
            os.environ["PATH"] = os.path.dirname(exe) + os.pathsep + os.environ.get("PATH", "")
            break

_ensure_winget_tools_in_path()

# ─── Constants ───────────────────────────────────────────────────────
MAX_BULK_URLS = 50
MAX_QUEUE_SIZE = 100
VALID_QUALITIES = {"360", "480", "720", "1080", "1440", "2160"}
VALID_FORMATS = {"mp4", "mkv", "webm", "mp3"}
AUTO_CLEAN_SECONDS = 600

# ─── Config ──────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "quality": "1080",
    "format": "mp4",
    "download_dir": str(Path.home() / "Downloads" / "VidGrab"),
    "filename_template": "%(title)s.%(ext)s",
    "concurrent_downloads": 2,
    "cookie_browser": "chrome",
}

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    return {**DEFAULT_CONFIG, **saved}
        except (json.JSONDecodeError, OSError):
            try:
                CONFIG_FILE.unlink()
            except OSError:
                pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def validate_config(cfg):
    if str(cfg.get("quality")) not in VALID_QUALITIES:
        cfg["quality"] = DEFAULT_CONFIG["quality"]
    if cfg.get("format") not in VALID_FORMATS:
        cfg["format"] = DEFAULT_CONFIG["format"]
    try:
        cd = int(cfg.get("concurrent_downloads", 2))
        cfg["concurrent_downloads"] = max(1, min(10, cd))
    except (ValueError, TypeError):
        cfg["concurrent_downloads"] = DEFAULT_CONFIG["concurrent_downloads"]
    if not cfg.get("download_dir", "").strip():
        cfg["download_dir"] = DEFAULT_CONFIG["download_dir"]
    tmpl = cfg.get("filename_template", DEFAULT_CONFIG["filename_template"])
    tmpl = tmpl.replace("../", "").replace("..\\", "").replace("/", "").replace("\\", "")
    cfg["filename_template"] = tmpl or DEFAULT_CONFIG["filename_template"]
    return cfg

config = validate_config(load_config())

# ─── URL Sanitizer ───────────────────────────────────────────────────
VIDEO_DOMAINS = [
    "youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com",
    "vimeo.com", "dailymotion.com", "facebook.com", "fb.watch",
    "tiktok.com", "instagram.com", "twitter.com", "x.com",
    "twitch.tv", "bilibili.com", "nicovideo.jp",
    "storyblocks.com", "www.storyblocks.com",
    "elements.envato.com", "envato.com", "app.envato.com",
    "dvidshub.net", "www.dvidshub.net",
]

_URL_RE = re.compile(r'https?://[^\s<>\[\]\(\)\"\'「」\u201c\u201d\u2018\u2019,;!]+', re.I)
_NAKED_RE = re.compile(
    r'(?:www\.)?' + r'(?:' + '|'.join(re.escape(d) for d in VIDEO_DOMAINS) + r')[/\?][^\s<>\[\]\(\)\"\'「」,;!]*', re.I
)
_JUNK = '\u200b\u200c\u200d\ufeff\u00a0\u2028\u2029\u200e\u200f\t\r'
_WRAP = {'"':'"', "'":"'", '\u201c':'\u201d', '\u2018':'\u2019', '「':'」', '<':'>', '[':']', '(':')'}
_TRAIL = set('.,;:!?)>]」\u201d\u2019\'\"')

def _clean_url(raw):
    if not raw:
        return None
    s = raw.strip()
    for c in _JUNK:
        s = s.replace(c, '')
    s = s.strip()
    if not s:
        return None
    for _ in range(3):
        if len(s) >= 2 and s[0] in _WRAP and s[-1] == _WRAP[s[0]]:
            s = s[1:-1].strip()
    while s and s[-1] in _TRAIL:
        if s[-1] == ')' and '(' in s:
            break
        s = s[:-1]
    return s if s else None

def extract_urls(raw_text):
    """Extract and clean URLs from messy user input. Returns (urls, stats)."""
    if not raw_text or not isinstance(raw_text, str):
        return [], {"total_lines": 0, "extracted": 0, "fixed": 0, "skipped": 0}
    text = raw_text.replace('\r\n', '\n').replace('\r', '\n').replace('\t', '\n')
    lines = text.split('\n')
    found, fixed = [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        cleaned = _clean_url(line)
        # Strategy 1: Line is one clean URL
        if cleaned and cleaned.startswith(('http://', 'https://')) and ' ' not in cleaned:
            found.append(cleaned); continue
        # Strategy 2: Naked domain (youtube.com/...) without protocol
        if cleaned and ' ' not in cleaned:
            m = _NAKED_RE.match(cleaned)
            if m:
                found.append('https://' + cleaned); fixed += 1; continue
        # Strategy 3: Extract http(s) URLs from text
        http_urls = _URL_RE.findall(line)
        if http_urls:
            for u in http_urls:
                cu = _clean_url(u)
                if cu: found.append(cu)
            continue
        # Strategy 4: Naked domain embedded in text
        naked = _NAKED_RE.findall(line)
        if naked:
            for u in naked:
                cu = _clean_url(u)
                if cu: found.append('https://' + cu); fixed += 1
            continue
        # Strategy 5: Bare YouTube video ID (exactly 11 chars)
        if cleaned and re.match(r'^[A-Za-z0-9_-]{11}$', cleaned):
            found.append(f'https://www.youtube.com/watch?v={cleaned}'); fixed += 1; continue
    # Deduplicate preserving order
    seen, unique = set(), []
    for u in found:
        if u not in seen: seen.add(u); unique.append(u)
    total = sum(1 for l in lines if l.strip())
    return unique, {"total_lines": total, "extracted": len(unique), "fixed": fixed, "skipped": total - len(found)}

# ─── Premium Site Downloader (Envato Elements & Storyblocks) ─────────
PREMIUM_DOMAINS = {
    "elements.envato.com": "envato",
    "app.envato.com": "envato",
    "www.storyblocks.com": "storyblocks",
    "storyblocks.com": "storyblocks",
    "envato.com": "envato",
    "www.dvidshub.net": "dvidshub",
    "dvidshub.net": "dvidshub",
}

COOKIES_DIR = Path(__file__).parent / "cookies"
COOKIES_DIR.mkdir(exist_ok=True)

def _get_url_domain(url):
    """Return domain from URL."""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""

def is_premium_url(url):
    """Check if URL belongs to a premium stock site."""
    domain = _get_url_domain(url)
    if domain in PREMIUM_DOMAINS:
        return True
    # Substring fallback for variants like www.elements.envato.com
    if domain and ("envato.com" in domain or "storyblocks.com" in domain or "dvidshub.net" in domain):
        return True
    return False

def _get_premium_site_type(url):
    """Return the site type for a premium URL."""
    domain = _get_url_domain(url)
    if domain in PREMIUM_DOMAINS:
        return PREMIUM_DOMAINS[domain]
    if domain and "envato.com" in domain:
        return "envato"
    if domain and "storyblocks.com" in domain:
        return "storyblocks"
    if domain and "dvidshub.net" in domain:
        return "dvidshub"
    return "unknown"

def _load_cookie_file(site_type):
    """Load cookies from a saved JSON cookie file for a site.
    Supports formats: browser extension export (list of cookie objects),
    Netscape/Mozilla format, and EditThisCookie format.
    Returns a requests.Session or None.
    """
    cookie_file = COOKIES_DIR / f"{site_type}.json"
    if not cookie_file.exists():
        return None
    try:
        with open(cookie_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    session = requests.Session()

    # Handle list of cookie objects (most common browser extension export)
    if isinstance(data, list):
        for c in data:
            if not isinstance(c, dict):
                continue
            name = c.get("name") or c.get("Name", "")
            value = c.get("value") or c.get("Value", "")
            domain = c.get("domain") or c.get("Domain", "")
            path = c.get("path") or c.get("Path", "/")
            secure = c.get("secure") or c.get("Secure", False)
            if name and value:
                session.cookies.set(
                    name, value, domain=domain, path=path,
                    secure=bool(secure),
                )
        if len(session.cookies) > 0:
            return session
        return None

    # Handle dict format {"cookies": [...]}
    if isinstance(data, dict) and "cookies" in data:
        for c in data["cookies"]:
            if not isinstance(c, dict):
                continue
            name = c.get("name", "")
            value = c.get("value", "")
            domain = c.get("domain", "")
            path = c.get("path", "/")
            secure = c.get("secure", False)
            if name and value:
                session.cookies.set(
                    name, value, domain=domain, path=path,
                    secure=bool(secure),
                )
        if len(session.cookies) > 0:
            return session

    return None

def _get_cookie_file_info(site_type):
    """Get info about a stored cookie file."""
    cookie_file = COOKIES_DIR / f"{site_type}.json"
    if not cookie_file.exists():
        return None
    try:
        stat = cookie_file.stat()
        with open(cookie_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        if isinstance(data, list):
            count = len([c for c in data if isinstance(c, dict) and c.get("name")])
        elif isinstance(data, dict) and "cookies" in data:
            count = len([c for c in data["cookies"] if isinstance(c, dict) and c.get("name")])
        return {
            "exists": True,
            "cookie_count": count,
            "imported_at": stat.st_mtime,
            "size": stat.st_size,
        }
    except Exception:
        return None

# ─── Universal Cookie Import (one JSON → all sites) ─────────────────
# Domain patterns to match cookies to each site
COOKIE_DOMAIN_MAP = {
    "envato": ["envato.com"],
    "storyblocks": ["storyblocks.com"],
    "dvidshub": ["dvidshub.net"],
    "youtube": ["youtube.com", "youtu.be", "google.com", "googleapis.com", "googlevideo.com"],
}

def _match_cookie_site(cookie_domain):
    """Match a cookie's domain to known sites. Returns list of matching site_types."""
    if not cookie_domain:
        return []
    d = cookie_domain.lstrip(".")
    matched = []
    for site_type, patterns in COOKIE_DOMAIN_MAP.items():
        for p in patterns:
            if d == p or d.endswith("." + p):
                matched.append(site_type)
                break
    return matched

def _split_and_save_cookies(cookies_list):
    """Split a list of cookies by domain and save to per-site files.
    Also generates a Netscape cookie file for yt-dlp.
    Returns dict of {site_type: count}.
    """
    buckets = {st: [] for st in COOKIE_DOMAIN_MAP}
    all_cookies = []

    for c in cookies_list:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("Name", "")
        value = c.get("value") or c.get("Value", "")
        if not name or not value:
            continue
        all_cookies.append(c)
        domain = c.get("domain") or c.get("Domain", "")
        sites = _match_cookie_site(domain)
        for st in sites:
            buckets[st].append(c)

    result = {}
    # Save per-site JSON files
    for site_type, site_cookies in buckets.items():
        if site_type == "youtube":
            continue  # youtube gets Netscape format below
        if site_cookies:
            cookie_file = COOKIES_DIR / f"{site_type}.json"
            with open(cookie_file, "w", encoding="utf-8") as f:
                json.dump(site_cookies, f, indent=2, ensure_ascii=False)
            result[site_type] = len(site_cookies)

    # Generate Netscape cookie file for yt-dlp (ALL cookies, not just youtube)
    _save_netscape_cookies(all_cookies)
    if buckets["youtube"]:
        result["youtube"] = len(buckets["youtube"])

    return result

def _save_netscape_cookies(cookies_list):
    """Convert cookies to Netscape/Mozilla format for yt-dlp --cookies."""
    cookie_file = COOKIES_DIR / "cookies.txt"
    lines = ["# Netscape HTTP Cookie File", "# Generated by VidGrab", ""]
    for c in cookies_list:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("Name", "")
        value = c.get("value") or c.get("Value", "")
        if not name or not value:
            continue
        domain = c.get("domain") or c.get("Domain", "")
        if not domain.startswith("."):
            domain = "." + domain
        path = c.get("path") or c.get("Path", "/")
        secure = "TRUE" if c.get("secure") or c.get("Secure") else "FALSE"
        # httpOnly → domain starts with #HttpOnly_
        http_only = c.get("httpOnly") or c.get("HttpOnly", False)
        expiry = str(int(c.get("expirationDate") or c.get("expiry") or c.get("expires") or 0))
        host_only = not domain.startswith(".")
        include_subdomains = "FALSE" if host_only else "TRUE"
        prefix = "#HttpOnly_" if http_only else ""
        lines.append(f"{prefix}{domain}\t{include_subdomains}\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
    with open(cookie_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def _get_premium_session(site_type, browser="chrome"):
    """Get authenticated session: prefer cookie file, fallback to browser cookies."""
    # Priority 1: Imported cookie file
    session = _load_cookie_file(site_type)
    if session:
        return session, "cookie_file"

    # Priority 2: Browser cookies
    if not browser_cookie3:
        return None, None
    try:
        # Map site_type to domain for browser cookie extraction
        domain_map = {
            "envato": "envato.com",
            "storyblocks": "storyblocks.com",
            "dvidshub": "dvidshub.net",
        }
        domain = domain_map.get(site_type, "")
        loaders = {
            "chrome": browser_cookie3.chrome,
            "firefox": browser_cookie3.firefox,
            "safari": browser_cookie3.safari,
        }
        loader = loaders.get(browser, browser_cookie3.chrome)
        cj = loader(domain_name=domain)
        session = requests.Session()
        session.cookies = cj
        return session, "browser"
    except Exception:
        return None, None

# ─── Search URL Detection & Scraper ──────────────────────────────────
_SB_SEARCH_RE = re.compile(
    r'https?://(?:www\.)?storyblocks\.com/'
    r'(?:all-video|video|audio|images)/search/'
)
# Envato search: /video/stock-video?q=keyword or /video?term=keyword
_ENVATO_SEARCH_RE = re.compile(
    r'https?://(?:elements|app)\.envato\.com/'
    r'(?:[\w-]+/)*(?:video|photos|music|sound-effects|graphics|templates|stock-video)'
    r'(?:/[\w-]+)*\?.*(?:q=|term=|search=)'
)
# Envato category: /video/stock-video (no item ID at end)
_ENVATO_CATEGORY_RE = re.compile(
    r'https?://(?:elements|app)\.envato\.com/'
    r'(?:[\w-]+/)*(?:video|photos|music|sound-effects|graphics|templates|stock-video)'
    r'(?:/[\w-]+)?/?$'
)

_DVIDSHUB_SEARCH_RE = re.compile(
    r'https?://(?:www\.)?dvidshub\.net/search/\?.*(?:q=|query=)', re.I
)

def _is_envato_item_url(url):
    """Check if an Envato URL points to a specific item (not a search page)."""
    path = urlparse(url).path.rstrip("/")
    # app.envato.com item: /search/stock-video/{UUID}
    if re.search(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', path, re.I):
        return True
    # elements.envato.com item: slug ending with -UPPERCASE_ID
    last_part = path.split("/")[-1] if path else ""
    if re.search(r'-[A-Z0-9]{5,}$', last_part):
        return True
    return False

def is_search_url(url):
    """Detect if a URL is a search/category page rather than a single item."""
    if _SB_SEARCH_RE.match(url):
        return "storyblocks"
    # Envato: check for search params or category pages, but NOT direct item links
    if _ENVATO_SEARCH_RE.match(url):
        if _is_envato_item_url(url):
            return None  # Direct item, not a search
        return "envato"
    if _ENVATO_CATEGORY_RE.match(url):
        if _is_envato_item_url(url):
            return None
        return "envato"
    # DVIDSHUB search pages
    if _DVIDSHUB_SEARCH_RE.match(url):
        return "dvidshub"
    return None

def _scrape_storyblocks_search(session, search_url, max_pages=5):
    """Scrape all video URLs from a Storyblocks search page (multi-page).
    Returns list of (video_url, title) tuples.
    """
    from urllib.parse import urlencode, urlparse, parse_qs, urljoin

    results = []
    seen_urls = set()

    for page in range(1, max_pages + 1):
        # Add page parameter
        parsed = urlparse(search_url)
        qs = parse_qs(parsed.query)
        qs["page"] = [str(page)]
        page_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(qs, doseq=True)}"

        try:
            resp = session.get(page_url, timeout=30)
            if resp.status_code != 200:
                break
            html = resp.text
        except Exception:
            break

        page_results = []

        # Method 1: __NEXT_DATA__ (Next.js)
        m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                props = data.get("props", {}).get("pageProps", {})
                # Search results in various possible keys
                items = (
                    props.get("searchResults", {}).get("results")
                    or props.get("results")
                    or props.get("stockItems")
                    or props.get("items")
                    or []
                )
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    # Build video page URL from slug/id
                    slug = item.get("slug") or item.get("url") or ""
                    item_id = item.get("id") or item.get("contentId") or ""
                    title = item.get("title") or item.get("name") or "Storyblocks Video"
                    content_type = item.get("contentType") or item.get("type") or "video"

                    # Construct item URL
                    item_url = ""
                    if slug and slug.startswith("http"):
                        item_url = slug
                    elif slug:
                        # Storyblocks URL pattern: /video/slug
                        base = "video" if "video" in content_type.lower() else content_type.lower()
                        item_url = f"https://www.storyblocks.com/{base}/{slug}"
                    elif item_id:
                        item_url = f"https://www.storyblocks.com/video/{item_id}"

                    if item_url and item_url not in seen_urls:
                        seen_urls.add(item_url)
                        page_results.append((item_url, title))
            except (json.JSONDecodeError, AttributeError, KeyError):
                pass

        # Method 2: Scrape HTML links as fallback
        if not page_results:
            # Look for video links in the page
            link_patterns = [
                r'href="(/video/[^"]+)"',
                r'href="(/stock-video/[^"]+)"',
                r'href="(https://www\.storyblocks\.com/video/[^"]+)"',
            ]
            for pattern in link_patterns:
                for match in re.finditer(pattern, html):
                    href = match.group(1)
                    if href.startswith("/"):
                        href = f"https://www.storyblocks.com{href}"
                    # Skip search/category links
                    if "/search/" in href or "/category/" in href:
                        continue
                    if href not in seen_urls:
                        seen_urls.add(href)
                        # Extract title from URL slug
                        slug_part = href.rstrip("/").split("/")[-1]
                        title = slug_part.rsplit("-", 1)[0].replace("-", " ").title()
                        page_results.append((href, title))

        # Method 3: Try Storyblocks internal search API
        if not page_results and page == 1:
            # Extract search keyword from URL
            path_parts = parsed.path.rstrip("/").split("/")
            keyword = path_parts[-1] if path_parts else ""
            if keyword:
                try:
                    api_urls = [
                        f"https://www.storyblocks.com/api/v2/search?type=video&query={keyword}&page={page}",
                        f"https://www.storyblocks.com/api/search?projectType=all-video&searchTerm={keyword}&page={page}",
                    ]
                    for api_url in api_urls:
                        try:
                            api_resp = session.get(api_url, timeout=15)
                            if api_resp.status_code == 200:
                                api_data = api_resp.json()
                                items = api_data.get("results") or api_data.get("items") or api_data.get("data", {}).get("results") or []
                                for item in items:
                                    if not isinstance(item, dict):
                                        continue
                                    slug = item.get("slug") or item.get("url") or ""
                                    title = item.get("title") or "Storyblocks Video"
                                    item_id = item.get("id") or ""
                                    if slug and slug.startswith("http"):
                                        item_url = slug
                                    elif slug:
                                        item_url = f"https://www.storyblocks.com/video/{slug}"
                                    elif item_id:
                                        item_url = f"https://www.storyblocks.com/video/{item_id}"
                                    else:
                                        continue
                                    if item_url not in seen_urls:
                                        seen_urls.add(item_url)
                                        page_results.append((item_url, title))
                                if page_results:
                                    break
                        except Exception:
                            continue
                except Exception:
                    pass

        if not page_results:
            break  # No more results

        results.extend(page_results)

    return results

def _scrape_envato_search(session, search_url, max_pages=5):
    """Scrape all video URLs from an Envato Elements search/category page.
    Returns list of (video_url, title) tuples.
    """
    from urllib.parse import urlencode, urlparse, parse_qs

    results = []
    seen_urls = set()

    for page in range(1, max_pages + 1):
        parsed = urlparse(search_url)
        qs = parse_qs(parsed.query)
        qs["page"] = [str(page)]
        page_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(qs, doseq=True)}"

        try:
            resp = session.get(page_url, timeout=30)
            if resp.status_code != 200:
                break
            html = resp.text
        except Exception:
            break

        page_results = []

        # Method 1: Look for item links with Envato item ID pattern
        # Envato items end with -UPPERCASE_ALPHANUM_ID (e.g., /ocean-waves-4k-ABCD1234)
        link_patterns = [
            r'href="(https://elements\.envato\.com/(?:[\w-]+/)*[\w]+-[A-Z0-9]{5,})"',
            r'href="(/(?:[\w-]+/)*[\w]+-[A-Z0-9]{5,})"',
        ]
        for pattern in link_patterns:
            for match in re.finditer(pattern, html):
                href = match.group(1)
                if href.startswith("/"):
                    href = f"https://elements.envato.com{href}"
                # Skip non-item pages
                if "/search" in href or "?" in href or "/category/" in href:
                    continue
                if href not in seen_urls:
                    seen_urls.add(href)
                    title = _title_from_url(href) or "Envato Video"
                    page_results.append((href, title))

        # Method 2: JSON data embedded in page (React/Next.js props)
        if not page_results:
            # Try to find item data in script tags
            for m in re.finditer(r'"itemUrl"\s*:\s*"([^"]+)"', html):
                href = m.group(1).replace('\\/', '/')
                if not href.startswith("http"):
                    href = f"https://elements.envato.com{href}"
                if href not in seen_urls and re.search(r'-[A-Z0-9]{5,}$', href):
                    seen_urls.add(href)
                    title = _title_from_url(href) or "Envato Video"
                    page_results.append((href, title))

        # Method 3: Try Envato search API
        if not page_results and page == 1:
            keyword = qs.get("q", qs.get("term", qs.get("search", [""])))[0] if qs else ""
            if not keyword:
                # Extract from URL path
                path_parts = parsed.path.rstrip("/").split("/")
                keyword = path_parts[-1] if path_parts else ""
            if keyword:
                try:
                    api_resp = session.get(
                        f"https://elements.envato.com/api/v1/search",
                        params={"q": keyword, "type": "video", "page": page, "per_page": 50},
                        timeout=15,
                    )
                    if api_resp.status_code == 200:
                        api_data = api_resp.json()
                        items = api_data.get("items") or api_data.get("results") or api_data.get("data", [])
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            item_url = item.get("url") or item.get("itemUrl") or ""
                            if not item_url.startswith("http"):
                                slug = item.get("slug", "")
                                item_id = item.get("id", "")
                                if slug:
                                    item_url = f"https://elements.envato.com/{slug}"
                                elif item_id:
                                    item_url = f"https://elements.envato.com/item-{item_id}"
                            title = item.get("title") or item.get("name") or _title_from_url(item_url) or "Envato Video"
                            if item_url and item_url not in seen_urls:
                                seen_urls.add(item_url)
                                page_results.append((item_url, title))
                except Exception:
                    pass

        if not page_results:
            break

        results.extend(page_results)

    return results

def _scrape_dvidshub_search(session, search_url, max_pages=5):
    """Scrape all video URLs from a DVIDSHUB search page.
    Returns list of (video_url, title) tuples.
    """
    from urllib.parse import urlencode, urlparse, parse_qs

    results = []
    seen_urls = set()

    for page in range(1, max_pages + 1):
        parsed = urlparse(search_url)
        qs = parse_qs(parsed.query)
        qs["page"] = [str(page)]
        # Ensure we're searching for videos
        if "filter[type]" not in qs and "filter%5Btype%5D" not in parsed.query:
            qs["filter[type]"] = ["video"]
        page_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(qs, doseq=True)}"

        try:
            resp = session.get(page_url, timeout=30)
            if resp.status_code != 200:
                break
            html = resp.text
        except Exception:
            break

        page_results = []

        # Method 1: Look for video links in search results
        # DVIDSHUB pattern: /video/XXXXXX/title-slug
        for m in re.finditer(r'href="((?:https?://www\.dvidshub\.net)?/video/(\d+)/([^"]+))"', html):
            href = m.group(1)
            if href.startswith("/"):
                href = f"https://www.dvidshub.net{href}"
            if href not in seen_urls:
                seen_urls.add(href)
                slug = m.group(3).rstrip("/")
                title = slug.replace("-", " ").title() or "DVIDSHUB Video"
                page_results.append((href, title))

        # Method 2: Look for data attributes or JSON with video IDs
        if not page_results:
            for m in re.finditer(r'data-id="(\d+)"[^>]*data-title="([^"]*)"', html):
                vid_id = m.group(1)
                title = m.group(2) or f"DVIDSHUB Video {vid_id}"
                href = f"https://www.dvidshub.net/video/{vid_id}"
                if href not in seen_urls:
                    seen_urls.add(href)
                    page_results.append((href, title))

        if not page_results:
            break

        results.extend(page_results)

    return results


def _extract_dvidshub_download(session, page_url):
    """Extract video download URL and title from DVIDSHUB page.
    Flow: video page → /download/popup/{ID} → pick best /download/videofile/{FILE_ID}
    """
    # Extract video ID from URL: /video/XXXXXX or /video/XXXXXX/slug
    m = re.search(r'/video/(\d+)', page_url)
    if not m:
        return None, None
    video_id = m.group(1)

    # Get video page for title
    title = None
    try:
        resp = session.get(page_url, timeout=30)
        if resp.status_code == 200:
            html = resp.text
            # Title from <title> tag
            tm = re.search(r'<title[^>]*>([^<]+)</title>', html)
            if tm:
                t = tm.group(1).strip()
                t = re.sub(r'\s*[|–—]\s*DVIDS.*$', '', t).strip()
                if t and len(t) > 3:
                    title = t
            # Fallback: <h1>
            if not title:
                tm = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
                if tm:
                    title = tm.group(1).strip()
            # Fallback: og:title
            if not title:
                tm = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html)
                if tm:
                    title = re.sub(r'\s*[|–—]\s*DVIDS.*$', '', tm.group(1)).strip()
    except Exception:
        pass

    if not title:
        title = _title_from_url(page_url) or f"DVIDSHUB Video {video_id}"

    # Fetch download popup to get available file versions
    download_url = None
    popup_url = f"https://www.dvidshub.net/download/popup/{video_id}"
    try:
        resp = session.get(popup_url, timeout=30)
        if resp.status_code == 200:
            popup_html = resp.text
            # Parse download links: /download/videofile/XXXXXXX with resolution and size
            # Pattern: href="/download/videofile/XXXXX" ... WxH ... SIZE MB
            files = []
            for fm in re.finditer(
                r'href="(/download/videofile/(\d+))"[^>]*>.*?(\d+)\s*x\s*(\d+).*?(\d+)\s*MB',
                popup_html, re.S
            ):
                file_path = fm.group(1)
                file_id = fm.group(2)
                width = int(fm.group(3))
                height = int(fm.group(4))
                size_mb = int(fm.group(5))
                pixels = width * height
                files.append((pixels, size_mb, file_id, file_path, width, height))

            # Fallback: simpler pattern just matching videofile links
            if not files:
                for fm in re.finditer(r'href="(/download/videofile/(\d+))"', popup_html):
                    file_path = fm.group(1)
                    file_id = fm.group(2)
                    files.append((0, 0, file_id, file_path, 0, 0))

            if files:
                # Pick the highest resolution (most pixels), fallback to largest file
                files.sort(key=lambda x: (x[0], x[1]), reverse=True)
                best = files[0]
                download_url = f"https://www.dvidshub.net{best[3]}"
    except Exception:
        pass

    # Fallback: try direct download endpoint
    if not download_url:
        download_url = f"https://www.dvidshub.net/download/videofile/{video_id}"

    return title, download_url


def expand_search_urls(urls, cfg):
    """Expand any search/category URLs into individual video URLs.
    Returns (expanded_urls, search_stats).
    """
    expanded = []
    search_stats = {"search_urls": 0, "videos_found": 0}

    for url in urls:
        site = is_search_url(url)
        if not site:
            expanded.append(url)
            continue

        search_stats["search_urls"] += 1
        browser = cfg.get("cookie_browser", "chrome")
        session, source = _get_premium_session(site, browser)

        if not session:
            # Can't expand without auth, pass through as-is
            expanded.append(url)
            continue

        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        })

        try:
            if site == "storyblocks":
                results = _scrape_storyblocks_search(session, url)
            elif site == "envato":
                results = _scrape_envato_search(session, url)
            elif site == "dvidshub":
                results = _scrape_dvidshub_search(session, url)
            else:
                results = []

            if results:
                search_stats["videos_found"] += len(results)
                for video_url, _ in results:
                    if video_url not in expanded:
                        expanded.append(video_url)
            else:
                # No results found, pass the URL through
                expanded.append(url)
        except Exception:
            expanded.append(url)

    return expanded, search_stats

def _sanitize_filename(name, ext="mp4", seq=None):
    """Make a safe filename from title. If seq is given, prefix with zero-padded number."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = name.strip('. ')
    if len(name) > 200:
        name = name[:200]
    if not name:
        name = f"download_{int(time.time())}"
    if seq is not None:
        return f"{seq:02d}. {name}.{ext}"
    return f"{name}.{ext}"

def _title_from_url(url):
    """Extract a human-readable title from a URL slug."""
    try:
        path = urlparse(url).path.rstrip("/")
        slug = path.split("/")[-1] if path else ""
        if not slug:
            return None
        # Remove item ID suffix (e.g. -ABC123, -12345678)
        slug = re.sub(r'-[A-Z0-9]{5,}$', '', slug)
        slug = re.sub(r'-\d{6,}$', '', slug)
        # Convert slug to title
        title = slug.replace("-", " ").replace("_", " ").strip()
        if title and len(title) > 3:
            return title.title()
    except Exception:
        pass
    return None

def _download_with_progress(session, download_url, filepath, task_id, referer=None, extra_headers=None):
    """Stream download a file with progress updates."""
    headers = {
        "Accept": "video/mp4,video/*,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = referer.split("/")[0] + "//" + referer.split("/")[2]
    if extra_headers:
        headers.update(extra_headers)
    resp = session.get(download_url, stream=True, headers=headers, timeout=120, allow_redirects=True)
    resp.raise_for_status()
    # Validate Content-Type: reject HTML/text responses masquerading as video
    content_type = resp.headers.get("content-type", "").lower()
    if "text/html" in content_type or "application/json" in content_type:
        resp.close()
        raise Exception(f"Server returned {content_type} instead of video. Check cookies/subscription.")
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 64):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = min(99, (downloaded / total) * 100)
                speed = ""
                with download_lock:
                    if task_id in downloads:
                        downloads[task_id]["progress"] = round(pct, 1)
                        speed_mb = downloaded / (1024 * 1024)
                        downloads[task_id]["speed"] = f"{speed_mb:.1f}MB"
    return filepath

def _extract_envato_download(session, page_url):
    """Extract video download URL and title from Envato page.
    Supports both:
    - elements.envato.com (old format, item slug with -ID suffix)
    - app.envato.com (new format, UUID in path)
    """
    domain = _get_url_domain(page_url)
    is_app = "app.envato.com" in (domain or "")

    # Extract item UUID from app.envato.com URL
    # Format: app.envato.com/search/stock-video/{UUID}?...
    item_uuid = None
    if is_app:
        m = re.search(r'/stock-video/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', page_url, re.I)
        if m:
            item_uuid = m.group(1)

    # Fetch the page
    resp = session.get(page_url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Extract title - multiple strategies
    title = None
    # Strategy 1: <title> tag — for SPA pages, pick the best from ALL title tags
    _skip_titles = {"unauthorized", "login", "sign in", "imagegen", "imageedit",
                    "videogen", "musicgen", "voicegen", "soundgen", "graphicsgen",
                    "mockupgen", "photos", "videos", "video templates", "music",
                    "sound effects", "graphics", "fonts", "3d", "web", "wordpress",
                    "add-ons", "workspaces", "generation history"}
    for tm in re.finditer(r'<title[^>]*>([^<]+)</title>', html):
        t = tm.group(1).strip()
        t = re.sub(r'\s*[|–—]\s*Envato.*$', '', t).strip()
        if t and len(t) > 3 and t.lower() not in _skip_titles:
            title = t
            break  # Use first meaningful title
    # Strategy 2: <h1>
    if not title:
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        if m:
            title = m.group(1).strip()
    # Strategy 3: og:title meta
    if not title:
        m = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html)
        if m:
            title = re.sub(r'\s*[|–—]\s*Envato.*$', '', m.group(1)).strip()
    # Strategy 4: JSON "name"/"title" field in page data
    if not title:
        for pat in [r'"title"\s*:\s*"([^"]{4,})"', r'"name"\s*:\s*"([^"]{4,})"']:
            m = re.search(pat, html)
            if m and m.group(1).lower() not in ("unauthorized", "sign in"):
                title = m.group(1).strip()
                break
    # Strategy 5: URL slug fallback
    if not title:
        title = _title_from_url(page_url) or "Envato Video"

    # For app.envato.com SPA: extract data from HTML card + JS data
    _content_uuid = None
    if is_app and item_uuid:
        escaped_uuid = re.escape(item_uuid)

        # Title: from data-analytics-item_title attribute (most reliable)
        tm = re.search(r'data-analytics-item_id="' + escaped_uuid + r'"[^>]*data-analytics-item_title="([^"]+)"', html)
        if not tm:
            tm = re.search(r'data-analytics-item_title="([^"]+)"[^>]*data-analytics-item_id="' + escaped_uuid + r'"', html)
        if tm:
            title = tm.group(1).strip()

        # Preview URL: from JS serialized data — find UUID followed by MP4 URL
        m = re.search(
            escaped_uuid + r'[\\/"]*,'               # item UUID
            r'.*?'                                    # skip fields (non-greedy)
            r'(https?://public-assets[^"\\]+\.mp4)',  # capture: preview MP4 URL
            html
        )
        if m:
            js_preview = m.group(1)
            # Extract content UUID from preview URL
            cm = re.search(r'/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/', js_preview)
            if cm:
                _content_uuid = cm.group(1)

    # If title is still a SPA shell title, it's not a real auth error for app.envato.com
    _bad_titles = {"unauthorized", "unauthorized domain", "login", "sign in", "access denied"}
    if title.lower().strip() in _bad_titles:
        if not is_app:
            # Only treat as auth error for non-SPA (elements.envato.com)
            raise requests.exceptions.HTTPError(
                response=type('R', (), {'status_code': 401})(),
            )
        # For app.envato.com, use URL-derived title
        title = _title_from_url(page_url) or "Envato Video"

    # Extract item ID from old-format URLs (elements.envato.com)
    item_id = None
    if not is_app:
        m = re.search(r'-([A-Z0-9]{6,})(?:\?|$)', page_url)
        if m:
            item_id = m.group(1)
        if not item_id:
            m = re.search(r'/(\d{5,})', page_url)
            if m:
                item_id = m.group(1)

    download_url = None

    # ── app.envato.com: Find preview URL from JS data ──
    if is_app and _content_uuid:
        # Find the preview MP4 URL that matches this item's content UUID
        pattern = re.escape(_content_uuid) + r'[^"]*preview[^"]*\.mp4[^"]*'
        all_mp4 = re.findall(r'"(https://public-assets[^"]+\.mp4[^"]*)"', html)
        for mp4_url in all_mp4:
            if _content_uuid in mp4_url:
                download_url = mp4_url.replace('\\/', '/')
                break

    # ── app.envato.com: Try download API (may be Cloudflare-protected) ──
    if not download_url and is_app and item_uuid:
        try:
            api_resp = session.post(
                "https://elements.envato.com/api/v1/downloads",
                json={"item_uuid": item_uuid},
                timeout=15,
            )
            if api_resp.status_code == 200:
                data = api_resp.json()
                download_url = data.get("download_url") or data.get("url") or data.get("downloadUrl")
        except Exception:
            pass

    # ── Scrape page for video URLs (both old and new format) ──
    if not download_url:
        video_patterns = [
            r'"videoPreviewUrl"\s*:\s*"([^"]+)"',
            r'"video_preview_url"\s*:\s*"([^"]+)"',
            r'"previewUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
            r'"preview_url"\s*:\s*"([^"]+)"',
            r'"downloadUrl"\s*:\s*"([^"]+)"',
            r'"download_url"\s*:\s*"([^"]+)"',
            r'"url"\s*:\s*"(https?://[^"]*\.mp4[^"]*)"',
            r'src="(https?://[^"]*preview[^"]*\.mp4[^"]*)"',
            r'"contentUrl"\s*:\s*"([^"]+)"',
            r'"videoUrl"\s*:\s*"([^"]+)"',
            r'"video_url"\s*:\s*"([^"]+)"',
            r'"hlsUrl"\s*:\s*"([^"]+)"',
            r'"hls_url"\s*:\s*"([^"]+)"',
            r'"src"\s*:\s*"(https?://[^"]*(?:\.mp4|video)[^"]*)"',
        ]
        for pattern in video_patterns:
            m = re.search(pattern, html)
            if m:
                candidate = m.group(1).replace('\\u002F', '/').replace('\\/', '/')
                if candidate.startswith("http"):
                    download_url = candidate
                    break

    # ── JSON-LD structured data ──
    if not download_url:
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
            try:
                ld = json.loads(m.group(1))
                if isinstance(ld, list):
                    ld = ld[0]
                url_candidate = ld.get("contentUrl") or ld.get("embedUrl") or ld.get("url")
                if url_candidate and (".mp4" in url_candidate or "video" in url_candidate):
                    download_url = url_candidate
                    break
            except (json.JSONDecodeError, AttributeError):
                continue

    # ── Try Envato download API (old elements.envato.com format) ──
    if not download_url and item_id:
        api_endpoints = [
            f"https://elements.envato.com/api/v1/items/{item_id}/download",
            f"https://elements.envato.com/api/v2/items/{item_id}/download",
        ]
        for api_url in api_endpoints:
            try:
                api_resp = session.get(api_url, timeout=15)
                if api_resp.status_code == 200:
                    data = api_resp.json()
                    download_url = data.get("download_url") or data.get("url") or data.get("downloadUrl")
                    if download_url:
                        break
            except Exception:
                continue

    # ── HTML5 video source ──
    if not download_url:
        for m in re.finditer(r'<(?:source|video)[^>]+src="([^"]+)"', html):
            src = m.group(1)
            if ".mp4" in src or "video" in src:
                download_url = src
                break

    return title, download_url

def _extract_storyblocks_download(session, page_url):
    """Extract video download URL and title from Storyblocks page."""
    resp = session.get(page_url, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # Extract title - multiple strategies
    title = None
    m = re.search(r'<title[^>]*>([^<]+)</title>', html)
    if m:
        t = m.group(1).strip()
        t = re.sub(r'\s*[|–—]\s*Storyblocks.*$', '', t).strip()
        if t and len(t) > 3:
            title = t
    if not title:
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        if m:
            title = m.group(1).strip()
    if not title:
        m = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', html)
        if m:
            title = re.sub(r'\s*[|–—]\s*Storyblocks.*$', '', m.group(1)).strip()
    if not title:
        m = re.search(r'"title"\s*:\s*"([^"]{4,})"', html)
        if m:
            title = m.group(1).strip()
    if not title:
        title = _title_from_url(page_url) or "Storyblocks Video"

    download_url = None

    # Method 1: Look for download button data / stock item download URL
    download_patterns = [
        r'"downloadUrl"\s*:\s*"([^"]+)"',
        r'"download_url"\s*:\s*"([^"]+)"',
        r'"contentUrl"\s*:\s*"([^"]+)"',
        r'"url"\s*:\s*"(https?://[^"]*\.mp4[^"]*)"',
        r'href="([^"]*download[^"]*)"',
        r'"mp4"\s*:\s*\{[^}]*"url"\s*:\s*"([^"]+)"',
        r'"hd"\s*:\s*\{[^}]*"url"\s*:\s*"([^"]+)"',
    ]
    for pattern in download_patterns:
        m = re.search(pattern, html)
        if m:
            url_candidate = m.group(1).replace('\\u002F', '/').replace('\\/', '/')
            if url_candidate.startswith('http'):
                download_url = url_candidate
                break

    # Method 2: Look for __NEXT_DATA__ or similar JS data
    if not download_url:
        m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                # Navigate through Next.js page props
                props = data.get("props", {}).get("pageProps", {})
                stock_item = props.get("stockItem") or props.get("item") or {}
                # Try preview URLs
                preview = stock_item.get("previewUrls") or stock_item.get("preview_urls") or {}
                download_url = preview.get("mp4") or preview.get("hd") or preview.get("sd")
                if not download_url and isinstance(preview, dict):
                    for v in preview.values():
                        if isinstance(v, str) and v.startswith("http"):
                            download_url = v
                            break
                if not title or title == "Storyblocks Video":
                    title = stock_item.get("title") or stock_item.get("name") or title
            except (json.JSONDecodeError, AttributeError):
                pass

    # Method 3: Look for video source tags
    if not download_url:
        m = re.search(r'<source[^>]+src="([^"]+\.mp4[^"]*)"', html)
        if m:
            download_url = m.group(1)

    # Method 4: Try Storyblocks API endpoint for stock item
    if not download_url:
        # Extract stock item ID from URL
        m = re.search(r'/video/[^/]+-([a-zA-Z0-9_-]+)$', page_url)
        if not m:
            m = re.search(r'/stock-video/[^/]+-([a-zA-Z0-9_-]+)', page_url)
        if m:
            stock_id = m.group(1)
            try:
                api_resp = session.post(
                    "https://www.storyblocks.com/api/media/download",
                    json={"mediaId": stock_id, "mediaType": "video"},
                    timeout=15,
                )
                if api_resp.status_code == 200:
                    api_data = api_resp.json()
                    download_url = api_data.get("downloadUrl") or api_data.get("url")
            except Exception:
                pass

    return title, download_url

def premium_download_worker(task_id, url, cfg):
    """Download worker for premium sites (Envato/Storyblocks)."""
    semaphore.acquire()
    try:
        site_type = _get_premium_site_type(url)
        browser = cfg.get("cookie_browser", "chrome")

        with download_lock:
            if task_id not in downloads:
                return
            downloads[task_id]["status"] = "downloading"
            # Set initial title from URL slug (better than "Unknown")
            initial_title = _title_from_url(url) or f"Loading from {site_type.title()}..."
            downloads[task_id]["title"] = initial_title

        # Get authenticated session (cookie file first, then browser)
        session, source = _get_premium_session(site_type, browser)
        if not session:
            with download_lock:
                if task_id in downloads:
                    downloads[task_id].update(
                        status="error",
                        error=f"No cookies found for {site_type.title()}. Import cookie JSON in Settings or login in {browser}.",
                        done_at=time.time(),
                    )
            return

        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        })

        # Extract download info based on site
        try:
            if site_type == "envato":
                title, download_url = _extract_envato_download(session, url)
            elif site_type == "storyblocks":
                title, download_url = _extract_storyblocks_download(session, url)
            elif site_type == "dvidshub":
                title, download_url = _extract_dvidshub_download(session, url)
            else:
                with download_lock:
                    if task_id in downloads:
                        downloads[task_id].update(status="error", error="Unsupported premium site", done_at=time.time())
                return
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP {e.response.status_code}"
            if e.response.status_code in (401, 403):
                hint = "Import a fresh cookie JSON in Settings." if source == "cookie_file" else f"Login to {site_type.title()} in {browser} or import cookie JSON."
                error_msg = f"Not authenticated on {site_type.title()}. {hint}"
            with download_lock:
                if task_id in downloads:
                    downloads[task_id].update(status="error", error=error_msg, done_at=time.time())
            return

        with download_lock:
            if task_id in downloads:
                downloads[task_id]["title"] = title

        if not download_url:
            with download_lock:
                if task_id in downloads:
                    downloads[task_id].update(
                        status="error",
                        error=f"Could not find download URL. Import cookies or check your {site_type.title()} subscription.",
                        done_at=time.time(),
                    )
            return

        # Download the file
        dl_dir = cfg.get("download_dir", DEFAULT_CONFIG["download_dir"])
        os.makedirs(dl_dir, exist_ok=True)
        with download_lock:
            seq = downloads[task_id].get("seq") if task_id in downloads else None
        filename = _sanitize_filename(title, "mp4", seq=seq)
        filepath = os.path.join(dl_dir, filename)

        with download_lock:
            if task_id in downloads:
                downloads[task_id]["filename"] = filename

        # DVIDSHUB needs Referer from dvidshub.net for download to work
        referer = url
        extra_headers = None
        if site_type == "dvidshub":
            video_id_m = re.search(r'/video/(\d+)', url)
            referer = f"https://www.dvidshub.net/download/popup/{video_id_m.group(1)}" if video_id_m else url
            extra_headers = {
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Upgrade-Insecure-Requests": "1",
            }

        _download_with_progress(session, download_url, filepath, task_id, referer=referer, extra_headers=extra_headers)

        with download_lock:
            if task_id in downloads:
                downloads[task_id].update(status="done", progress=100, speed="", eta="", done_at=time.time())

    except Exception as e:
        with download_lock:
            if task_id in downloads:
                downloads[task_id].update(status="error", error=str(e)[:200], done_at=time.time())
    finally:
        with download_lock:
            processes.pop(task_id, None)
        semaphore.release()

# ─── Download Manager ────────────────────────────────────────────────
downloads = {}
download_lock = threading.Lock()
semaphore = threading.Semaphore(config["concurrent_downloads"])
processes = {}

def _get_ytdlp_cookie_args():
    """Return cookie args for yt-dlp: use cookies.txt if available, otherwise none.
    --cookies-from-browser chrome is unreliable on Windows (Chrome locks its DB).
    """
    cookie_file = COOKIES_DIR / "cookies.txt"
    if cookie_file.exists() and cookie_file.stat().st_size > 100:
        return ["--cookies", str(cookie_file)]
    return []

def build_ytdlp_cmd(url, cfg, seq=None):
    dl_dir = cfg.get("download_dir", DEFAULT_CONFIG["download_dir"])
    quality = cfg.get("quality", "1080")
    fmt = cfg.get("format", "mp4")
    tmpl = cfg.get("filename_template", DEFAULT_CONFIG["filename_template"])
    if seq is not None:
        # Prefix template with sequence number: "01. %(title)s.%(ext)s"
        tmpl = f"{seq:02d}. {tmpl}"
    os.makedirs(dl_dir, exist_ok=True)
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist", "-o", os.path.join(dl_dir, tmpl),
        "--newline", "--no-colors",
        "--progress-template", "%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        *_get_ytdlp_cookie_args(),
        "--remote-components", "ejs:github",
    ]
    if fmt == "mp3":
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        # Fallback chain: quality merge → any merge → single stream at quality → any single stream
        cmd += ["-f", f"bv[height<={quality}]+ba/bv+ba/b[height<={quality}]/b",
                "--merge-output-format", fmt]
    cmd.append(url)
    return cmd

def get_video_title(url):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--get-title", "--no-playlist",
             *_get_ytdlp_cookie_args(), "--remote-components", "ejs:github", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        title = result.stdout.strip()
        if title and title != "NA":
            return title
    except Exception:
        pass
    return ""

def download_worker(task_id, url, cfg):
    semaphore.acquire()
    try:
        with download_lock:
            if task_id not in downloads:
                return
            downloads[task_id]["status"] = "downloading"

        # Fetch title OUTSIDE the lock (can take up to 30s)
        title = get_video_title(url)
        with download_lock:
            if task_id not in downloads:
                return
            if title:
                downloads[task_id]["title"] = title
            seq = downloads[task_id].get("seq")
        cmd = build_ytdlp_cmd(url, cfg, seq=seq)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)

        with download_lock:
            if task_id not in downloads:
                process.kill()
                return
            processes[task_id] = process

        last_error_lines = []
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            # Capture error lines for better reporting
            if line.startswith("ERROR:") or line.startswith("WARNING:"):
                last_error_lines.append(line)
                if len(last_error_lines) > 5:
                    last_error_lines.pop(0)
            if "%" in line and "|" in line:
                parts = line.split("|")
                if len(parts) >= 3:
                    try:
                        pct_val = float(parts[0].strip().replace("%", ""))
                        with download_lock:
                            if task_id in downloads:
                                downloads[task_id]["progress"] = pct_val
                                downloads[task_id]["speed"] = parts[1].strip()
                                downloads[task_id]["eta"] = parts[2].strip()
                    except ValueError:
                        pass
            if "[download] Destination:" in line:
                with download_lock:
                    if task_id in downloads:
                        fname = os.path.basename(line.split("Destination:")[-1].strip())
                        downloads[task_id]["filename"] = fname
                        # Extract title from filename if still "Fetching..."
                        if downloads[task_id]["title"] == "Fetching...":
                            # Remove seq prefix and extension: "01. Title.mp4" → "Title"
                            name = re.sub(r'^\d+\.\s*', '', fname)
                            name = re.sub(r'\.[^.]+$', '', name)
                            if name:
                                downloads[task_id]["title"] = name
            if "[Merger]" in line or "[ExtractAudio]" in line:
                with download_lock:
                    if task_id in downloads:
                        downloads[task_id].update(progress=99, speed="merging...", eta="")

        process.wait()
        with download_lock:
            if task_id in downloads:
                # Don't overwrite if already cancelled/errored
                if downloads[task_id]["status"] not in ("done", "error"):
                    if process.returncode == 0:
                        downloads[task_id].update(status="done", progress=100, speed="", eta="", done_at=time.time())
                    else:
                        error_msg = "yt-dlp exited with error"
                        error_details = [l for l in last_error_lines if l.startswith("ERROR:")]
                        if error_details:
                            error_msg = error_details[-1].replace("ERROR: ", "", 1)
                        downloads[task_id].update(status="error", error=error_msg, done_at=time.time())

    except Exception as e:
        with download_lock:
            if task_id in downloads:
                downloads[task_id].update(status="error", error=str(e), done_at=time.time())
    finally:
        with download_lock:
            processes.pop(task_id, None)
        semaphore.release()

def auto_clean_old_tasks():
    while True:
        time.sleep(60)
        now = time.time()
        with download_lock:
            to_remove = [tid for tid, t in downloads.items()
                         if t["status"] in ("done", "error") and (now - t.get("done_at", 0)) > AUTO_CLEAN_SECONDS]
            for tid in to_remove:
                del downloads[tid]

threading.Thread(target=auto_clean_old_tasks, daemon=True).start()

def cleanup_processes():
    with download_lock:
        for proc in processes.values():
            try:
                proc.kill()
            except Exception:
                pass
        processes.clear()

atexit.register(cleanup_processes)

# ─── Flask App ───────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def index():
    return send_from_directory(Path(__file__).parent, "index.html")

@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json or {}
    urls_raw = data.get("urls", "")
    if not isinstance(urls_raw, str):
        return jsonify({"error": "URLs must be a string"}), 400
    valid_urls, stats = extract_urls(urls_raw)
    if not valid_urls:
        return jsonify({"error": "No valid URLs found. Paste YouTube links or video URLs."}), 400

    # Expand search/category URLs into individual video URLs
    cfg = validate_config(load_config())
    expanded_urls, search_stats = expand_search_urls(valid_urls, cfg)
    stats.update(search_stats)

    if len(valid_urls) > MAX_BULK_URLS:
        return jsonify({"error": f"Too many URLs ({len(valid_urls)}). Max {MAX_BULK_URLS} per batch."}), 400
    if len(expanded_urls) > MAX_QUEUE_SIZE:
        return jsonify({"error": f"Too many videos ({len(expanded_urls)}). Max {MAX_QUEUE_SIZE} per batch."}), 400
    with download_lock:
        active = sum(1 for t in downloads.values() if t["status"] in ("queued", "downloading"))
    if active + len(expanded_urls) > MAX_QUEUE_SIZE:
        return jsonify({"error": f"Queue full (max {MAX_QUEUE_SIZE} active)"}), 429
    task_ids = []
    for idx, url in enumerate(expanded_urls, start=1):
        task_id = str(uuid.uuid4())[:8]
        source = "premium" if is_premium_url(url) else "ytdlp"
        seq = idx  # sequence number based on paste order
        with download_lock:
            downloads[task_id] = dict(status="queued", progress=0, title="Fetching...",
                                      url=url, source=source, error=None, filename="", speed="", eta="", done_at=0, seq=seq)
        worker = premium_download_worker if source == "premium" else download_worker
        threading.Thread(target=worker, args=(task_id, url, cfg), daemon=True).start()
        task_ids.append(task_id)
    result = {"tasks": task_ids, **stats}
    return jsonify(result)

@app.route("/api/search/preview", methods=["POST"])
def preview_search():
    """Preview what videos would be found from a search URL."""
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    site = is_search_url(url)
    if not site:
        return jsonify({"is_search": False, "url": url})

    cfg = validate_config(load_config())
    browser = cfg.get("cookie_browser", "chrome")
    session, source = _get_premium_session(site, browser)

    if not session:
        return jsonify({
            "is_search": True, "site": site,
            "error": f"No cookies for {site.title()}. Import in Settings first."
        })

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    })

    try:
        if site == "storyblocks":
            results = _scrape_storyblocks_search(session, url, max_pages=1)
        elif site == "envato":
            results = _scrape_envato_search(session, url, max_pages=1)
        elif site == "dvidshub":
            results = _scrape_dvidshub_search(session, url, max_pages=1)
        else:
            results = []
        return jsonify({
            "is_search": True, "site": site,
            "videos": [{"url": u, "title": t} for u, t in results],
            "count": len(results),
        })
    except Exception as e:
        return jsonify({"is_search": True, "site": site, "error": str(e)[:200]})

@app.route("/api/parse-urls", methods=["POST"])
def parse_urls():
    """Live preview: parse and clean URLs without downloading."""
    data = request.json or {}
    urls_raw = data.get("urls", "")
    if not isinstance(urls_raw, str):
        return jsonify({"urls": [], "stats": {}})
    urls, stats = extract_urls(urls_raw)
    # Detect search URLs
    search_count = sum(1 for u in urls if is_search_url(u))
    stats["search_urls"] = search_count
    return jsonify({"urls": urls, "stats": stats})

@app.route("/api/cancel/<task_id>", methods=["POST"])
def cancel_download(task_id):
    with download_lock:
        proc = processes.get(task_id)
        task = downloads.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task["status"] in ("done", "error"):
        return jsonify({"ok": True})
    if proc:
        try:
            proc.kill()
        except Exception:
            pass
    with download_lock:
        if task_id in downloads and downloads[task_id]["status"] not in ("done", "error"):
            downloads[task_id].update(status="error", error="Cancelled", done_at=time.time())
    return jsonify({"ok": True})

@app.route("/api/status")
def get_status():
    with download_lock:
        return jsonify(downloads)

@app.route("/api/status/<task_id>")
def get_task_status(task_id):
    with download_lock:
        task = downloads.get(task_id)
    return jsonify(task) if task else (jsonify({"error": "Not found"}), 404)

@app.route("/api/clear", methods=["POST"])
def clear_done():
    with download_lock:
        to_remove = [tid for tid, t in downloads.items() if t["status"] in ("done", "error")]
        for tid in to_remove:
            del downloads[tid]
    return jsonify({"cleared": len(to_remove)})

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(validate_config(load_config()))

@app.route("/api/config", methods=["POST"])
def update_config():
    global config, semaphore
    data = request.json or {}
    cfg = load_config()
    for key in DEFAULT_CONFIG:
        if key in data:
            cfg[key] = data[key]
    cfg = validate_config(cfg)
    save_config(cfg)
    semaphore = threading.Semaphore(cfg["concurrent_downloads"])
    config = cfg
    return jsonify(cfg)

@app.route("/api/open-folder", methods=["POST"])
def open_folder():
    cfg = load_config()
    folder = cfg.get("download_dir", DEFAULT_CONFIG["download_dir"])
    os.makedirs(folder, exist_ok=True)
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", folder])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/cookies/status")
def cookies_status():
    """Get status of imported cookie files for each premium site."""
    result = {}
    for site_type in ("envato", "storyblocks", "dvidshub"):
        info = _get_cookie_file_info(site_type)
        result[site_type] = info or {"exists": False}
    # yt-dlp Netscape cookie file
    netscape = COOKIES_DIR / "cookies.txt"
    result["_ytdlp"] = netscape.exists() and netscape.stat().st_size > 100
    return jsonify(result)

@app.route("/api/cookies/import/<site_type>", methods=["POST"])
def import_cookies(site_type):
    """Import a JSON cookie file for a premium site.
    Accepts: JSON body with cookie array, or multipart file upload.
    """
    if site_type not in ("envato", "storyblocks", "dvidshub"):
        return jsonify({"error": "Invalid site. Use 'envato', 'storyblocks', or 'dvidshub'."}), 400

    cookie_data = None

    # Method 1: File upload (multipart/form-data)
    if "file" in request.files:
        f = request.files["file"]
        if f.filename:
            try:
                raw = f.read().decode("utf-8")
                cookie_data = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                return jsonify({"error": f"Invalid JSON file: {e}"}), 400

    # Method 2: JSON body (paste from UI)
    if cookie_data is None and request.is_json:
        data = request.json or {}
        cookie_data = data.get("cookies")
        # Also accept raw JSON array posted directly
        if cookie_data is None and isinstance(data, list):
            cookie_data = data

    if cookie_data is None:
        return jsonify({"error": "No cookie data provided. Upload a JSON file or paste cookie JSON."}), 400

    # Validate: must be a list of cookie objects or dict with "cookies" key
    if isinstance(cookie_data, dict) and "cookies" in cookie_data:
        cookies_list = cookie_data["cookies"]
    elif isinstance(cookie_data, list):
        cookies_list = cookie_data
    else:
        return jsonify({"error": "Invalid format. Expected a JSON array of cookies or {\"cookies\": [...]}"}), 400

    # Basic validation
    valid_cookies = []
    for c in cookies_list:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("Name", "")
        value = c.get("value") or c.get("Value", "")
        if name and value:
            valid_cookies.append(c)

    if not valid_cookies:
        return jsonify({"error": "No valid cookies found in the data. Each cookie needs 'name' and 'value' fields."}), 400

    # Save to file
    cookie_file = COOKIES_DIR / f"{site_type}.json"
    try:
        with open(cookie_file, "w", encoding="utf-8") as f:
            json.dump(valid_cookies, f, indent=2, ensure_ascii=False)
    except OSError as e:
        return jsonify({"error": f"Failed to save: {e}"}), 500

    return jsonify({
        "ok": True,
        "site": site_type,
        "cookie_count": len(valid_cookies),
        "message": f"Imported {len(valid_cookies)} cookies for {site_type.title()}."
    })

@app.route("/api/cookies/import-all", methods=["POST"])
def import_all_cookies():
    """Import ONE JSON cookie file → auto-split to all sites by domain.
    Also generates Netscape cookie file for yt-dlp.
    """
    cookie_data = None

    if "file" in request.files:
        f = request.files["file"]
        if f.filename:
            try:
                raw = f.read().decode("utf-8")
                cookie_data = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                return jsonify({"error": f"Invalid JSON file: {e}"}), 400

    if cookie_data is None and request.is_json:
        data = request.json or {}
        cookie_data = data.get("cookies")
        if cookie_data is None and isinstance(data, list):
            cookie_data = data

    if cookie_data is None:
        return jsonify({"error": "No cookie data provided."}), 400

    if isinstance(cookie_data, dict) and "cookies" in cookie_data:
        cookies_list = cookie_data["cookies"]
    elif isinstance(cookie_data, list):
        cookies_list = cookie_data
    else:
        return jsonify({"error": "Invalid format. Expected a JSON array of cookies."}), 400

    valid_cookies = [c for c in cookies_list if isinstance(c, dict) and (c.get("name") or c.get("Name")) and (c.get("value") or c.get("Value"))]
    if not valid_cookies:
        return jsonify({"error": "No valid cookies found."}), 400

    try:
        result = _split_and_save_cookies(valid_cookies)
    except OSError as e:
        return jsonify({"error": f"Failed to save: {e}"}), 500

    parts = [f"{v} cookies → {k.title()}" for k, v in result.items() if v]
    summary = ", ".join(parts) if parts else "No matching site cookies found"
    return jsonify({
        "ok": True,
        "total": len(valid_cookies),
        "sites": result,
        "message": f"Imported {len(valid_cookies)} cookies. {summary}. yt-dlp cookie file updated."
    })

@app.route("/api/cookies/delete-all", methods=["POST"])
def delete_all_cookies():
    """Delete all stored cookies for all sites."""
    deleted = []
    for site_type in ("envato", "storyblocks", "dvidshub"):
        cookie_file = COOKIES_DIR / f"{site_type}.json"
        if cookie_file.exists():
            cookie_file.unlink()
            deleted.append(site_type)
    netscape = COOKIES_DIR / "cookies.txt"
    if netscape.exists():
        netscape.unlink()
        deleted.append("yt-dlp")
    return jsonify({"ok": True, "deleted": deleted, "message": f"Deleted cookies for: {', '.join(deleted) or 'none'}"})

@app.route("/api/cookies/delete/<site_type>", methods=["POST"])
def delete_cookies(site_type):
    """Delete stored cookies for a premium site."""
    if site_type not in ("envato", "storyblocks", "dvidshub"):
        return jsonify({"error": "Invalid site."}), 400
    cookie_file = COOKIES_DIR / f"{site_type}.json"
    try:
        if cookie_file.exists():
            cookie_file.unlink()
        return jsonify({"ok": True, "message": f"Cookies for {site_type.title()} deleted."})
    except OSError as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/check-deps")
def check_deps():
    deps = {}
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], capture_output=True, text=True, timeout=5)
        deps["yt-dlp"] = r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        deps["yt-dlp"] = None
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        deps["ffmpeg"] = r.stdout.split("\n")[0] if r.returncode == 0 else None
    except Exception:
        deps["ffmpeg"] = None
    return jsonify(deps)

if __name__ == "__main__":
    import webbrowser
    port = int(os.environ.get("PORT", 9123))
    print(f"\n  VidGrab v2.2 -- http://localhost:{port}\n  Press Ctrl+C to stop\n")
    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False)
