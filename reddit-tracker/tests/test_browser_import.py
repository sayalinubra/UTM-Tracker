import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from tests.fake_reddit import comment, listing, replies_tree, thread_payload, tree
from tracker import bookmarklet as BM
from tracker import browser_import as BI
from tracker import db
from tracker.config import load_config
from tracker.inputs import parse_comments

CFG = load_config()
HERE = Path(__file__).resolve().parent
T0 = 1_761_600_000.0  # export time used in the tests
DAY = 86400


def make_export(accounts=None, threads=None, links=(), comments=None, subreddits=None, exported_at=T0,
                list_version="", errors=()):
    return {
        "format": "nubra-reddit-export", "version": 1, "exported_at": exported_at, "started_at": exported_at - 60,
        "list_version": list_version, "origin": "https://www.reddit.com",
        "accounts": accounts or {}, "links": list(links), "threads": threads or {}, "comments": comments or {},
        "subreddits": subreddits or {}, "errors": list(errors),
    }


def ok_account(*items):
    return {"status": "ok", "items": list(items)}


class ParseTests(unittest.TestCase):
    def test_rejects_bad_files(self):
        for raw, phrase in ((b"not json", "valid JSON"),
                            (json.dumps({"format": "other"}), "isn't a browser export"),
                            (json.dumps({"format": "nubra-reddit-export", "version": 9}), "Unsupported"),
                            (json.dumps({"format": "nubra-reddit-export", "version": 1}), "incomplete")):
            with self.assertRaises(BI.ExportError) as ctx:
                BI.parse_export(raw)
            self.assertIn(phrase, str(ctx.exception))

    def test_normalises_and_drops_malformed_parts(self):
        raw = make_export(
            accounts={"alice": {"status": "ok", "items": [comment("c1", "p1", author="alice"), {"no": "id"}]},
                      "bad": "not a dict"},
            threads={"p1": thread_payload("p1", None), "p2": "garbage"},
            subreddits={"IndianStockMarket": 1200, "x": "n/a", "y": True})
        e = BI.parse_export(json.dumps(raw).encode("utf-8"))
        self.assertEqual(e.exported_at, T0)
        self.assertEqual(len(e.accounts["alice"]["items"]), 1)
        self.assertNotIn("bad", e.accounts)
        self.assertEqual(list(e.threads), ["p1"])
        self.assertEqual(e.subreddits, {"IndianStockMarket": 1200, "x": None, "y": None})


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.accounts, self.comments = d / "accounts.txt", d / "comments.txt"
        self.accounts.write_text("alice\nghost\n", encoding="utf-8")
        self.comments.write_text("", encoding="utf-8")
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_import(self, raw, version=None):
        return BI.import_export(self.conn, CFG, BI.parse_export(raw), self.accounts, self.comments, version)

    def basic_export(self, score=5, exported_at=T0, **kw):
        kids = tree(comment("k1", "p1", author="op_user", body="thanks, helpful", parent="t1_c1"))
        mine = comment("c1", "p1", author="alice", body="Nubra has greeks (I work at Nubra)", score=score,
                       created=T0 - 3 * DAY, replies=kids)
        other = comment("o1", "p1", author="bob", body="zerodha kite works", score=score + 10, created=T0 - 3 * DAY)
        return make_export(
            accounts={"alice": ok_account(comment("c1", "p1", author="alice", body="Nubra has greeks (I work at Nubra)",
                                                  score=score, created=T0 - 3 * DAY)),
                      "ghost": {"status": "not_found", "error": "HTTP 404", "items": []}},
            threads={"p1": thread_payload("p1", [other, mine], author="op_user", created=T0 - 3 * DAY - 3600,
                                          upvote_ratio=0.93, num_comments=55, title="Which broker for greeks?")},
            subreddits={"IndianStockMarket": 1_300_000}, exported_at=exported_at, **kw)

    def test_import_stores_replies_and_a_snapshot_dated_by_the_export(self):
        res = self.run_import(self.basic_export())
        self.assertEqual((res.added, res.updated, res.snapshots, res.threads), (1, 0, 1, 1))
        s = self.conn.execute("SELECT * FROM snapshots").fetchone()
        self.assertEqual(s["taken_at"], T0)
        self.assertEqual((s["score"], s["replies_count"], s["thread_comments"], s["thread_rank"], s["upvote_ratio"],
                          s["op_engaged"]), (5, 1, 55, 2, 0.93, 1))
        r = self.conn.execute("SELECT * FROM replies").fetchone()
        self.assertEqual((r["account"], r["depth"], r["thread_author"]), ("alice", 0, "op_user"))
        self.assertEqual(self.conn.execute("SELECT sentiment FROM responses").fetchone()[0], "positive")
        scan = json.loads(self.conn.execute("SELECT mentions FROM thread_scans").fetchone()[0])
        self.assertEqual(scan, {"Zerodha": 1})
        self.assertEqual(self.conn.execute("SELECT subscribers FROM subreddits").fetchone()[0], 1_300_000)
        statuses = dict(self.conn.execute("SELECT username, status FROM accounts").fetchall())
        self.assertEqual(statuses, {"alice": "ok", "ghost": "not_found"})
        self.assertTrue(any("u/ghost" in w for w in res.warnings))
        self.assertEqual(float(db.get_meta(self.conn, "last_refresh")), T0)
        self.assertEqual(float(db.get_meta(self.conn, "last_browser_import")), T0)
        c = self.conn.execute("SELECT * FROM classifications").fetchone()
        self.assertEqual((c["mentions_nubra"], c["disclosed"]), (1, 1))

    def test_reimporting_the_same_file_adds_nothing(self):
        raw = self.basic_export()
        self.run_import(raw)
        res = self.run_import(raw)
        self.assertEqual((res.added, res.updated, res.snapshots, res.already_imported), (0, 1, 0, 1))
        for table in ("snapshots", "thread_scans", "responses", "replies"):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1, table)

    def test_later_export_adds_a_new_snapshot_and_older_one_fills_history(self):
        self.run_import(self.basic_export(score=5))
        self.run_import(self.basic_export(score=9, exported_at=T0 + DAY))
        self.run_import(self.basic_export(score=2, exported_at=T0 - DAY))
        rows = self.conn.execute("SELECT taken_at, score FROM snapshots ORDER BY taken_at").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(T0 - DAY, 2), (T0, 5), (T0 + DAY, 9)])
        # importing an older file never moves "last refreshed" backwards
        self.assertEqual(float(db.get_meta(self.conn, "last_refresh")), T0 + DAY)

    def test_missing_thread_keeps_listing_score_with_unknown_replies(self):
        raw = make_export(accounts={"alice": ok_account(comment("c1", "p1", author="alice", score=7))},
                          errors=[{"item": "thread p1", "error": "HTTP 500"}])
        res = self.run_import(raw)
        s = self.conn.execute("SELECT * FROM snapshots").fetchone()
        self.assertEqual((s["score"], s["replies_count"], s["thread_rank"]), (7, None, None))
        self.assertTrue(any("couldn't be fetched" in w for w in res.warnings))

    def test_deep_reply_comes_from_the_comments_section(self):
        nested = comment("c2", "p1", author="alice", score=4, parent="t1_o1", replies=replies_tree(2))
        raw = make_export(accounts={"alice": ok_account(comment("c2", "p1", author="alice", parent="t1_o1"))},
                          threads={"p1": thread_payload("p1", [comment("o1", "p1", author="bob", score=9)])},
                          comments={"c2": thread_payload("p1", nested)})
        self.run_import(raw)
        s = self.conn.execute("SELECT * FROM snapshots").fetchone()
        self.assertEqual((s["score"], s["replies_count"], s["thread_rank"]), (4, 2, None))
        self.assertEqual(self.conn.execute("SELECT depth FROM replies").fetchone()[0], 1)

    def test_reply_missing_from_a_fetched_thread_is_marked_removed(self):
        raw = make_export(accounts={"alice": ok_account(comment("c1", "p1", author="alice", body="saved text"))},
                          threads={"p1": thread_payload("p1", [comment("o1", "p1", author="bob")])})
        self.run_import(raw)
        r = self.conn.execute("SELECT status, text FROM replies").fetchone()
        self.assertEqual(tuple(r), ("removed", "saved text"))

    def test_linked_comment_and_share_link(self):
        self.accounts.write_text("", encoding="utf-8")
        share = "https://www.reddit.com/r/x/s/AbC123"
        self.comments.write_text(share + "\n", encoding="utf-8")
        theirs = comment("c9", "p9", author="someone_else", score=3)
        raw = make_export(links=[{"raw": share, "post_id": "p9", "comment_id": "c9",
                                  "listing": thread_payload("p9", theirs)}],
                          threads={"p9": thread_payload("p9", [theirs])})
        res = self.run_import(raw)
        self.assertEqual(res.added, 1)
        self.assertEqual(db.get_meta(self.conn, "share:" + share), "c9")
        r = self.conn.execute("SELECT account, from_link FROM replies").fetchone()
        self.assertEqual(tuple(r), ("someone_else", 1))

    def test_dedupes_with_existing_direct_sync_data(self):
        db.upsert_reply(self.conn, {"comment_id": "c1", "post_id": "p1", "account": "alice", "status": "live",
                                    "created_utc": T0 - 3 * DAY, "text": "old"}, T0 - DAY, from_account=True)
        db.add_snapshot(self.conn, "c1", T0 - DAY, 1, 0, 1, 1)
        res = self.run_import(self.basic_export())
        self.assertEqual((res.added, res.updated), (0, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2)

    def test_warns_when_export_is_from_an_older_bookmark(self):
        res = self.run_import(self.basic_export(list_version="aaaa1111"), version="bbbb2222")
        self.assertTrue(any("older version of the bookmark" in w for w in res.warnings))
        res = self.run_import(self.basic_export(list_version="bbbb2222", exported_at=T0 + 1), version="bbbb2222")
        self.assertFalse(any("older version" in w for w in res.warnings))


class BookmarkletTests(unittest.TestCase):
    LINKS = ("https://www.reddit.com/r/a/comments/p1/s/c1/?x=1#frag\n"
             "https://www.reddit.com/r/IndianStockMarket/s/AbC123\n")

    def config(self, users=("alice", "bob"), links_text=None):
        links = parse_comments(self.LINKS if links_text is None else links_text).links
        return BM.build_config(list(users), links, CFG), links

    def test_url_is_a_single_safe_javascript_url(self):
        config, _ = self.config()
        url = BM.to_url(BM.build_js(config))
        self.assertTrue(url.startswith("javascript:"))
        for ch in (" ", "#", '"', "\n", "%0", "%%"):
            if ch.startswith("%"):
                continue
            self.assertNotIn(ch, url[len("javascript:"):])
        js = unquote(url[len("javascript:"):])
        self.assertIn('"users": ["alice", "bob"]', js)
        self.assertIn('"path": "/r/IndianStockMarket/s/AbC123"', js)
        self.assertNotIn("__NB_CONFIG__", js)

    def test_list_version_changes_with_lists_only(self):
        a, links = self.config()
        b, _ = self.config(users=("Bob", "ALICE"))
        c, _ = self.config(users=("alice", "bob", "carol"))
        d, _ = self.config(links_text="")
        self.assertEqual(a["listVersion"], b["listVersion"])  # order and case don't matter
        self.assertNotEqual(a["listVersion"], c["listVersion"])
        self.assertNotEqual(a["listVersion"], d["listVersion"])
        self.assertEqual(len(a["listVersion"]), 8)

    def test_for_files_reads_current_lists(self):
        with tempfile.TemporaryDirectory() as d:
            acc, com = Path(d) / "a.txt", Path(d) / "c.txt"
            acc.write_text("u/alice\n# x\nbob,hunter2\n", encoding="utf-8")
            com.write_text(self.LINKS, encoding="utf-8")
            url, version, n_users, n_links = BM.for_files(acc, com, CFG)
            self.assertEqual((n_users, n_links), (2, 2))
            self.assertNotIn("hunter2", unquote(url))

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_javascript_syntax(self):
        config, _ = self.config()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.js"
            path.write_text(unquote(BM.to_url(BM.build_js(config))[len("javascript:"):]), encoding="utf-8")
            proc = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class EndToEndTests(unittest.TestCase):
    """Runs the real bookmarklet in Node against a fake reddit.com, then imports what it downloads."""

    def test_bookmarklet_export_imports_cleanly(self):
        cfg = dict(CFG, browser_sync_delay_seconds=0, browser_sync_retry_seconds=0.01, browser_sync_max_pages=3)
        links = parse_comments("https://www.reddit.com/r/x/comments/p9/slug/c9/\n"
                               "https://www.reddit.com/r/x/s/Share1\n").links
        config = BM.build_config(["alice", "ghost"], links, cfg)
        js = BM.build_js(config)

        kids = tree(comment("k1", "p1", author="op_user", body="will try, thanks", parent="t1_c1"))
        c1 = comment("c1", "p1", author="alice", body="Nubra (I work at Nubra)", score=8, replies=kids)
        c2 = comment("c2", "p2", author="alice", body="nested", score=2, parent="t1_z1", sub="IndiaAlgoTrading")
        c9 = comment("c9", "p9", author="carol", score=4, sub="x")
        c8 = comment("c8", "p8", author="dave", score=1, sub="x")
        q = "&raw_json=1"
        routes = {
            "/user/alice/comments.json?limit=100" + q: {"body": listing([c1], after="t1_c1")},
            "/user/alice/comments.json?limit=100" + q + "&after=t1_c1": {"body": listing([c2])},
            "/comments/p9/_/c9.json?depth=2" + q: {"body": thread_payload("p9", c9, sub="x")},
            "/r/x/s/Share1": {"type": "text/html", "url": "https://www.reddit.com/r/x/comments/p8/slug/c8/"},
            "/comments/p8/_/c8.json?depth=2" + q: {"body": thread_payload("p8", c8, sub="x")},
            "/comments/p1.json?sort=top&limit=500&depth=8" + q: {
                "body": thread_payload("p1", [comment("o1", "p1", author="bob", body="dhan is ok", score=20), c1],
                                       author="op_user")},
            # p2 thread fails twice with 500 then works; c2 is too deep for it
            "/comments/p2.json?sort=top&limit=500&depth=8" + q: [
                {"status": 500, "body": {}}, {"status": 500, "body": {}},
                {"body": thread_payload("p2", [comment("z1", "p2", author="eve")], sub="IndiaAlgoTrading")}],
            "/comments/p2/_/c2.json?depth=2" + q: {"body": thread_payload("p2", c2, sub="IndiaAlgoTrading")},
            "/comments/p9.json?sort=top&limit=500&depth=8" + q: {"body": thread_payload("p9", [c9], sub="x")},
            "/comments/p8.json?sort=top&limit=500&depth=8" + q: {"status": 403, "body": {}},
            "/r/IndianStockMarket/about.json?raw_json=1": {"body": {"data": {"subscribers": 1_000_000}}},
            "/r/IndiaAlgoTrading/about.json?raw_json=1": {"body": {"data": {"subscribers": 40_000}}},
            "/r/x/about.json?raw_json=1": {"body": {"data": {"subscribers": 12}}},
            # --- logged-out health checks ---
            "anon:/user/alice/about.json?raw_json=1": {"body": {"data": {"name": "alice", "is_suspended": False}}},
            "anon:/user/ghost/about.json?raw_json=1": {"status": 404, "body": {"message": "Not Found", "error": 404}},
            # alice's first page has 1 comment logged in, 0 to visitors -> only possibly hidden at >= 3 comments
            "anon:/user/alice/comments.json?limit=100" + q: {"body": listing([])},
            # c1 shows as [removed] to visitors; o1 is fine
            "anon:/comments/p1.json?sort=top&limit=500&depth=8" + q: {
                "body": thread_payload("p1", [comment("c1", "p1", author="[deleted]", body="[removed]")])},
            # c2 is missing from the thread view and from its permalink -> not visible
            "anon:/comments/p2.json?sort=top&limit=500&depth=8" + q: {
                "body": thread_payload("p2", [comment("z1", "p2", author="eve")], sub="IndiaAlgoTrading")},
            "anon:/comments/p2/_/c2.json?depth=1" + q: {"body": thread_payload("p2", None, sub="IndiaAlgoTrading")},
            "anon:/comments/p9.json?sort=top&limit=500&depth=8" + q: {"body": thread_payload("p9", [c9], sub="x")},
            # p8: visitors can't see the subreddit
            "anon:/comments/p8.json?sort=top&limit=500&depth=8" + q: {"status": 403, "body": {"reason": "private"}},
        }
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "b.js").write_text(js, encoding="utf-8")
            (d / "routes.json").write_text(json.dumps(routes), encoding="utf-8")
            proc = subprocess.run(["node", str(HERE / "bookmarklet_harness.js"), str(d / "b.js"),
                                   str(d / "routes.json")], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)
            export, calls = out["export"], out["calls"]

            # every request stayed on the page's own origin (relative paths)
            self.assertTrue(all(c.replace("anon:", "", 1).startswith("/") for c in calls), calls)
            # health checks ran logged-out, the regular fetches did not
            self.assertIn("anon:/user/ghost/about.json?raw_json=1", calls)
            self.assertIn("/user/alice/comments.json?limit=100&raw_json=1", calls)
            self.assertEqual(export["health"]["accounts"]["ghost"]["about"], {"http": 404, "json": True})
            self.assertEqual(export["health"]["accounts"]["alice"]["normal_count"], 1)
            self.assertEqual(export["health"]["accounts"]["alice"]["anon_count"], 0)
            self.assertEqual(export["health"]["replies"]["c1"], {"found": True, "body": "removed"})
            self.assertEqual(export["health"]["replies"]["c2"], {"found": False})
            self.assertEqual(export["health"]["replies"]["c8"], {"found": False, "http": 403})
            self.assertEqual(export["accounts"]["ghost"]["status"], "not_found")
            self.assertEqual([i["id"] for i in export["accounts"]["alice"]["items"]], ["c1", "c2"])
            self.assertEqual(sorted(export["threads"]), ["p1", "p2", "p9"])
            self.assertIn("c2", export["comments"])
            self.assertEqual(export["list_version"], config["listVersion"])
            self.assertTrue(any("p8" in e["item"] for e in export["errors"]))
            self.assertNotIn("selftext_html", json.dumps(export))  # trimmed to the fields we use

            acc, com = d / "accounts.txt", d / "comments.txt"
            acc.write_text("alice\nghost\n", encoding="utf-8")
            com.write_text("https://www.reddit.com/r/x/comments/p9/slug/c9/\nhttps://www.reddit.com/r/x/s/Share1\n",
                           encoding="utf-8")
            conn = db.connect(":memory:")
            res = BI.import_export(conn, CFG, BI.parse_export(json.dumps(export)), acc, com, config["listVersion"])
            self.assertEqual(res.added, 4)  # c1, c2, c9, c8
            snaps = {r["comment_id"]: r for r in conn.execute("SELECT * FROM snapshots")}
            self.assertEqual(snaps["c1"]["thread_rank"], 2)
            self.assertEqual(snaps["c1"]["op_engaged"], 1)
            self.assertEqual(snaps["c2"]["score"], 2)
            self.assertIsNone(snaps["c8"]["replies_count"])  # its thread request failed
            self.assertEqual(db.get_meta(conn, "share:https://www.reddit.com/r/x/s/Share1"), "c8")
            subs = dict(conn.execute("SELECT name, subscribers FROM subreddits").fetchall())
            self.assertEqual(subs["r/IndiaAlgoTrading"], 40_000)
            vis = dict(conn.execute("SELECT comment_id, visibility FROM replies").fetchall())
            self.assertEqual(vis, {"c1": "removed", "c2": "not_visible", "c9": "public", "c8": "not_visible"})
            statuses = dict(conn.execute("SELECT username, status FROM account_health").fetchall())
            self.assertEqual(statuses, {"alice": "visible", "ghost": "shadowbanned"})
            conn.close()


if __name__ == "__main__":
    unittest.main()
