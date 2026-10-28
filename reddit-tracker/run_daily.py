"""Sync + refresh stats + weekly report, without the dashboard.

Run it by hand with:  python run_daily.py
or schedule run_daily.bat with Windows Task Scheduler (see README.md).
"""
import sys
import time
from datetime import datetime

from tracker import config as C
from tracker import db, service


def log(msg):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def main():
    cfg = C.load_config()
    conn = service.open_db()
    client = service.make_client(cfg)
    last_pct = [-1]

    def progress(fraction, message):
        pct = int(fraction * 100)
        if pct // 10 != last_pct[0] // 10:
            log(f"{pct:3d}%  {message}")
            last_pct[0] = pct

    mode = db.get_meta(conn, "sync_mode") or cfg.get("sync_mode", "browser")
    if mode != "direct":
        # Browser sync needs you on reddit.com, so the scheduled run only keeps the weekly reports up to date.
        log("Browser sync is selected in the dashboard, so nothing is fetched from Reddit here. "
            "Click the Sync from Reddit bookmark on reddit.com and import the file to update stats.")
        for path in service.auto_weekly_report(conn, cfg):
            log(f"Weekly report saved: {path}")
        db.set_meta(conn, "last_daily_run", time.time())
        return 0

    log("Starting daily sync...")
    result = service.sync_and_refresh(conn, client, cfg, C.ACCOUNTS_FILE, C.COMMENTS_FILE, progress)
    for msg in result.messages:
        log(msg)
    for warning in result.warnings:
        log("WARNING: " + warning)
    for path in service.auto_weekly_report(conn, cfg):
        log(f"Weekly report saved: {path}")
    db.set_meta(conn, "last_daily_run", time.time())
    log(f"Done ({client.request_count} requests to Reddit).")
    return 1 if result.aborted else 0


if __name__ == "__main__":
    sys.exit(main())
