"""Import a reddit_export_<date>.json file made by the "Sync from Reddit" bookmarklet.

Everything in the file is stored exactly as a direct refresh would store it, with the snapshot
dated by the export time. Importing the same file twice adds nothing new.
"""
import json
from dataclasses import dataclass, field

from . import db
from . import health as H
from .inputs import parse_comment_link
from .reddit_client import ThreadScan
from .sync import _classify, _save_mentions, _store_reply_stats, is_tracked, load_tracking, reply_from_data

FORMAT = "nubra-reddit-export"


class ExportError(Exception):
    """The file isn't a usable browser export (message is shown to the user)."""


@dataclass
class Export:
    exported_at: float
    list_version: str
    accounts: dict      # username -> {"status", "error", "items": [comment dicts]}
    links: list         # [{"raw", "post_id", "comment_id", "listing": [post listing, comment listing]}]
    threads: dict       # post_id -> [post listing, comments listing]
    comments: dict      # comment_id -> [post listing, comment listing] (replies too deep for the thread view)
    subreddits: dict    # name -> subscribers or None
    errors: list
    health_accounts: dict = field(default_factory=dict)   # username -> logged-out check record
    health_replies: dict = field(default_factory=dict)    # comment_id -> logged-out visibility record


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    snapshots: int = 0
    already_imported: int = 0
    threads: int = 0
    warnings: list = field(default_factory=list)
    exported_at: float = None
    health: dict = field(default_factory=dict)        # username -> health.AccountCheck
    new_problems: list = field(default_factory=list)  # accounts that went from Visible to a problem
    visibility_checked: int = 0


def _is_pair(value):
    return isinstance(value, list) and len(value) >= 2 and all(isinstance(v, dict) for v in value[:2])


def parse_export(raw):
    """Validate and normalise an export (bytes, str or already-parsed dict)."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8-sig", errors="replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExportError("This file isn't valid JSON. Use the reddit_export_<date>.json file the "
                              "bookmark downloaded.") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ExportError("This isn't a browser export from the Sync from Reddit bookmark.")
    if raw.get("version") != 1:
        raise ExportError(f"Unsupported export version {raw.get('version')!r}. Drag the latest bookmark "
                          f"from the dashboard and export again.")
    try:
        exported_at = float(raw["exported_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExportError("The export is incomplete (no export time). The sync may have been interrupted; "
                          "run the bookmark again.") from exc

    accounts = {}
    for name, acc in (raw.get("accounts") or {}).items():
        if isinstance(acc, dict):
            items = [i for i in acc.get("items") or [] if isinstance(i, dict) and i.get("id")]
            accounts[str(name)] = {"status": acc.get("status", "ok"), "error": acc.get("error"), "items": items}
    links = [l for l in raw.get("links") or [] if isinstance(l, dict) and l.get("comment_id") and _is_pair(l.get("listing"))]
    threads = {str(k): v for k, v in (raw.get("threads") or {}).items() if _is_pair(v)}
    comments = {str(k): v for k, v in (raw.get("comments") or {}).items() if _is_pair(v)}
    subreddits = {}
    for name, n in (raw.get("subreddits") or {}).items():
        subreddits[str(name)] = int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else None
    errors = [e for e in raw.get("errors") or [] if isinstance(e, dict)]
    health = raw.get("health") if isinstance(raw.get("health"), dict) else {}
    health_accounts = {str(k): v for k, v in (health.get("accounts") or {}).items() if isinstance(v, dict)}
    health_replies = {str(k): v for k, v in (health.get("replies") or {}).items() if isinstance(v, dict)}
    return Export(exported_at, str(raw.get("list_version") or ""), accounts, links, threads, comments,
                  subreddits, errors, health_accounts, health_replies)


def _comment_in(pair, cid):
    try:
        return ThreadScan(pair).comments.get(cid)
    except (KeyError, TypeError, AttributeError, IndexError):
        return None


def _post_of(pair):
    children = pair[0].get("data", {}).get("children", [])
    return children[0].get("data", {}) if children else {}


def import_export(conn, cfg, export, accounts_path, comments_path, current_list_version=None):
    now = export.exported_at
    result = ImportResult(exported_at=now)
    if current_list_version and export.list_version and export.list_version != current_list_version:
        result.warnings.append("This export was made with an older version of the bookmark: your accounts or "
                               "links have changed since. Drag the new 'Sync from Reddit' link to your bookmarks "
                               "bar so the next export includes them.")
    tracking, _ = load_tracking(conn, accounts_path, comments_path)
    touched = {}           # comment_id -> listing/linked comment dict (fallback data)

    def store(reply, item, **source):
        if db.upsert_reply(conn, reply, now, **source):
            result.added += 1
        elif reply["comment_id"] not in touched:
            result.updated += 1
        _classify(conn, cfg, reply["comment_id"], item.get("body"), reply["thread_title"])
        touched[reply["comment_id"]] = item

    for name, acc in export.accounts.items():
        status = acc["status"]
        if status == "ok":
            n = len(acc["items"])
            db.upsert_account(conn, name, "ok", f"{n} public comments read via browser sync.", now)
            for item in acc["items"]:
                store(reply_from_data(item), item, from_account=True)
        elif status == "not_found":
            db.upsert_account(conn, name, "not_found", "Account not found (deleted or never existed).", now)
            result.warnings.append(f"u/{name}: account not found. Check the spelling in accounts.txt.")
        else:
            db.upsert_account(conn, name, "unavailable", acc.get("error") or "Couldn't be read.", now)
            result.warnings.append(f"u/{name}: couldn't be read ({acc.get('error') or 'unknown error'}).")

    for link in export.links:
        cid = link["comment_id"]
        comment = _comment_in(link["listing"], cid)
        if comment is None:
            result.warnings.append(f"Linked comment {cid} wasn't found in its thread (it may have been removed).")
            continue
        parsed, _ = parse_comment_link(link.get("raw") or "")
        if parsed and parsed.needs_resolving:
            db.set_meta(conn, "share:" + link["raw"], cid)
        store(reply_from_data(comment, _post_of(link["listing"])), comment, from_link=True)
    conn.commit()

    # thread-level stats, exactly like a direct refresh, dated by the export time
    tracking, _ = load_tracking(conn, accounts_path, comments_path)
    all_rows = {r["comment_id"]: r for r in conn.execute("SELECT * FROM replies")}
    own_names = {u.lower() for u in tracking.usernames} | {n.lower() for n in export.accounts}
    own_names |= {r["account"].lower() for r in all_rows.values() if r["account"] and is_tracked(r, tracking)}
    own_names.discard("[deleted]")
    own_ids = set(all_rows)
    snapped = {r[0] for r in conn.execute("SELECT comment_id FROM snapshots WHERE taken_at=?", (now,))}
    scanned = {r[0] for r in conn.execute("SELECT post_id FROM thread_scans WHERE scanned_at=?", (now,))}

    by_post = {}
    for cid in touched:
        by_post.setdefault(all_rows[cid]["post_id"], []).append(cid)
    for post_id, cids in by_post.items():
        pair = export.threads.get(post_id)
        scan = ThreadScan(pair) if pair else None
        if scan:
            result.threads += 1
            if post_id not in scanned:
                _save_mentions(conn, cfg, post_id, scan, own_names, own_ids, now)
        for cid in cids:
            if cid in snapped:
                result.already_imported += 1
                continue
            row = all_rows[cid]
            comment = scan.comments.get(cid) if scan else None
            thread = scan.post if scan else {}
            if comment is None and cid in export.comments:
                comment = _comment_in(export.comments[cid], cid)
                thread = thread or _post_of(export.comments[cid])
            if comment is not None:
                _store_reply_stats(conn, cfg, row, comment, thread, scan, own_names, now)
            elif scan is not None:
                # the thread was read but the reply wasn't in it: Reddit no longer shows it
                conn.execute("UPDATE replies SET status='removed', note=? WHERE comment_id=?",
                             ("Comment not returned by Reddit", cid))
                db.add_snapshot(conn, cid, now, None, None, thread.get("score"), thread.get("num_comments"),
                                upvote_ratio=thread.get("upvote_ratio"))
            else:
                # no thread data (that request failed): keep what the comment listing told us
                item = touched[cid]
                db.add_snapshot(conn, cid, now, item.get("score"), None, None, None)
            result.snapshots += 1

    for name, subscribers in export.subreddits.items():
        db.save_subreddit(conn, name if name.startswith("r/") else f"r/{name}", subscribers, now)

    _import_health(conn, cfg, export, touched, result)

    last = db.get_meta(conn, "last_refresh")
    if last in (None, "") or float(last) < now:
        db.set_meta(conn, "last_refresh", now)
    db.set_meta(conn, "last_browser_import", now)
    conn.commit()
    if export.errors:
        result.warnings.append(f"{len(export.errors)} item(s) couldn't be fetched in the browser "
                               f"(e.g. {export.errors[0].get('item')}: {export.errors[0].get('error')}).")
    if not export.health_accounts:
        result.warnings.append("This export has no account health checks: it was made with an older bookmark. "
                               "Drag the new 'Sync from Reddit' button to your bookmarks bar.")
    return result


def _import_health(conn, cfg, export, touched, result):
    """Store the logged-out account checks (history) and each reply's public visibility."""
    now = export.exported_at
    statuses = {}
    for name, record in export.health_accounts.items():
        check = H.classify_account(record, cfg)
        db.add_account_health(conn, name, now, check.status, check.reason, check.normal_count, check.anon_count)
        statuses[name.lower()] = check.status
        result.health[name] = check
    if not statuses:
        return
    # an account whose check failed this time (or isn't in this export) keeps its latest known status
    for r in conn.execute("SELECT username, status FROM account_health WHERE id IN "
                          "(SELECT MAX(id) FROM account_health WHERE status != ? GROUP BY lower(username))",
                          (H.UNKNOWN,)):
        if statuses.get(r["username"].lower()) in (None, H.UNKNOWN):
            statuses[r["username"].lower()] = r["status"]
    rows = {r["comment_id"]: r for r in conn.execute("SELECT comment_id, account, status FROM replies")}
    for cid in set(touched) | set(export.health_replies):
        row = rows.get(cid)
        if row is None:
            continue
        visibility, reason = H.classify_reply(export.health_replies.get(cid),
                                              statuses.get((row["account"] or "").lower()), row["status"])
        if visibility:
            db.set_visibility(conn, cid, visibility, reason, now)
            result.visibility_checked += 1
    conn.commit()
    history = H.load_history(conn)
    result.new_problems = [p for p in H.new_problems(history) if p["detected_at"] == now]
    for p in result.new_problems:
        result.warnings.insert(0, f"u/{p['username']} changed from Visible to "
                                  f"{H.ACCOUNT_LABELS[p['status']]}: {p['reason']}.")
