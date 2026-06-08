#!/usr/bin/env python3
"""
GoPeek Reddit Monitor v1.2 — Cron Mode
Runs once per invocation, checks all subreddits, exits.
Designed for Railway Cron Jobs or scheduled runners.
"""

import os
import re
import json
import hashlib
import feedparser
import requests
from datetime import datetime, timedelta
from pathlib import Path

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

KEYWORDS = [
    "tab", "tabs", "tab overload", "too many tabs",
    "peek", "preview", "preview links", "glance",
    "sidebar", "side bar",
    "browser extension", "firefox extension", "chrome extension",
    "link preview", "hover preview"
]

DEDUP_HOURS = 72
STATE_FILE = Path(__file__).parent / "gopeek_monitor_state.json"

# ---------------------------------------------------------
# NOTIFICATION SETTINGS
# ---------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

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
    cutoff = (datetime.now() - timedelta(hours=DEDUP_HOURS)).isoformat()
    cleaned = {k: v for k, v in state.items() if v > cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(cleaned, f, indent=2)

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(title, url, subreddit, author, body_preview=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"   ⚠️ Telegram not configured")
        return False
    
    message = f"""🚀 <b>GoPeek Alert</b>

📌 <b>{title}</b>
🏷 r/{subreddit}  👤 u/{author}

🔗 <a href="{url}">View on Reddit</a>

<i>{body_preview[:200]}...</i>"""

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        resp = requests.post(api_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"   ✅ Telegram sent")
            return True
        else:
            print(f"   ❌ Telegram failed: {resp.status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Telegram error: {e}")
        return False

# =========================================================
# KEYWORD MATCHING
# =========================================================

def matches_keywords(text):
    if not text:
        return False
    text_lower = text.lower()
    for kw in KEYWORDS:
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        if re.search(pattern, text_lower):
            return True
    return False

# =========================================================
# RSS CHECK
# =========================================================

def check_rss_feed(subreddit, state):
    url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
    headers = {"User-Agent": "GoPeekMonitor/1.2"}
    
    try:
        feed = feedparser.parse(url, request_headers=headers)
    except Exception as e:
        print(f"[RSS Error] r/{subreddit}: {e}")
        return state
    
    for entry in feed.entries:
        pid = hashlib.md5(f"{subreddit}:{entry.title}:{entry.get('author', '')}".encode()).hexdigest()
        
        if pid in state:
            continue
        
        title = entry.title
        body = entry.get("summary", "")
        
        if matches_keywords(title) or matches_keywords(body):
            link = entry.link
            author = entry.get("author", "unknown").replace("/u/", "").replace("u/", "")
            
            print(f"\n🎯 MATCH in r/{subreddit}")
            print(f"   Title: {title[:80]}")
            print(f"   Link: {link}")
            
            send_telegram(title, link, subreddit, author, body)
            state[pid] = datetime.now().isoformat()
    
    return state

# =========================================================
# MAIN — Runs once, checks all, exits
# =========================================================

def main():
    print(f"💓 GoPeek Monitor run at {datetime.now().isoformat()}")
    print(f"   Subreddits: {', '.join(SUBREDDITS)}")
    print(f"   Keywords: {', '.join(KEYWORDS[:5])}...")
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ ERROR: Telegram not configured!")
        return
    
    print(f"   ✅ Telegram ready")
    
    state = load_state()
    total_matches = 0
    
    for sub in SUBREDDITS:
        before = len(state)
        state = check_rss_feed(sub, state)
        if len(state) > before:
            total_matches += 1
    
    save_state(state)
    print(f"\n✅ Done. Total matches: {total_matches}")
    print(f"   Next run: every 5 minutes (via Railway scheduler)")

if __name__ == "__main__":
    main()
