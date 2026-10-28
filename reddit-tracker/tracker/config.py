"""Paths and settings loaded from config.json / assets/brand.json."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS_FILE = ROOT / "accounts.txt"
COMMENTS_FILE = ROOT / "comments.txt"
CONFIG_FILE = ROOT / "config.json"
BRAND_FILE = ROOT / "assets" / "brand.json"
DB_FILE = Path(os.environ.get("REDDIT_TRACKER_DB") or ROOT / "data" / "reddit.db")
REPORTS_DIR = ROOT / "reports"
LOGO_LIGHT_ON_DARK = ROOT / "assets" / "nubra-logo.png"
LOGO_DARK_ON_LIGHT = ROOT / "assets" / "nubra-logo-dark.png"

DEFAULTS = {
    "user_agent": "windows:nubra-reddit-reply-tracker:1.0 (personal read-only stats tracker)",
    "request_interval_seconds": 2.0,
    "max_retries": 5,
    "backoff_base_seconds": 5,
    "max_pages_per_user": 10,
    "auto_refresh_hours": 6,
    "sync_mode": "browser",
    "browser_sync_max_pages": 3,
    "browser_sync_delay_seconds": 1.5,
    "utc_offset_hours": 5.5,
    "nubra_terms": ["nubra", "nubra.io"],
    "disclosure_phrases": ["i work at nubra", "i work for nubra", "disclosure"],
    "topic_uses_thread_title": True,
    "topics": {},
    "competitors": {},
    "sentiment": {"positive": [], "negative": []},
    "subreddit_size_buckets": [[0, "Small (<100k members)"], [100000, "Medium (100k–1M)"],
                               [1000000, "Large (1M+)"]],
    "subreddit_info_max_age_hours": 24,
}

NEUTRAL_BRAND = {
    "logo": {"navy": "#030712", "white": "#FFFFFF"},
    "accent": "#6382D9",
    "status": {"positive": "#2E9E6A", "negative": "#D9534F", "warning": "#C98A1B"},
    "dark": {"background": "#030712", "surface": "#0C111D", "border": "#1E2433",
             "text": "#FFFFFF", "muted": "#9D9FA3"},
    "light": {"background": "#F5F6F7", "surface": "#FFFFFF", "border": "#E1E2E4",
              "text": "#030712", "muted": "#5F6168"},
}


def load_config(path=CONFIG_FILE):
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    return cfg


def load_brand(path=BRAND_FILE):
    brand = json.loads(json.dumps(NEUTRAL_BRAND))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return brand
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(brand.get(key), dict):
            brand[key].update(value)
        else:
            brand[key] = value
    return brand
