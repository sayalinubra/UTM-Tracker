"""Glue used by both the dashboard and run_daily.py."""
import time
from dataclasses import dataclass

import pandas as pd

from . import config as C
from . import db
from . import health as H
from . import metrics as m
from . import report
from .reddit_client import RedditClient
from .sync import is_tracked, load_tracking, refresh, refresh_due, sync, sync_and_refresh  # noqa: F401


@dataclass
class Data:
    replies: pd.DataFrame
    snaps: pd.DataFrame
    responses: pd.DataFrame
    scans: pd.DataFrame
    subreddits: pd.DataFrame
    tracking: object
    history: pd.DataFrame = None   # account health checks (all accounts; not narrowed by filters)

    def subset(self, replies):
        """Same data limited to the given replies (e.g. after filters)."""
        ids = set(replies["comment_id"])
        posts = set(replies["post_id"].dropna())
        return Data(replies, self.snaps[self.snaps["comment_id"].isin(ids)],
                    self.responses[self.responses["comment_id"].isin(ids)],
                    self.scans[self.scans["post_id"].isin(posts)], self.subreddits, self.tracking, self.history)


def make_client(cfg, session=None, sleep=time.sleep):
    return RedditClient(cfg["user_agent"], interval=cfg["request_interval_seconds"],
                        max_retries=cfg["max_retries"], backoff_base=cfg["backoff_base_seconds"],
                        session=session, sleep=sleep)


def open_db(path=None):
    return db.connect(path or C.DB_FILE)


def tracked_data(conn, accounts_path=None, comments_path=None):
    """Everything for the replies that accounts.txt / comments.txt currently list."""
    tracking, _ = load_tracking(conn, accounts_path or C.ACCOUNTS_FILE, comments_path or C.COMMENTS_FILE)
    replies = m.load_replies(conn)
    if not replies.empty:
        replies = replies[replies.apply(lambda r: bool(is_tracked(r, tracking)), axis=1)].copy()
    history = H.load_history(conn)
    tracked_names = {u.lower() for u in tracking.usernames}
    history = history[history["username"].str.lower().isin(tracked_names)] if not history.empty else history
    full = Data(replies, m.load_snapshots(conn), m.load_responses(conn), m.load_scans(conn),
                m.load_subreddits(conn), tracking, history)
    return full.subset(replies)


def auto_weekly_report(conn, cfg, now=None, reports_dir=None):
    """Save last week's detailed + manager reports if they don't exist yet. Returns new paths."""
    now = now or time.time()
    d = tracked_data(conn)
    return report.ensure_last_week_report(d.replies, d.snaps, reports_dir or C.REPORTS_DIR, now,
                                          cfg["utc_offset_hours"], d.responses, d.scans, d.history, cfg)
