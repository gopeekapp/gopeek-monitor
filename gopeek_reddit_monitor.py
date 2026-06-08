#!/usr/bin/env python3
"""
GoPeek Reddit Monitor v1.0
Monitors Reddit for keywords related to GoPeek and sends instant notifications.
RSS Method — No Reddit API key needed.
"""

import os
import re
import time
import json
import hashlib
import feedparser
import requests
from datetime import datetime, timedelta
from pathlib import Path

# Keep Railway from killing "idle" container
print(f"💓 GoPeek Monitor starting at {datetime.now().isoformat()}")
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

CHECK_INTERVAL = 300
DEDUP_HOURS = 72
STATE_FILE = Path(__file__).parent / "gopeek_monitor_state.json"

# ---------------------------------------------------------
# NOTIFICATION SETTINGS (from environment variables)
# ---------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# =========================================================
# DEDUPLICATION ENGINE
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
# NOTIFICATION SENDERS
# =========================================================

def send_telegram(title, url, subreddit, author, body_preview=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
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
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram Error] {e}")
        return False

def notify_all(title, url, subreddit, author, body=""):
    preview = body.replace("\\n", " ").replace("\\r", "")[:300]
    result = send_telegram(title, url, subreddit, author, preview)
    print(f"   {'✅' if result else '❌'} Telegram")
    return result

# =========================================================
# KEYWORD MATCHING
# =========================================================

def matches_keywords(text):
    if not text:
        return False
    text_lower = text.lower()
    for kw in KEYWORDS:
        pattern = r'\\b' + re.escape(kw.lower()) + r'\\b'
        if re.search(pattern, text_lower):
            return True
    return False

# =========================================================
# RSS FEED MONITOR
# =========================================================

def check_rss_feed(subreddit, state):
    url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
    headers = {"User-Agent": "GoPeekMonitor/1.0"}
    
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
            
            notify_all(title, link, subreddit, author, body)
            state[pid] = datetime.now().isoformat()
    
    return state

def run_rss_monitor():
    print("=" * 60)
    print("GoPeek Reddit Monitor — RSS Mode")
    print(f"Subreddits: {', '.join(SUBREDDITS)}")
    print(f"Keywords: {', '.join(KEYWORDS)}")
    print(f"Check interval: {CHECK_INTERVAL}s")
    print("=" * 60)
    
    # Verify Telegram is configured
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ ERROR: Telegram not configured!")
        print(f"   TOKEN present: {bool(TELEGRAM_BOT_TOKEN)}")
        print(f"   CHAT_ID present: {bool(TELEGRAM_CHAT_ID)}")
        return
    
    print(f"✅ Telegram configured: TOKEN={'Yes' if TELEGRAM_BOT_TOKEN else 'No'}, CHAT_ID={TELEGRAM_CHAT_ID}")
    
    state = load_state()
    
    while True:
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scanning {len(SUBREDDITS)} subreddits...")
        
        match_count = 0
        for sub in SUBREDDITS:
            before = len(state)
            state = check_rss_feed(sub, state)
            if len(state) > before:
                match_count += 1
            time.sleep(2)
        
        save_state(state)
        print(f"   ✅ Done. Matches this round: {match_count}. Sleeping {CHECK_INTERVAL}s...")
        print(f"   💓 Health check: {datetime.now().isoformat()}")  # Keeps Railway alive
        time.sleep(CHECK_INTERVAL)

# =========================================================
# MAIN ENTRY
# =========================================================

if __name__ == "__main__":
    run_rss_monitor()
