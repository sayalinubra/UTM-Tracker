"""Builds the "Sync from Reddit" bookmarklet with the current accounts.txt / comments.txt baked in."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlparse

from .inputs import parse_accounts, parse_comments, read_text

TEMPLATE = Path(__file__).with_name("bookmarklet.js")
PLACEHOLDER = "__NB_CONFIG__"
# characters left as-is in the javascript: URL; everything else (%, #, quotes, spaces...) is escaped
SAFE = "()[]{},;:=+-*/<>!?&|'_$.~@^"


def list_version(usernames, links):
    """Short code that changes whenever either list or the bookmarklet code changes (stored in every export)."""
    payload = json.dumps({"users": sorted(u.lower() for u in usernames),
                          "links": sorted(l.raw for l in links),
                          "code": TEMPLATE.read_text(encoding="utf-8")}, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


def build_config(usernames, links, cfg):
    return {
        "users": list(usernames),
        "links": [{"raw": l.raw, "post": l.post_id, "comment": l.comment_id, "share": l.needs_resolving,
                   # share links are fetched by path so the request stays on the page's own origin
                   "path": urlparse(l.raw if "://" in l.raw else "https://" + l.raw).path}
                  for l in links],
        "maxPages": int(cfg.get("browser_sync_max_pages", 3)),
        "delay": int(float(cfg.get("browser_sync_delay_seconds", 1.5)) * 1000),
        "retryDelay": int(float(cfg.get("browser_sync_retry_seconds", 10)) * 1000),
        "threadLimit": 500,
        "listVersion": list_version(usernames, links),
    }


def build_js(config):
    return TEMPLATE.read_text(encoding="utf-8").replace(PLACEHOLDER, json.dumps(config))


def to_url(js):
    # drop indentation and blank lines to keep the bookmark short (the code has no multi-line strings)
    compact = "\n".join(line.strip() for line in js.splitlines() if line.strip())
    return "javascript:" + quote(compact, safe=SAFE)


def for_files(accounts_path, comments_path, cfg):
    """(bookmarklet URL, list version, number of usernames, number of links) for the current files."""
    usernames = parse_accounts(read_text(accounts_path)).usernames
    links = parse_comments(read_text(comments_path)).links
    config = build_config(usernames, links, cfg)
    return to_url(build_js(config)), config["listVersion"], len(usernames), len(links)
