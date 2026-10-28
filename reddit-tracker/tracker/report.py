"""Weekly reports (Markdown, Slack-friendly): a detailed version and a manager version."""
from datetime import datetime

from . import health as H
from . import metrics as m

WEEK = m.WEEK
QUALITY = (("avg_upvotes", "Avg upvotes per reply", False),
           ("top_comment_rate", "Top-comment rate (top-level replies)", True),
           ("op_engagement_rate", "OP engagement rate", True),
           ("positive_reply_rate", "Positive reply rate", True),
           ("nubra_share", "Nubra share of broker mentions", True))


def week_stats(replies, snaps, start, end, responses=None, scans=None):
    """Numbers for one period [start, end)."""
    posted = replies[(replies["created_utc"] >= start) & (replies["created_utc"] < end)]
    score_end = m.values_at(replies, snaps, end, "score")
    score_start = m.values_at(replies, snaps, start, "score").reindex(score_end.index).fillna(0)
    gains = (score_end - score_start).sort_values(ascending=False, kind="stable")
    received = (m.values_at(replies, snaps, end, "replies_count").sum()
                - m.values_at(replies, snaps, start, "replies_count").sum())

    indexed = replies.set_index("comment_id")
    best_replies = []
    for cid, gain in gains.items():
        if gain <= 0 or len(best_replies) == 3:
            break
        row = indexed.loc[cid]
        best_replies.append({"comment_id": cid, "gain": float(gain), "subreddit": row["subreddit"],
                             "thread_title": row["thread_title"], "permalink": row["permalink"],
                             "account": row["account"]})

    def best_groups(column, n=3):
        # among publicly visible replies posted in the period: highest average upvotes as of period end
        public = H.publicly_visible(posted)
        if public.empty:
            return []
        scores = score_end.reindex(public["comment_id"]).fillna(0).values
        g = public.assign(_s=scores).groupby(column)["_s"].agg(["mean", "count"])
        g = g.sort_values(["mean", "count"], ascending=False).head(n)
        return [{"name": name, "avg": float(r["mean"]), "count": int(r["count"])} for name, r in g.iterrows()]

    # quality of the replies posted in this period, as of the period end
    quality = m.kpis_at(posted, snaps, end, responses, scans) if not posted.empty else {}
    mentions = posted[posted["mentions_nubra"]]
    subs, topics = best_groups("subreddit"), best_groups("topic", 1)
    return {
        "replies_posted": len(posted),
        "upvotes_gained": float(gains.sum()),
        "replies_received": float(received),
        "best_reply": best_replies[0] if best_replies else None,
        "best_replies": best_replies,
        "best_subreddit": subs[0] if subs else None,
        "best_subreddits": subs,
        "best_topic": topics[0] if topics else None,
        "nubra_mentions": len(mentions),
        "disclosure_rate": (100.0 * mentions["disclosed"].sum() / len(mentions)) if len(mentions) else None,
        **{key: quality.get(key) for key, _, _ in QUALITY},
    }


def _num(v):
    return "–" if v is None else f"{v:,.0f}"


def _change(cur, prev, pct=False, decimals=0):
    if cur is None or prev is None:
        return ""
    d = cur - prev
    unit = " pts" if pct else ""
    if abs(d) < 0.5 * 10 ** -decimals:
        return " (no change vs last week)"
    return f" ({'+' if d > 0 else ''}{d:,.{decimals}f}{unit} vs last week)"


def _quality_lines(cur, prev):
    lines = []
    for key, label, pct in QUALITY:
        v = cur.get(key)
        if v is None:
            lines.append(f"• {label}: –")
            continue
        text = f"{v:.0f}%" if pct else f"{v:.1f}"
        lines.append(f"• {label}: {text}{_change(v, prev.get(key), pct=pct, decimals=0 if pct else 1)}")
    return lines


def _title(week_start_ts, now, offset_hours, suffix=""):
    end = week_start_ts + WEEK
    title = (f"Reddit reply impact{suffix} – week of {m.fmt_date(week_start_ts, offset_hours, '%d %b')} to "
             f"{m.fmt_date(end - 1, offset_hours, '%d %b %Y')}")
    if end > now:
        title += f" (so far, as of {m.fmt_date(now, offset_hours, '%d %b %H:%M')} IST)"
    return title


def _periods(replies, snaps, week_start_ts, now, responses, scans):
    end = min(week_start_ts + WEEK, now)
    cur = week_stats(replies, snaps, week_start_ts, end, responses, scans)
    prev = week_stats(replies, snaps, week_start_ts - WEEK, week_start_ts, responses, scans)
    return cur, prev


FOOTNOTE = "_Upvotes are net (up minus down). Reddit doesn't share impressions or clicks._"


def health_lines(replies, snaps, history, week_start_ts, now, offset_hours, cfg=None):
    """Factual account-health section: status per account, changes this week, hidden replies."""
    end = min(week_start_ts + WEEK, now)
    lines = ["*Account health*"]
    if history is None or history.empty:
        return lines + ["• No account health checks yet (they come from the Sync from Reddit bookmark)."]
    known = history[history["checked_at"] < end + 1]
    current = H.current_status(known)
    risk = H.at_risk(m.latest_stats(replies, snaps), end, cfg or {}) if not replies.empty else {}
    for key in sorted(current, key=str.lower):
        c = current[key]
        text = f"• u/{c['username']}: {H.ACCOUNT_LABELS[c['status']]}"
        if c["status"] in H.PROBLEM_STATUSES and c["first_detected"]:
            text += f" (since {m.fmt_date(c['first_detected'], offset_hours, '%d %b')})"
        if c["status"] == H.VISIBLE and key in risk:
            text += f" – at risk: {'; '.join(risk[key])}"
        lines.append(text)
    changes = H.status_changes(known)
    week = changes[(changes["changed_at"] >= week_start_ts) & (changes["changed_at"] < end + 1)
                   & changes["from_status"].notna()]
    if week.empty:
        lines.append("• Status changes this week: none")
    else:
        for r in week.itertuples(index=False):
            lines.append(f"• Changed this week: u/{r.username} {H.ACCOUNT_LABELS[r.from_status]} → "
                         f"{H.ACCOUNT_LABELS[r.to_status]} ({m.fmt_date(r.changed_at, offset_hours, '%d %b')})")
    n, by_reason = H.hidden_breakdown(replies)
    detail = f" ({', '.join(f'{v} {k.lower()}' for k, v in by_reason.items())})" if n else ""
    lines.append(f"• Hidden replies: {n} not visible to the public{detail}")
    return lines


def build_report(replies, snaps, week_start_ts, now, offset_hours, responses=None, scans=None, history=None,
                 cfg=None):
    """Detailed version (for me): volumes with week-on-week changes, quality metrics and account health."""
    cur, prev = _periods(replies, snaps, week_start_ts, now, responses, scans)
    lines = [f"*{_title(week_start_ts, now, offset_hours)}*", ""]
    lines.append(f"• Replies posted: {cur['replies_posted']}{_change(cur['replies_posted'], prev['replies_posted'])}")
    lines.append(f"• Upvotes gained: {_num(cur['upvotes_gained'])}{_change(cur['upvotes_gained'], prev['upvotes_gained'])}")
    lines.append(f"• Replies received: {_num(cur['replies_received'])}"
                 f"{_change(cur['replies_received'], prev['replies_received'])}")
    b = cur["best_reply"]
    if b:
        lines.append(f"• Best reply: +{b['gain']:,.0f} upvotes in {b['subreddit']} – "
                     f"\"{b['thread_title']}\" by u/{b['account']}: {b['permalink']}")
    else:
        lines.append("• Best reply: –")
    for key, label in (("best_subreddit", "Best subreddit"), ("best_topic", "Best topic")):
        g = cur[key]
        lines.append(f"• {label}: {g['name']} ({g['avg']:.1f} avg upvotes across {g['count']} "
                     f"repl{'y' if g['count'] == 1 else 'ies'})" if g else f"• {label}: –")
    if cur["disclosure_rate"] is None:
        lines.append("• Disclosure rate: – (no replies mentioned Nubra)")
    else:
        lines.append(f"• Disclosure rate: {cur['disclosure_rate']:.0f}% of {cur['nubra_mentions']} "
                     f"Nubra-mentioning repl{'y' if cur['nubra_mentions'] == 1 else 'ies'}"
                     f"{_change(cur['disclosure_rate'], prev['disclosure_rate'], pct=True)}")
    lines += ["", "*Quality of this week's replies*"] + _quality_lines(cur, prev)
    lines += [""] + health_lines(replies, snaps, history, week_start_ts, now, offset_hours, cfg)
    lines += ["", FOOTNOTE]
    return "\n".join(lines) + "\n"


def build_manager_report(replies, snaps, week_start_ts, now, offset_hours, responses=None, scans=None,
                         history=None, cfg=None):
    """Manager version: totals, averages, best replies and subreddits, account health.

    Changes vs last week are shown only on quality metrics."""
    cur, prev = _periods(replies, snaps, week_start_ts, now, responses, scans)
    lines = [f"*{_title(week_start_ts, now, offset_hours, ' (summary)')}*", "", "*Totals*"]
    lines.append(f"• Replies: {cur['replies_posted']}")
    lines.append(f"• Upvotes gained: {_num(cur['upvotes_gained'])}")
    lines.append(f"• Replies received: {_num(cur['replies_received'])}")
    lines += ["", "*Quality*"] + _quality_lines(cur, prev)
    lines += ["", "*Best replies*"]
    if cur["best_replies"]:
        for i, b in enumerate(cur["best_replies"], start=1):
            lines.append(f"{i}. +{b['gain']:,.0f} upvotes in {b['subreddit']} – \"{b['thread_title']}\": "
                         f"{b['permalink']}")
    else:
        lines.append("–")
    lines += ["", "*Best subreddits*"]
    if cur["best_subreddits"]:
        for i, g in enumerate(cur["best_subreddits"], start=1):
            lines.append(f"{i}. {g['name']} – {g['avg']:.1f} avg upvotes")
    else:
        lines.append("–")
    lines += [""] + health_lines(replies, snaps, history, week_start_ts, now, offset_hours, cfg)
    lines += ["", FOOTNOTE]
    return "\n".join(lines) + "\n"


def report_path(reports_dir, week_start_ts, offset_hours, partial=False, manager=False):
    day = datetime.fromtimestamp(week_start_ts, m.tz(offset_hours)).strftime("%Y-%m-%d")
    return reports_dir / f"weekly-{day}{'-manager' if manager else ''}{'-so-far' if partial else ''}.md"


def save_report(reports_dir, week_start_ts, offset_hours, text, partial=False, manager=False):
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = report_path(reports_dir, week_start_ts, offset_hours, partial, manager)
    path.write_text(text, encoding="utf-8")
    return path


def ensure_last_week_report(replies, snaps, reports_dir, now, offset_hours, responses=None, scans=None,
                            history=None, cfg=None):
    """Write both versions for the last completed week if missing. Returns the new paths."""
    if replies.empty:
        return []
    last_week = m.week_start(now, offset_hours) - WEEK
    written = []
    for manager, build in ((False, build_report), (True, build_manager_report)):
        path = report_path(reports_dir, last_week, offset_hours, manager=manager)
        if path.exists():
            continue
        text = build(replies, snaps, last_week, now, offset_hours, responses, scans, history, cfg)
        written.append(save_report(reports_dir, last_week, offset_hours, text, manager=manager))
    return written
