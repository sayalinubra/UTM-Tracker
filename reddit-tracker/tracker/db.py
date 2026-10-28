"""SQLite storage. All timestamps are UTC unix seconds."""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username     TEXT PRIMARY KEY COLLATE NOCASE,
    status       TEXT,
    message      TEXT,
    last_checked REAL
);
CREATE TABLE IF NOT EXISTS replies (
    comment_id    TEXT PRIMARY KEY,
    post_id       TEXT,
    account       TEXT,
    permalink     TEXT,
    subreddit     TEXT,
    thread_title  TEXT,
    thread_link   TEXT,
    created_utc   REAL,
    text          TEXT,
    status        TEXT,
    from_account  INTEGER DEFAULT 0,
    from_link     INTEGER DEFAULT 0,
    first_seen    REAL,
    last_seen     REAL,
    note          TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id      TEXT NOT NULL,
    taken_at        REAL NOT NULL,
    score           INTEGER,
    replies_count   INTEGER,
    thread_score    INTEGER,
    thread_comments INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snap_comment ON snapshots(comment_id, taken_at);
CREATE TABLE IF NOT EXISTS classifications (
    comment_id         TEXT PRIMARY KEY,
    mentions_nubra     INTEGER,
    disclosed          INTEGER,
    topic              TEXT,
    override_mentions  INTEGER,
    override_disclosed INTEGER,
    override_topic     TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- direct replies other people (or my accounts) left under a tracked reply
CREATE TABLE IF NOT EXISTS responses (
    child_id    TEXT PRIMARY KEY,
    comment_id  TEXT NOT NULL,
    author      TEXT,
    body        TEXT,
    created_utc REAL,
    sentiment   TEXT
);
CREATE INDEX IF NOT EXISTS idx_resp_comment ON responses(comment_id);
-- brand mentions by other users in a thread, one row per scan (history)
CREATE TABLE IF NOT EXISTS thread_scans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id          TEXT NOT NULL,
    scanned_at       REAL NOT NULL,
    comments_scanned INTEGER,
    mentions         TEXT
);
CREATE INDEX IF NOT EXISTS idx_scan_post ON thread_scans(post_id, scanned_at);
-- logged-out health checks from browser exports, one row per account per check (history)
CREATE TABLE IF NOT EXISTS account_health (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL,
    checked_at   REAL NOT NULL,
    status       TEXT NOT NULL,
    reason       TEXT,
    normal_count INTEGER,
    anon_count   INTEGER,
    UNIQUE(username, checked_at)
);
CREATE TABLE IF NOT EXISTS subreddits (
    name        TEXT PRIMARY KEY COLLATE NOCASE,
    subscribers INTEGER,
    fetched_at  REAL
);
"""

# Columns added after the first release; added in place to existing databases.
MIGRATIONS = {
    "replies": {"parent_id": "TEXT", "depth": "INTEGER", "thread_created_utc": "REAL",
                "thread_author": "TEXT", "op_engaged": "INTEGER",
                "visibility": "TEXT", "visibility_reason": "TEXT", "visibility_checked_at": "REAL"},
    "snapshots": {"thread_rank": "INTEGER", "top_level_count": "INTEGER", "upvote_ratio": "REAL",
                  "op_engaged": "INTEGER"},
}

# Reply fields that are filled in whenever a newer, non-empty value arrives.
FILL_FIELDS = ("post_id", "permalink", "subreddit", "thread_title", "thread_link", "created_utc",
               "parent_id", "depth", "thread_created_utc", "thread_author")


def _migrate(conn):
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, kind in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    conn.commit()


def connect(path):
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()


def upsert_account(conn, username, status, message, now):
    conn.execute(
        "INSERT INTO accounts(username, status, message, last_checked) VALUES(?,?,?,?) "
        "ON CONFLICT(username) DO UPDATE SET status=excluded.status, message=excluded.message, "
        "last_checked=excluded.last_checked",
        (username, status, message, now))


def upsert_reply(conn, r, now, from_account=False, from_link=False):
    """Insert or update a reply. Never overwrites stored text with a removed/deleted body.

    Returns True when the reply is new.
    """
    existing = conn.execute("SELECT * FROM replies WHERE comment_id=?", (r["comment_id"],)).fetchone()
    if existing is None:
        cols = ["comment_id", "account", "text", "status", "note", "op_engaged", *FILL_FIELDS]
        values = [r.get(c) for c in cols]
        values[cols.index("status")] = r.get("status", "live")
        conn.execute(
            f"INSERT INTO replies({', '.join(cols)}, from_account, from_link, first_seen, last_seen) "
            f"VALUES({', '.join('?' * len(cols))}, ?, ?, ?, ?)",
            (*values, int(from_account), int(from_link), now, now))
        return True
    merged = dict(existing)
    for key in FILL_FIELDS:
        if r.get(key) not in (None, ""):
            merged[key] = r[key]
    # keep the real author name once known (deleted comments report "[deleted]")
    if r.get("account") and r["account"] != "[deleted]":
        merged["account"] = r["account"]
    if r.get("text") and r.get("status", "live") == "live":
        merged["text"] = r["text"]
    if r.get("status"):
        merged["status"] = r["status"]
    if r.get("op_engaged") is not None:
        # sticky: once the OP has replied, keep it even if that reply is later hidden
        merged["op_engaged"] = int(bool(existing["op_engaged"]) or bool(r["op_engaged"]))
    merged["note"] = r.get("note")
    merged["from_account"] = int(existing["from_account"] or from_account)
    merged["from_link"] = int(existing["from_link"] or from_link)
    merged["last_seen"] = now
    cols = ["account", "text", "status", "note", "op_engaged", "from_account", "from_link", "last_seen",
            *FILL_FIELDS]
    conn.execute(f"UPDATE replies SET {', '.join(c + '=?' for c in cols)} WHERE comment_id=?",
                 (*[merged[c] for c in cols], r["comment_id"]))
    return False


def add_snapshot(conn, comment_id, taken_at, score, replies_count, thread_score, thread_comments,
                 thread_rank=None, top_level_count=None, upvote_ratio=None, op_engaged=None):
    conn.execute(
        "INSERT INTO snapshots(comment_id, taken_at, score, replies_count, thread_score, thread_comments, "
        "thread_rank, top_level_count, upvote_ratio, op_engaged) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (comment_id, taken_at, score, replies_count, thread_score, thread_comments, thread_rank,
         top_level_count, upvote_ratio, None if op_engaged is None else int(op_engaged)))


def save_response(conn, child_id, comment_id, author, body, created_utc, sentiment):
    conn.execute(
        "INSERT INTO responses(child_id, comment_id, author, body, created_utc, sentiment) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(child_id) DO UPDATE SET author=CASE WHEN excluded.author='[deleted]' THEN author "
        "ELSE excluded.author END, body=CASE WHEN excluded.body IN ('[deleted]','[removed]') THEN body "
        "ELSE excluded.body END, sentiment=CASE WHEN excluded.body IN ('[deleted]','[removed]') "
        "THEN sentiment ELSE excluded.sentiment END",
        (child_id, comment_id, author, body, created_utc, sentiment))


def add_thread_scan(conn, post_id, scanned_at, comments_scanned, mentions):
    conn.execute("INSERT INTO thread_scans(post_id, scanned_at, comments_scanned, mentions) VALUES(?,?,?,?)",
                 (post_id, scanned_at, comments_scanned, json.dumps(mentions, sort_keys=True)))


def add_account_health(conn, username, checked_at, status, reason, normal_count=None, anon_count=None):
    """Returns False when this account already has a check at this time (same export imported twice)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO account_health(username, checked_at, status, reason, normal_count, anon_count) "
        "VALUES(?,?,?,?,?,?)", (username, checked_at, status, reason, normal_count, anon_count))
    return cur.rowcount > 0


def set_visibility(conn, comment_id, visibility, reason, checked_at):
    """Store a reply's public visibility, unless a newer check is already stored."""
    conn.execute(
        "UPDATE replies SET visibility=?, visibility_reason=?, visibility_checked_at=? "
        "WHERE comment_id=? AND (visibility_checked_at IS NULL OR visibility_checked_at <= ?)",
        (visibility, reason, checked_at, comment_id, checked_at))


def subreddit_fetched_at(conn, name):
    row = conn.execute("SELECT fetched_at FROM subreddits WHERE name=?", (name,)).fetchone()
    return row["fetched_at"] if row else None


def save_subreddit(conn, name, subscribers, now):
    conn.execute("INSERT INTO subreddits(name, subscribers, fetched_at) VALUES(?,?,?) "
                 "ON CONFLICT(name) DO UPDATE SET subscribers=COALESCE(excluded.subscribers, subscribers), "
                 "fetched_at=excluded.fetched_at", (name, subscribers, now))


def save_classification(conn, comment_id, mentions, disclosed, topic):
    conn.execute(
        "INSERT INTO classifications(comment_id, mentions_nubra, disclosed, topic) VALUES(?,?,?,?) "
        "ON CONFLICT(comment_id) DO UPDATE SET mentions_nubra=excluded.mentions_nubra, "
        "disclosed=excluded.disclosed, topic=excluded.topic",
        (comment_id, int(mentions), int(disclosed), topic))


def save_override(conn, comment_id, mentions=None, disclosed=None, topic=None):
    """Set manual overrides. Pass None to clear one."""
    conn.execute("INSERT OR IGNORE INTO classifications(comment_id) VALUES(?)", (comment_id,))
    conn.execute(
        "UPDATE classifications SET override_mentions=?, override_disclosed=?, override_topic=? "
        "WHERE comment_id=?",
        (None if mentions is None else int(mentions), None if disclosed is None else int(disclosed),
         topic, comment_id))
    conn.commit()
