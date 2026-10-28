"""Sync (discover replies) and refresh (snapshot stats) logic."""
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field

from . import classify as clf
from . import db
from .inputs import parse_accounts, parse_comment_link, parse_comments, read_text
from .reddit_client import (BASE, Forbidden, NotFound, RedditError, ThreadScan, count_direct_replies,
                            detect_status, direct_replies, is_top_level)

TEXT_LIMIT = 300


@dataclass
class RunResult:
    messages: list = field(default_factory=list)   # plain-English successes / info
    warnings: list = field(default_factory=list)   # things the user should look at
    new_replies: int = 0
    refreshed: int = 0
    aborted: bool = False


@dataclass
class Tracking:
    usernames: list
    comment_ids: set
    unresolved_links: list
    warnings: list


def _noop(*_args, **_kwargs):
    pass


def load_tracking(conn, accounts_path, comments_path):
    """Re-read both input files. Share links resolved in earlier syncs are mapped via meta."""
    acc = parse_accounts(read_text(accounts_path))
    com = parse_comments(read_text(comments_path))
    ids, unresolved = set(), []
    for link in com.links:
        if link.needs_resolving:
            cid = db.get_meta(conn, "share:" + link.raw)
            if cid:
                ids.add(cid)
            else:
                unresolved.append(link)
        else:
            ids.add(link.comment_id)
    return Tracking(acc.usernames, ids, unresolved, acc.warnings + com.warnings), com.links


def is_tracked(row, tracking):
    names = {u.lower() for u in tracking.usernames}
    account = row["account"] if isinstance(row["account"], str) else ""
    return (row["comment_id"] in tracking.comment_ids
            or (bool(row["from_account"]) and account.lower() in names))


def reply_from_data(c, thread=None):
    thread = thread or {}
    link_id = c.get("link_id") or ""
    post_id = link_id[3:] if link_id.startswith("t3_") else thread.get("id", "")
    thread_link = c.get("link_permalink") or (BASE + thread["permalink"] if thread.get("permalink") else "")
    sub = c.get("subreddit") or thread.get("subreddit") or ""
    status = detect_status(c)
    body = c.get("body") or ""
    parent = c.get("parent_id") or ""
    return {
        "comment_id": c["id"],
        "post_id": post_id,
        "account": c.get("author"),
        "permalink": BASE + c["permalink"] if c.get("permalink") else "",
        "subreddit": f"r/{sub}" if sub else "",
        "thread_title": c.get("link_title") or thread.get("title") or "",
        "thread_link": thread_link,
        "created_utc": float(c.get("created_utc") or 0) or None,
        "text": body[:TEXT_LIMIT] if status == "live" else None,
        "status": status,
        "note": None,
        "parent_id": parent or None,
        "depth": (0 if is_top_level(c) else 1) if parent else None,
        "thread_created_utc": float(thread["created_utc"]) if thread.get("created_utc") else None,
        "thread_author": thread.get("author"),
    }


def _classify(conn, cfg, comment_id, body, thread_title):
    if not body or body in ("[removed]", "[deleted]"):
        return
    result = clf.classify(body, cfg, thread_title)
    db.save_classification(conn, comment_id, result["mentions_nubra"], result["disclosed"], result["topic"])


def _sync_account(conn, client, cfg, username, now, result):
    synced_before = db.get_meta(conn, "synced:" + username.lower()) is not None
    known = {r["comment_id"] for r in conn.execute("SELECT comment_id FROM replies")}

    def stop_when(items):
        # after the first full backfill, stop once a whole page is already known
        return synced_before and all(i["id"] in known for i in items)

    new_ids, seen = [], 0
    try:
        for item in client.user_comments(username, cfg.get("max_pages_per_user", 10), stop_when):
            seen += 1
            reply = reply_from_data(item)
            if db.upsert_reply(conn, reply, now, from_account=True):
                new_ids.append(reply["comment_id"])
            _classify(conn, cfg, reply["comment_id"], item.get("body"), reply["thread_title"])
    except NotFound:
        db.upsert_account(conn, username, "not_found", "Account not found (deleted or never existed).", now)
        result.warnings.append(f"u/{username}: account not found. Check the spelling in accounts.txt, "
                               f"or the account may have been deleted.")
        return new_ids
    except Forbidden:
        message = "Account is private or unavailable."
        try:
            if client.user_about(username).get("is_suspended"):
                message = "Account is suspended."
        except RedditError:
            pass
        db.upsert_account(conn, username, "unavailable", message, now)
        result.warnings.append(f"u/{username}: {message} Its comments can't be read.")
        return new_ids
    conn.commit()
    db.set_meta(conn, "synced:" + username.lower(), now)
    msg = f"{seen} public comments checked, {len(new_ids)} new." if seen else "No public comments yet."
    db.upsert_account(conn, username, "ok", msg, now)
    result.messages.append(f"u/{username}: {msg}")
    return new_ids


def _sync_link(conn, client, cfg, link, now, result):
    """Store a linked comment. Returns its id if it's new (its stats come from refresh)."""
    if link.needs_resolving:
        final_url = client.resolve_share_link(link.raw)
        resolved, _ = parse_comment_link(final_url)
        if not resolved or resolved.needs_resolving:
            result.warnings.append(f"Couldn't work out which comment this share link points to: {link.raw}")
            return None
        db.set_meta(conn, "share:" + link.raw, resolved.comment_id)
        link = resolved
    try:
        thread, comment = client.comment_with_thread(link.post_id, link.comment_id)
    except NotFound:
        result.warnings.append(f"Linked comment {link.comment_id}: its thread no longer exists on Reddit.")
        conn.execute("UPDATE replies SET status='removed', note=? WHERE comment_id=?",
                     ("Thread no longer exists", link.comment_id))
        return None
    if comment is None:
        result.warnings.append(f"Linked comment {link.comment_id} wasn't found in its thread "
                               f"(it may have been removed).")
        conn.execute("UPDATE replies SET status='removed', note=? WHERE comment_id=?",
                     ("Comment not returned by Reddit", link.comment_id))
        return None
    reply = reply_from_data(comment, thread)
    is_new = db.upsert_reply(conn, reply, now, from_link=True)
    _classify(conn, cfg, reply["comment_id"], comment.get("body"), reply["thread_title"])
    if reply["status"] != "live":
        result.warnings.append(f"Linked comment in {reply['subreddit']} is {reply['status']}.")
    return reply["comment_id"] if is_new else None


def sync(conn, client, cfg, accounts_path, comments_path, progress=_noop, now=None):
    """Re-read inputs, discover replies, and snapshot any new ones."""
    now = now or time.time()
    result = RunResult()
    tracking, links = load_tracking(conn, accounts_path, comments_path)
    result.warnings.extend(tracking.warnings)
    if not tracking.usernames and not links:
        result.warnings.append("accounts.txt and comments.txt are empty. Add at least one username.")
        return result

    steps = len(tracking.usernames) + len(links)
    step = 0
    new_ids = []
    try:
        for username in tracking.usernames:
            progress(0.6 * step / max(steps, 1), f"Reading comments from u/{username}...")
            new_ids += _sync_account(conn, client, cfg, username, now, result)
            step += 1
        for link in links:
            progress(0.6 * step / max(steps, 1), "Checking linked comments...")
            cid = _sync_link(conn, client, cfg, link, now, result)
            if cid and cid not in new_ids:
                new_ids.append(cid)
            step += 1
        conn.commit()
    except RedditError as exc:
        conn.commit()
        result.warnings.append(f"Sync stopped early: {exc}")
        result.aborted = True
    result.new_replies = len(new_ids)

    # give brand-new replies a first full snapshot (thread stats, rank, replies)
    if new_ids and not result.aborted:
        sub = refresh(conn, client, cfg, accounts_path, comments_path,
                      lambda f, m: progress(0.6 + 0.4 * f, m), only_ids=new_ids, now=now,
                      mark_complete=False)
        result.warnings.extend(sub.warnings)
        result.aborted = sub.aborted
    plural = "y" if result.new_replies == 1 else "ies"
    if result.aborted:
        result.messages.insert(0, f"Sync didn't finish. {result.new_replies} new repl{plural} saved so far; "
                                  f"the next sync will carry on.")
    else:
        db.set_meta(conn, "last_sync", now)
        result.messages.insert(0, f"Sync finished: {result.new_replies} new repl{plural} found.")
    progress(1.0, "Sync stopped." if result.aborted else "Sync finished.")
    return result


# --- refresh ---------------------------------------------------------------

def _save_mentions(conn, cfg, post_id, scan, own_names, own_ids, now):
    """Count brand mentions by other users: each comment (and the post) once per brand."""
    brands = clf.all_brands(cfg)
    counts, scanned = Counter(), 0
    post = scan.post
    texts = []
    if post and (post.get("author") or "").lower() not in own_names:
        texts.append(f"{post.get('title', '')}\n{post.get('selftext', '')}")
    for cid, c in scan.comments.items():
        if cid in own_ids or (c.get("author") or "").lower() in own_names:
            continue
        texts.append(c.get("body") or "")
    for text in texts:
        scanned += 1
        counts.update(clf.brands_mentioned(text, brands))
    db.add_thread_scan(conn, post_id, now, scanned, dict(counts))


def _store_reply_stats(conn, cfg, row, comment, thread, scan, own_names, now):
    cid = row["comment_id"]
    reply = reply_from_data(comment, thread)
    kids = direct_replies(comment)
    op = thread.get("author")
    op_engaged = bool(op and op != "[deleted]" and any(k.get("author") == op for k in kids))
    reply["op_engaged"] = op_engaged
    db.upsert_reply(conn, reply, now)
    _classify(conn, cfg, cid, comment.get("body"), reply["thread_title"] or row["thread_title"])
    for k in kids:
        if (k.get("author") or "").lower() in own_names:
            continue  # my own accounts replying don't count as audience reactions
        body = k.get("body") or ""
        db.save_response(conn, k["id"], cid, k.get("author"), body[:TEXT_LIMIT],
                         float(k.get("created_utc") or 0) or None, clf.sentiment(body, cfg))
    top = is_top_level(comment)
    db.add_snapshot(
        conn, cid, now, comment.get("score"), count_direct_replies(comment),
        thread.get("score"), thread.get("num_comments"),
        thread_rank=scan.rank.get(cid) if (scan and top) else None,
        top_level_count=scan.top_level_count if (scan and top) else None,
        upvote_ratio=thread.get("upvote_ratio"), op_engaged=op_engaged)


def _refresh_subreddits(conn, client, cfg, names, now, result):
    max_age = cfg.get("subreddit_info_max_age_hours", 24) * 3600
    for name in sorted(n for n in names if n):
        fetched = db.subreddit_fetched_at(conn, name)
        if fetched and now - fetched < max_age:
            continue
        try:
            about = client.subreddit_about(name[2:] if name.startswith("r/") else name)
            subs = about.get("subscribers")
            db.save_subreddit(conn, name, int(subs) if subs is not None else None, now)
        except (NotFound, Forbidden):
            db.save_subreddit(conn, name, None, now)
        except RedditError as exc:
            result.warnings.append(f"Couldn't read subreddit sizes: {exc}")
            return
    conn.commit()


def refresh(conn, client, cfg, accounts_path, comments_path, progress=_noop, only_ids=None,
            now=None, mark_complete=True):
    """Snapshot every currently tracked reply (deleted ones are skipped).

    Each thread is fetched once (top-sorted), which gives rank, replies, OP engagement and other
    users' brand mentions for every tracked reply in it. Replies too deep to appear in that fetch
    fall back to their own permalink.
    """
    now = now or time.time()
    result = RunResult()
    tracking, _ = load_tracking(conn, accounts_path, comments_path)
    own_names = {u.lower() for u in tracking.usernames}
    rows = conn.execute("SELECT * FROM replies ORDER BY created_utc DESC").fetchall()
    own_ids = {r["comment_id"] for r in rows}
    # "my accounts" = accounts.txt plus authors of any tracked reply (e.g. linked comments)
    own_names |= {r["account"].lower() for r in rows if r["account"] and is_tracked(r, tracking)}
    own_names.discard("[deleted]")
    if only_ids is not None:
        wanted = set(only_ids)
        rows = [r for r in rows if r["comment_id"] in wanted]
    else:
        rows = [r for r in rows if is_tracked(r, tracking)]
    rows = [r for r in rows if r["status"] != "deleted"]
    # replies already snapshotted in this same run (e.g. by sync) aren't fetched twice
    done = {r["comment_id"] for r in conn.execute("SELECT comment_id FROM snapshots WHERE taken_at=?", (now,))}
    rows = [r for r in rows if r["comment_id"] not in done]

    threads = OrderedDict()
    for r in rows:
        threads.setdefault(r["post_id"], []).append(r)

    for i, (post_id, group) in enumerate(threads.items()):
        progress(i / max(len(threads), 1), f"Updating thread {i + 1} of {len(threads)}...")
        try:
            scan = ThreadScan(client.thread(post_id))
        except NotFound:
            for row in group:
                conn.execute("UPDATE replies SET status='removed', note=? WHERE comment_id=?",
                             ("Thread no longer exists", row["comment_id"]))
            continue
        except Forbidden:
            for row in group:
                conn.execute("UPDATE replies SET note=? WHERE comment_id=?",
                             ("Subreddit is private or banned; stats can't be read", row["comment_id"]))
            continue
        except RedditError as exc:
            result.warnings.append(f"Refresh stopped early after {result.refreshed} replies: {exc}")
            result.aborted = True
            break
        _save_mentions(conn, cfg, post_id, scan, own_names, own_ids, now)

        for row in group:
            cid = row["comment_id"]
            comment, thread = scan.comments.get(cid), scan.post
            if comment is None:
                try:
                    thread2, comment = client.comment_with_thread(post_id, cid)
                    thread = thread or thread2
                except (NotFound, Forbidden):
                    comment = None
                except RedditError as exc:
                    result.warnings.append(f"Refresh stopped early after {result.refreshed} replies: {exc}")
                    result.aborted = True
                    break
            if comment is None:
                conn.execute("UPDATE replies SET status='removed', note=? WHERE comment_id=?",
                             ("Comment not returned by Reddit", cid))
                db.add_snapshot(conn, cid, now, None, None, thread.get("score"), thread.get("num_comments"),
                                upvote_ratio=thread.get("upvote_ratio"))
            else:
                _store_reply_stats(conn, cfg, row, comment, thread, scan, own_names, now)
            result.refreshed += 1
        conn.commit()
        if result.aborted:
            break

    if not result.aborted:
        _refresh_subreddits(conn, client, cfg, {r["subreddit"] for r in rows}, now, result)
    conn.commit()
    # "last refreshed" only moves when there was something to refresh
    if mark_complete and not result.aborted and threads:
        db.set_meta(conn, "last_refresh", now)
    result.messages.append(f"Stats updated for {result.refreshed} repl"
                           f"{'y' if result.refreshed == 1 else 'ies'}"
                           f"{' before stopping' if result.aborted else ''}.")
    progress(1.0, "Stats updated.")
    return result


def refresh_due(last_refresh, now, hours):
    if last_refresh in (None, ""):
        return True
    return now - float(last_refresh) > hours * 3600


def sync_and_refresh(conn, client, cfg, accounts_path, comments_path, progress=_noop, now=None):
    now = now or time.time()
    first = sync(conn, client, cfg, accounts_path, comments_path,
                 lambda f, m: progress(f * 0.5, m), now=now)
    if first.aborted:
        return first
    second = refresh(conn, client, cfg, accounts_path, comments_path,
                     lambda f, m: progress(0.5 + f * 0.5, m), now=now)
    first.messages += second.messages
    first.warnings += second.warnings
    first.refreshed = second.refreshed
    first.aborted = second.aborted
    return first
