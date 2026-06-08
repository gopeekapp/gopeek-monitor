#!/usr/bin/env python3
"""
GoPeek Reddit Monitor v1.6
- STRICT whole-word matching only
- Exclude subreddit name from body matching
- 48-hour age limit
- Fixed deduplication
"""

import os
import re
import json
import hashlib
import threading
import time
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# =========================================================
# CONFIGURATION
# =========================================================

SUBREDDITS = [
    "browsers",
    "firefox",
    "chrome",
    "microsoftedge",
    "productivity",
    "webdev",
    "programming",
    "opensource",
    "chrome_extensions"
]

# STRICT phrases - must match as whole words only
KEYWORDS = [
    "peek", "preview", "glance", "hover preview", "link preview",
    "too many tabs", "tab overload", "tab clutter", "drowning in tabs",
    "tab management", "tab hoarding", "tab anxiety", "hundreds of tabs",
    "without opening tabs", "open links without", "save tabs for later",
    "sidebar", "side panel", "split view", "dual pane",
    "tab suspension", "tab groups"
]

# Only match these in specific contexts (not just anywhere)
EXTENSION_KEYWORDS = [
    "browser extension for tabs",
    "extension for tab management",
    "extension to manage tabs",
    "extension to preview links",
    "extension for link preview",
    "need an extension",
    "looking for extension",
    "recommend an extension",
    "what extension",
    "which extension"
]

CHECK_INTERVAL = 300
DEDUP_HOURS = 48
MAX_POST_AGE_HOURS = 48
STATE_FILE = Path(__file__).parent / "gopeek_monitor_state.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - GoPeek Monitor running")

    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print("   Health server on port " + str(port))
    server.serve_forever()

# =========================================================
# DEDUPLICATION
# =========================================================

def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=DEDUP_HOURS)).isoformat()
    cleaned = {k: v for k, v in state.items() if v > cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(cleaned, f, indent=2)
    print("   State saved: " + str(len(cleaned)) + " posts tracked")

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(title, url, subreddit, author, body_preview=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("   Telegram not configured")
        return False

    def escape_html(text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    safe_title = escape_html(title)
    safe_preview = escape_html(body_preview[:200])

    message = "GoPeek Alert\n\n"
    message += safe_title + "\n"
    message += "r/" + subreddit + "  u/" + author + "\n\n"
    message += url + "\n\n"
    message += safe_preview + "..."

    api_url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": False
    }

    try:
        resp = requests.post(api_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("   Telegram sent")
            return True
        else:
            print("   Telegram failed: " + str(resp.status_code) + " - " + resp.text[:200])
            return False
    except Exception as e:
        print("   Telegram error: " + str(e))
        return False

# =========================================================
# AGE CHECK
# =========================================================

def parse_rss_date(date_str):
    if not date_str:
        return None

    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    return None


def is_recent(entry):
    date_str = entry.get("published", "") or entry.get("updated", "")

    if not date_str:
        return True

    post_time = parse_rss_date(date_str)
    if not post_time:
        return True

    if post_time.tzinfo is None:
        post_time = post_time.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age = now - post_time
    max_age = timedelta(hours=MAX_POST_AGE_HOURS)

    is_fresh = age <= max_age
    if not is_fresh:
        age_str = str(age.days) + "d " + str(age.seconds // 3600) + "h"
        print("   Skipping old post (" + age_str + " old): " + entry.title[:50] + "...")

    return is_fresh

# =========================================================
# STRICT MATCHING
# =========================================================

def strict_match(text, phrase):
    """Match phrase as whole words only."""
    if not text:
        return False
    text_lower = text.lower()
    phrase_lower = phrase.lower()

    # Escape special regex chars in phrase
    escaped = re.escape(phrase_lower)

    # Match as whole word with word boundaries
    pattern = r'\b' + escaped + r'\b'
    return bool(re.search(pattern, text_lower))


def matches_keywords(title, body, subreddit):
    """Check if post matches ANY keyword with strict whole-word matching."""
    combined = title + " " + body

    # Check core keywords
    for kw in KEYWORDS:
        if strict_match(combined, kw):
            return True, kw

    # Check extension keywords (only in non-extension subreddits to avoid spam)
    if subreddit not in ["chrome_extensions", "firefox_addons", "edge_extensions"]:
        for kw in EXTENSION_KEYWORDS:
            if strict_match(combined, kw):
                return True, kw

    return False, None

# =========================================================
# QUALITY FILTER
# =========================================================

def is_high_quality_match(title, body, matched_keyword, subreddit):
    """Only alert on genuinely relevant posts."""
    text = (title + " " + body).lower()

    # Must contain a strong intent signal
    intent_signals = [
        "looking for", "need", "want", "recommend", "suggestion",
        "help", "how to", "is there", "any way", "best way",
        "tired of", "fed up", "annoying", "problem", "struggle",
        "too many", "drowning", "overwhelmed", "cluttered"
    ]

    has_intent = any(signal in text for signal in intent_signals)

    # Reject obvious junk
    junk = ["porn", "nsfw", "xxx", "crypto", "nft", "airdrop", "giveaway", "vpn"]
    has_junk = any(j in text for j in junk)

    # Reject if it's just a generic extension announcement (not asking for help)
    if subreddit in ["chrome_extensions"]:
        # In chrome_extensions subreddit, only alert if someone is ASKING
        asking_signals = ["looking for", "need", "want", "recommend", "suggestion", "help", "how to"]
        is_asking = any(s in text for s in asking_signals)
        if not is_asking:
            return False

    return has_intent and not has_junk

# =========================================================
# RSS CHECK
# =========================================================

def check_rss_feed(subreddit, state):
    url = "https://www.reddit.com/r/" + subreddit + "/new/.rss"
    headers = {"User-Agent": "GoPeekMonitor/1.6"}

    try:
        feed = feedparser.parse(url, request_headers=headers)
    except Exception as e:
        print("[RSS Error] r/" + subreddit + ": " + str(e))
        return state

    matches_found = 0
    skipped_old = 0
    skipped_weak = 0
    already_seen = 0

    for entry in feed.entries:
        pid = hashlib.md5((subreddit + ":" + entry.title + ":" + entry.get("author", "")).encode()).hexdigest()

        if pid in state:
            already_seen += 1
            continue

        if not is_recent(entry):
            skipped_old += 1
            state[pid] = datetime.now(timezone.utc).isoformat()
            continue

        title = entry.title
        body = entry.get("summary", "")

        matched, keyword = matches_keywords(title, body, subreddit)

        if matched:
            if not is_high_quality_match(title, body, keyword, subreddit):
                print("   Weak match skipped: " + title[:60] + "... (matched: " + keyword + ")")
                skipped_weak += 1
                state[pid] = datetime.now(timezone.utc).isoformat()
                continue

            link = entry.link
            author = entry.get("author", "unknown").replace("/u/", "").replace("u/", "")

            print("\nMATCH in r/" + subreddit)
            print("   Title: " + title[:80])
            print("   Keyword: " + keyword)
            print("   Link: " + link)

            send_telegram(title, link, subreddit, author, body)
            state[pid] = datetime.now(timezone.utc).isoformat()
            matches_found += 1

    print("   r/" + subreddit + ": " + str(matches_found) + " alerts, " + str(skipped_old) + " old, " + str(skipped_weak) + " weak, " + str(already_seen) + " seen")

    return state

# =========================================================
# BACKGROUND MONITOR
# =========================================================

def monitor_loop():
    print("\nGoPeek Monitor v1.6 started at " + datetime.now().isoformat())
    print("   Subreddits: " + ", ".join(SUBREDDITS))
    print("   Max post age: " + str(MAX_POST_AGE_HOURS) + " hours")
    print("   Check interval: " + str(CHECK_INTERVAL) + "s")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Telegram not configured!")
        return

    print("   Telegram ready")

    state = load_state()
    print("   Loaded state: " + str(len(state)) + " posts tracked")

    while True:
        print("\n[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] Scanning " + str(len(SUBREDDITS)) + " subreddits...")

        for sub in SUBREDDITS:
            state = check_rss_feed(sub, state)
            time.sleep(2)

        save_state(state)
        print("\nRound complete. Sleeping " + str(CHECK_INTERVAL) + "s...")
        time.sleep(CHECK_INTERVAL)

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    monitor_loop()
