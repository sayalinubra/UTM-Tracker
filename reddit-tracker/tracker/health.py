"""Account health and reply visibility, from the bookmarklet's logged-out (anonymous) checks.

All rules are plain functions so they can be tested without a database or Reddit.
"""
from dataclasses import dataclass

import pandas as pd

# account statuses
VISIBLE, POSSIBLY_HIDDEN, SHADOWBANNED, SUSPENDED, UNKNOWN = (
    "visible", "possibly_hidden", "shadowbanned", "suspended", "unknown")
ACCOUNT_LABELS = {
    VISIBLE: "Visible",
    POSSIBLY_HIDDEN: "Possibly hidden",
    SHADOWBANNED: "Shadowbanned or deleted",
    SUSPENDED: "Suspended",
    UNKNOWN: "Couldn't check",
}
PROBLEM_STATUSES = {POSSIBLY_HIDDEN, SHADOWBANNED, SUSPENDED}

# reply visibility
PUBLIC, REMOVED, DELETED, NOT_VISIBLE, VIS_UNKNOWN = "public", "removed", "deleted", "not_visible", "unknown"
VISIBILITY_LABELS = {
    PUBLIC: "Public",
    REMOVED: "Removed by moderators",
    DELETED: "Deleted",
    NOT_VISIBLE: "Not visible publicly",
    VIS_UNKNOWN: "Couldn't check",
}
HIDDEN_VISIBILITY = {REMOVED, DELETED, NOT_VISIBLE}

DEFAULTS = {
    "possibly_hidden_ratio": 0.5,
    "possibly_hidden_min_comments": 3,
    "at_risk_window_days": 14,
    "at_risk_min_replies": 3,
    "at_risk_stuck_share": 0.75,
    "at_risk_stuck_min_age_hours": 24,
    "at_risk_removed_share": 0.20,
    "at_risk_nubra_count": 3,
    "at_risk_nubra_share": 0.40,
}


def settings(cfg):
    return {**DEFAULTS, **(cfg.get("account_health") or {})}


@dataclass
class AccountCheck:
    status: str
    reason: str
    normal_count: int = None
    anon_count: int = None


def classify_account(check, cfg=None):
    """check: the bookmarklet's record for one account, e.g.
    {"about": {"http": 200, "json": true, "is_suspended": false}, "normal_count": 40, "anon_count": 12}
    """
    s = settings(cfg or {})
    about = (check or {}).get("about") or {}
    normal, anon = check.get("normal_count") if check else None, check.get("anon_count") if check else None
    http = about.get("http")
    if about.get("error") and http is None:
        return AccountCheck(UNKNOWN, f"Couldn't check: {about['error']}", normal, anon)
    if http == 404 and about.get("json", True):
        return AccountCheck(SHADOWBANNED, "Profile returns 'not found' to logged-out visitors", normal, anon)
    if http == 200 and about.get("is_suspended"):
        return AccountCheck(SUSPENDED, "Reddit reports the account as suspended", normal, anon)
    if http != 200:
        return AccountCheck(UNKNOWN, f"Couldn't check (Reddit answered {http or 'nothing usable'})", normal, anon)
    if anon is None and check.get("anon_error"):
        return AccountCheck(VISIBLE, f"Profile visible; comment check failed ({check['anon_error']})", normal, anon)
    if (normal is not None and anon is not None and normal >= s["possibly_hidden_min_comments"]
            and anon < s["possibly_hidden_ratio"] * normal):
        return AccountCheck(POSSIBLY_HIDDEN,
                            f"Only {anon} of {normal} recent comments show to logged-out visitors "
                            f"(profile content hidden or shadowbanned)", normal, anon)
    return AccountCheck(VISIBLE, "Profile and comments visible to logged-out visitors", normal, anon)


def classify_reply(record, account_status=None, logged_in_status=None):
    """(visibility, reason) for one reply.

    record: the bookmarklet's anonymous check, e.g. {"found": true, "body": "ok"} or
    {"found": false, "http": 404} or {"error": "..."}; None means it wasn't checked.
    """
    if account_status in (SHADOWBANNED, SUSPENDED):
        return NOT_VISIBLE, f"Account is {ACCOUNT_LABELS[account_status].lower()}"
    if logged_in_status == "deleted":
        return DELETED, "Deleted by the account"
    if record is None:
        return None, None
    http = record.get("http")
    if http in (403, 404):
        return NOT_VISIBLE, ("Thread or subreddit can't be viewed by logged-out visitors" if http == 403
                             else "Thread no longer exists for logged-out visitors")
    if record.get("error"):
        return VIS_UNKNOWN, f"Couldn't check: {record['error']}"
    if not record.get("found"):
        return NOT_VISIBLE, "Missing from what logged-out visitors see"
    body = record.get("body")
    if body == "deleted":
        return DELETED, "Shows as [deleted]"
    if body == "removed":
        return REMOVED, "Shows as [removed] to logged-out visitors"
    return PUBLIC, "Visible to logged-out visitors"


# --- history and alerts ---------------------------------------------------------

HISTORY_COLUMNS = ["username", "checked_at", "status", "reason", "normal_count", "anon_count"]


def load_history(conn):
    df = pd.read_sql_query(f"SELECT {', '.join(HISTORY_COLUMNS)} FROM account_health ORDER BY checked_at, id", conn)
    return df if not df.empty else pd.DataFrame(columns=HISTORY_COLUMNS)


def status_changes(history):
    """Each account's status changes, oldest first.

    history: DataFrame [username, checked_at, status, reason]. Returns DataFrame
    [username, changed_at, from_status, to_status, reason]; an account's first check counts as a change
    from None.
    """
    rows = []
    if history is None or history.empty:
        return pd.DataFrame(columns=["username", "changed_at", "from_status", "to_status", "reason"])
    for name, h in history.sort_values("checked_at").groupby(history["username"].str.lower(), sort=False):
        prev = None
        for r in h.itertuples(index=False):
            if r.status == UNKNOWN:
                continue  # a failed check isn't a change
            if r.status != prev:
                rows.append({"username": r.username, "changed_at": r.checked_at, "from_status": prev,
                             "to_status": r.status, "reason": r.reason})
                prev = r.status
    return pd.DataFrame(rows, columns=["username", "changed_at", "from_status", "to_status", "reason"])


def current_status(history):
    """{username_lower: dict(status, reason, first_detected, last_checked, username)}"""
    out = {}
    if history is None or history.empty:
        return out
    changes = status_changes(history)
    for name, h in history.sort_values("checked_at").groupby(history["username"].str.lower(), sort=False):
        known = h[h["status"] != UNKNOWN]
        last = h.iloc[-1]
        if known.empty:
            out[name] = {"username": last["username"], "status": UNKNOWN, "reason": last["reason"],
                         "first_detected": None, "last_checked": float(last["checked_at"])}
            continue
        latest = known.iloc[-1]
        c = changes[changes["username"].str.lower() == name]
        out[name] = {"username": latest["username"], "status": latest["status"], "reason": latest["reason"],
                     "first_detected": float(c.iloc[-1]["changed_at"]) if not c.empty else float(latest["checked_at"]),
                     "last_checked": float(last["checked_at"]),
                     "last_check_failed": last["status"] == UNKNOWN}
    return out


def new_problems(history):
    """Accounts that went from Visible to a problem status between the two most recent imports."""
    if history is None or history.empty:
        return []
    times = sorted(history["checked_at"].unique())
    if len(times) < 2:
        return []
    latest_t, prev_t = times[-1], times[-2]
    out = []
    for name, h in history.groupby(history["username"].str.lower()):
        now_rows = h[h["checked_at"] == latest_t]
        before = h[(h["checked_at"] <= prev_t) & (h["status"] != UNKNOWN)].sort_values("checked_at")
        if now_rows.empty or before.empty:
            continue
        now = now_rows.iloc[-1]
        if before.iloc[-1]["status"] == VISIBLE and now["status"] in PROBLEM_STATUSES:
            out.append({"username": now["username"], "status": now["status"], "reason": now["reason"],
                        "detected_at": float(latest_t)})
    return out


# --- early warning signs ----------------------------------------------------------

def at_risk(stats, now, cfg):
    """{username_lower: [reasons]} for accounts showing early warning signs in the last N days.

    stats: replies with latest stats (metrics.latest_stats), incl. score, replies_count, visibility,
    status and mentions_nubra.
    """
    s = settings(cfg)
    out = {}
    if stats is None or stats.empty:
        return out
    recent = stats[stats["created_utc"].fillna(0) >= now - s["at_risk_window_days"] * 86400]
    for name, g in recent.groupby(recent["account"].fillna("").str.lower()):
        if not name or len(g) < s["at_risk_min_replies"]:
            continue
        reasons = []
        settled = g[g["created_utc"] <= now - s["at_risk_stuck_min_age_hours"] * 3600]
        if len(settled) >= s["at_risk_min_replies"]:
            stuck = ((settled["score"].fillna(1).astype(float) <= 1)
                     & (settled["replies_count"].fillna(0).astype(float) == 0)).sum()
            if stuck / len(settled) >= s["at_risk_stuck_share"]:
                reasons.append(f"{stuck} of {len(settled)} recent replies stuck at 1 upvote with no replies")
        vis = g["visibility"] if "visibility" in g else pd.Series(index=g.index, dtype=object)
        removed = ((vis == REMOVED) | (g["status"] == "removed")).sum()
        if removed / len(g) >= s["at_risk_removed_share"]:
            reasons.append(f"{removed} of {len(g)} recent replies removed by moderators")
        nubra = int(g["mentions_nubra"].astype(bool).sum())
        if nubra >= s["at_risk_nubra_count"] or nubra / len(g) >= s["at_risk_nubra_share"]:
            reasons.append(f"{nubra} of {len(g)} recent replies mention or link Nubra")
        if reasons:
            out[name] = reasons
    return out


def hidden_breakdown(replies):
    """(count, {label: n}) of replies not visible to the public."""
    if replies is None or replies.empty or "visibility" not in replies:
        return 0, {}
    hidden = replies[replies["visibility"].isin(HIDDEN_VISIBILITY)]
    counts = hidden["visibility"].value_counts()
    return len(hidden), {VISIBILITY_LABELS[k]: int(v) for k, v in counts.items()}


def publicly_visible(replies):
    """Replies that count for reach-based metrics: everything not known to be hidden."""
    if replies is None or replies.empty or "visibility" not in replies:
        return replies
    return replies[~replies["visibility"].isin(HIDDEN_VISIBILITY)]
