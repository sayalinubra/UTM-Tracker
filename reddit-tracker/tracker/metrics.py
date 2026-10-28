"""KPIs and weekly calculations.

How a value "as of time t" is worked out for one reply:
  * replies created after t don't count yet;
  * otherwise use the latest snapshot taken at or before t;
  * if tracking started after t (no snapshot yet), use the earliest snapshot we have.
"Change vs last week" = value now minus value at the start of the current week (Mon 00:00 IST).
"""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import health as H

WEEK = 7 * 24 * 3600


def tz(offset_hours):
    return timezone(timedelta(hours=offset_hours))


def week_start(ts, offset_hours):
    """Epoch seconds of the Monday 00:00 (local time) on or before ts."""
    local = datetime.fromtimestamp(ts, tz(offset_hours))
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.timestamp()


def fmt_date(ts, offset_hours, fmt="%d %b %Y"):
    if ts is None or pd.isna(ts):
        return "–"
    return datetime.fromtimestamp(float(ts), tz(offset_hours)).strftime(fmt)


# --- loading ---------------------------------------------------------------

REPLY_COLUMNS = ["comment_id", "post_id", "account", "permalink", "subreddit", "thread_title",
                 "thread_link", "created_utc", "text", "status", "note", "from_account",
                 "from_link", "parent_id", "depth", "thread_created_utc", "thread_author", "op_engaged",
                 "visibility", "visibility_reason", "visibility_checked_at",
                 "mentions_nubra", "disclosed", "topic"]
SNAP_COLUMNS = ["comment_id", "taken_at", "score", "replies_count", "thread_score", "thread_comments",
                "thread_rank", "top_level_count", "upvote_ratio", "op_engaged"]
RESPONSE_COLUMNS = ["child_id", "comment_id", "author", "body", "created_utc", "sentiment"]
SCAN_COLUMNS = ["post_id", "scanned_at", "comments_scanned", "mentions"]
TIMING_BUCKETS = ["Under 2h", "2–12h", "12h+"]
DEPTH_LABELS = {0: "Top-level comment", 1: "Nested reply"}


def load_replies(conn):
    df = pd.read_sql_query(
        "SELECT r.*, "
        "COALESCE(c.override_mentions, c.mentions_nubra, 0) AS mentions_nubra, "
        "COALESCE(c.override_disclosed, c.disclosed, 0) AS disclosed, "
        "COALESCE(c.override_topic, c.topic, 'Other') AS topic "
        "FROM replies r LEFT JOIN classifications c ON c.comment_id = r.comment_id", conn)
    if df.empty:
        return pd.DataFrame(columns=REPLY_COLUMNS)
    df["mentions_nubra"] = df["mentions_nubra"].astype(bool)
    df["disclosed"] = df["disclosed"].astype(bool)
    return df


def load_snapshots(conn):
    df = pd.read_sql_query(f"SELECT {', '.join(SNAP_COLUMNS)} FROM snapshots ORDER BY taken_at", conn)
    return df if not df.empty else pd.DataFrame(columns=SNAP_COLUMNS)


def load_responses(conn):
    df = pd.read_sql_query(f"SELECT {', '.join(RESPONSE_COLUMNS)} FROM responses", conn)
    return df if not df.empty else pd.DataFrame(columns=RESPONSE_COLUMNS)


def load_scans(conn):
    df = pd.read_sql_query(f"SELECT {', '.join(SCAN_COLUMNS)} FROM thread_scans ORDER BY scanned_at", conn)
    if df.empty:
        return pd.DataFrame(columns=SCAN_COLUMNS)
    df["mentions"] = df["mentions"].apply(lambda s: json.loads(s) if s else {})
    return df


def load_subreddits(conn):
    return pd.read_sql_query("SELECT name, subscribers, fetched_at FROM subreddits", conn)


def empty_responses():
    return pd.DataFrame(columns=RESPONSE_COLUMNS)


def empty_scans():
    return pd.DataFrame(columns=SCAN_COLUMNS)


# --- point-in-time values --------------------------------------------------

def values_at(replies, snaps, t, col, how="last"):
    """Series (index comment_id) of `col` as of time t, for replies created by t.

    how="last" takes the latest snapshot at or before t; how="max" the highest value seen by t
    (used for "OP has replied", which shouldn't flip back if that reply is hidden later).
    """
    alive = replies[replies["created_utc"].fillna(0) <= t]
    if alive.empty:
        return pd.Series(dtype=float)
    s = snaps[snaps["comment_id"].isin(alive["comment_id"])].dropna(subset=[col])
    s = s.assign(**{col: s[col].astype(float)})
    before = s[s["taken_at"] <= t].groupby("comment_id")[col].agg(how)
    earliest = s.groupby("comment_id")[col].first()
    out = before.reindex(alive["comment_id"])
    out = out.fillna(earliest.reindex(alive["comment_id"])).fillna(0)
    return out.astype(float)


def _top_level(replies):
    return replies[replies["depth"].fillna(-1).astype(float) == 0]


def top_comment_rate_at(replies, snaps, t):
    """% of top-level replies (posted by t) ranked #1 in their thread at t."""
    top = _top_level(replies[replies["created_utc"].fillna(0) <= t])
    if top.empty:
        return None
    ranks = values_at(top, snaps, t, "thread_rank")
    return 100.0 * float((ranks == 1).sum()) / len(top)


def op_engagement_rate_at(replies, snaps, t):
    alive = replies[replies["created_utc"].fillna(0) <= t]
    if alive.empty:
        return None
    engaged = values_at(alive, snaps, t, "op_engaged", how="max")
    return 100.0 * float((engaged > 0).sum()) / len(alive)


def positive_reply_rate_at(replies, responses, t):
    """% of direct replies from other people (received by t) that read as positive."""
    if responses is None or responses.empty:
        return None
    alive = replies[replies["created_utc"].fillna(0) <= t]
    r = responses[responses["comment_id"].isin(alive["comment_id"]) & (responses["created_utc"].fillna(0) <= t)]
    if r.empty:
        return None
    return 100.0 * float((r["sentiment"] == "positive").sum()) / len(r)


def brand_mentions_at(replies, scans, t):
    """{brand: comments mentioning it} by other users, across the replied-to threads, as of t."""
    if scans is None or scans.empty:
        return {}
    alive = replies[replies["created_utc"].fillna(0) <= t]
    s = scans[scans["post_id"].isin(alive["post_id"].dropna().unique())]
    if s.empty:
        return {}
    latest = s[s["scanned_at"] <= t].groupby("post_id").last()
    earliest = s.groupby("post_id").first()
    chosen = latest.reindex(earliest.index)
    chosen["mentions"] = chosen["mentions"].where(chosen["mentions"].notna(), earliest["mentions"])
    total = {}
    for mentions in chosen["mentions"]:
        for brand, n in (mentions or {}).items():
            total[brand] = total.get(brand, 0) + int(n)
    return total


def nubra_share_at(replies, scans, t):
    counts = brand_mentions_at(replies, scans, t)
    total = sum(counts.values())
    return 100.0 * counts.get("Nubra", 0) / total if total else None


def thread_reach_at(replies, snaps, t):
    """Sum of thread comment counts, each thread counted once."""
    vals = values_at(replies, snaps, t, "thread_comments")
    if vals.empty:
        return 0.0
    posts = replies.set_index("comment_id").loc[vals.index, "post_id"]
    keys = [p if isinstance(p, str) and p else c for p, c in zip(posts, vals.index)]
    return float(vals.groupby(keys).max().sum())


def kpis_at(replies, snaps, t, responses=None, scans=None):
    """KPIs as of t. Reach-based ones (avg upvotes, thread reach, top-comment rate) leave out replies
    known to be hidden from the public; totals and counts include every reply."""
    alive = replies[replies["created_utc"].fillna(0) <= t]
    n = len(alive)
    upvotes = float(values_at(replies, snaps, t, "score").sum())
    received = float(values_at(replies, snaps, t, "replies_count").sum())
    mentions = alive[alive["mentions_nubra"]]
    disclosure = (100.0 * mentions["disclosed"].sum() / len(mentions)) if len(mentions) else None
    public = H.publicly_visible(replies)
    n_public = int((public["created_utc"].fillna(0) <= t).sum())
    public_upvotes = float(values_at(public, snaps, t, "score").sum())
    return {
        "replies_posted": n,
        "total_upvotes": upvotes,
        "avg_upvotes": public_upvotes / n_public if n_public else None,
        "replies_received": received,
        "thread_reach": thread_reach_at(public, snaps, t),
        "hidden_replies": n - n_public,
        "disclosure_rate": disclosure,
        "top_comment_rate": top_comment_rate_at(public, snaps, t),
        "op_engagement_rate": op_engagement_rate_at(replies, snaps, t),
        "positive_reply_rate": positive_reply_rate_at(replies, responses, t),
        "nubra_share": nubra_share_at(replies, scans, t),
    }


def kpis_with_change(replies, snaps, now, offset_hours, responses=None, scans=None):
    """{kpi: (value_now, change_since_week_start or None)}."""
    current = kpis_at(replies, snaps, now, responses, scans)
    previous = kpis_at(replies, snaps, week_start(now, offset_hours), responses, scans)
    out = {}
    for key, value in current.items():
        prev = previous[key]
        change = None if value is None or prev is None else value - prev
        out[key] = (value, change)
    return out


# --- time series and groupings --------------------------------------------

def weekly_activity(replies, snaps, now, offset_hours):
    """DataFrame: week (date), replies_posted, upvotes_gained - one row per week."""
    if replies.empty or replies["created_utc"].isna().all():
        return pd.DataFrame(columns=["week", "replies_posted", "upvotes_gained"])
    first = week_start(replies["created_utc"].min(), offset_hours)
    rows, start = [], first
    while start <= now:
        end = start + WEEK
        clamp = min(end, now)
        posted = int(((replies["created_utc"] >= start) & (replies["created_utc"] < end)).sum())
        gained = (values_at(replies, snaps, clamp, "score").sum()
                  - values_at(replies, snaps, start, "score").sum())
        rows.append({"week": datetime.fromtimestamp(start, tz(offset_hours)).date(),
                     "replies_posted": posted, "upvotes_gained": float(gained)})
        start = end
    return pd.DataFrame(rows)


LATEST_COLS = ("score", "replies_count", "thread_score", "thread_comments", "thread_rank",
               "top_level_count", "upvote_ratio")


def latest_stats(replies, snaps, responses=None, subreddits=None, offset_hours=5.5):
    """Replies with their most recent stats and derived per-reply metrics attached."""
    df = replies.copy()
    if snaps.empty:
        for col in LATEST_COLS:
            df[col] = None
    else:
        latest = snaps.groupby("comment_id")[list(LATEST_COLS)].last()
        for col in LATEST_COLS:
            df[col] = df["comment_id"].map(latest[col])
        # a nested reply never has a thread rank
        df.loc[df["depth"].fillna(-1).astype(float) != 0, "thread_rank"] = None
    df["upvotes_24h"] = df["comment_id"].map(velocity_24h(replies, snaps))
    df["hours_after_post"] = hours_after_post(replies)
    df["timing"] = df["hours_after_post"].apply(timing_bucket)
    df["depth_label"] = df["depth"].map(lambda d: DEPTH_LABELS.get(int(d)) if d == d and d is not None else None)
    op_from_snaps = (snaps.dropna(subset=["op_engaged"]).groupby("comment_id")["op_engaged"].max()
                     if not snaps.empty else pd.Series(dtype=float))
    df["op_replied"] = (df["comment_id"].map(op_from_snaps).fillna(0).astype(float) > 0) | \
        (df["op_engaged"].fillna(0).astype(float) > 0)
    if responses is not None and not responses.empty:
        counts = responses.groupby(["comment_id", "sentiment"]).size().unstack(fill_value=0)
        for s in ("positive", "neutral", "negative"):
            df[f"resp_{s}"] = df["comment_id"].map(counts[s]) if s in counts else 0
            df[f"resp_{s}"] = df[f"resp_{s}"].fillna(0).astype(int)
    else:
        for s in ("positive", "neutral", "negative"):
            df[f"resp_{s}"] = 0
    if subreddits is not None and not subreddits.empty:
        df["subscribers"] = df["subreddit"].str.lower().map(
            subreddits.assign(k=subreddits["name"].str.lower()).set_index("k")["subscribers"])
    else:
        df["subscribers"] = None
    return df


def velocity_24h(replies, snaps):
    """Upvotes in the first 24h: latest score snapshotted within 24h of posting (else NaN)."""
    if snaps.empty or replies.empty:
        return pd.Series(dtype=float)
    created = replies.set_index("comment_id")["created_utc"]
    s = snaps.dropna(subset=["score"]).copy()
    s["created"] = s["comment_id"].map(created)
    s = s[(s["taken_at"] >= s["created"]) & (s["taken_at"] <= s["created"] + 24 * 3600)]
    return s.groupby("comment_id")["score"].last().astype(float)


def hours_after_post(replies):
    h = (replies["created_utc"].astype(float) - replies["thread_created_utc"].astype(float)) / 3600
    return h.clip(lower=0)


def timing_bucket(hours):
    if hours is None or hours != hours:
        return None
    return TIMING_BUCKETS[0] if hours < 2 else TIMING_BUCKETS[1] if hours < 12 else TIMING_BUCKETS[2]


def size_bucket(subscribers, buckets):
    if subscribers is None or subscribers != subscribers:
        return None
    label = None
    for threshold, name in sorted(buckets, key=lambda b: b[0]):
        if subscribers >= threshold:
            label = name
    return label


def works_best(stats, column, order=None):
    """Avg upvotes (publicly visible replies only) and reply count per value of `column`.

    Rows with no value are left out.
    """
    df = stats.dropna(subset=[column])
    if df.empty:
        return pd.DataFrame(columns=[column, "replies", "avg_upvotes"])
    counts = df.groupby(column)["comment_id"].count().rename("replies")
    avgs = H.publicly_visible(df).groupby(column)["score"].agg(lambda s: s.astype(float).mean()).rename("avg_upvotes")
    g = pd.concat([counts, avgs], axis=1).reset_index().rename(columns={"index": column})
    g["avg_upvotes"] = g["avg_upvotes"].fillna(0)
    if order:
        g[column] = pd.Categorical(g[column], categories=order, ordered=True)
        g = g.sort_values(column)
        g[column] = g[column].astype(str)
    return g


def by_group(replies, snaps, column):
    """Replies posted per group (all), plus averages over publicly visible replies only."""
    df = latest_stats(replies, snaps)
    if df.empty:
        return pd.DataFrame(columns=[column, "replies", "avg_upvotes", "avg_replies_received"])
    counts = df.groupby(column)["comment_id"].count().rename("replies")
    public = H.publicly_visible(df)
    avgs = public.groupby(column).agg(avg_upvotes=("score", lambda s: s.astype(float).mean()),
                                      avg_replies_received=("replies_count", lambda s: s.astype(float).mean()))
    g = pd.concat([counts, avgs], axis=1)
    g.index.name = column
    return g.reset_index().fillna(0).sort_values("avg_upvotes", ascending=False)


def distinct_snapshot_times(snaps):
    return 0 if snaps.empty else int(snaps["taken_at"].nunique())
