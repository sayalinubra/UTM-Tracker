import json
import tempfile
import unittest
from pathlib import Path

from tests.fake_reddit import (FakeReddit, FakeResponse, add_thread, comment, listing, make_client, ok,
                               replies_tree, tree)
from tracker import db
from tracker.config import load_config
from tracker.sync import refresh, refresh_due, sync, sync_and_refresh

CFG = load_config()
DAY = 86400
BODY = "Nubra has greeks (I work at Nubra)"


class SyncBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.accounts = d / "accounts.txt"
        self.comments = d / "comments.txt"
        self.accounts.write_text("alice\n", encoding="utf-8")
        self.comments.write_text("", encoding="utf-8")
        self.conn = db.connect(":memory:")
        self.fake = FakeReddit()
        self.client, _ = make_client(self.fake)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def thread(self, post, cid, score, n_replies=0, author="alice", body=BODY, thread_comments=40, **kw):
        c = comment(cid, post, author=author, body=body, score=score, replies=replies_tree(n_replies))
        add_thread(self.fake, post, [c], num_comments=thread_comments, **kw)
        return c

    def listing_of(self, *items):
        self.fake.add("/user/alice/comments.json", ok(listing(list(items))))

    def run_sync(self, now):
        return sync(self.conn, self.client, CFG, self.accounts, self.comments, now=now)

    def run_refresh(self, now):
        return refresh(self.conn, self.client, CFG, self.accounts, self.comments, now=now)

    def snap(self, cid):
        return self.conn.execute("SELECT * FROM snapshots WHERE comment_id=? ORDER BY taken_at DESC",
                                 (cid,)).fetchone()


class SyncTests(SyncBase):
    def test_sync_stores_reply_classification_and_first_snapshot(self):
        self.listing_of(comment("c1", "p1", author="alice", body=BODY))
        self.thread("p1", "c1", score=3, n_replies=2)
        result = self.run_sync(now=1000.0)
        self.assertEqual(result.new_replies, 1)
        r = self.conn.execute("SELECT * FROM replies").fetchone()
        self.assertEqual((r["account"], r["subreddit"], r["status"], r["depth"]),
                         ("alice", "r/IndianStockMarket", "live", 0))
        self.assertTrue(r["thread_link"].startswith("https://www.reddit.com/r/"))
        c = self.conn.execute("SELECT * FROM classifications").fetchone()
        self.assertEqual((c["mentions_nubra"], c["disclosed"], c["topic"]), (1, 1, "Options & Greeks"))
        s = self.conn.execute("SELECT * FROM snapshots").fetchall()
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["score"], s[0]["replies_count"], s[0]["thread_comments"]), (3, 2, 40))

    def test_text_truncated_to_300_chars(self):
        self.listing_of(comment("c1", "p1", author="alice", body="x" * 500))
        self.thread("p1", "c1", score=1, body="x" * 500)
        self.run_sync(now=1000.0)
        self.assertEqual(len(self.conn.execute("SELECT text FROM replies").fetchone()["text"]), 300)

    def test_dedup_between_account_and_link(self):
        self.comments.write_text("https://old.reddit.com/r/IndianStockMarket/comments/p1/slug/c1/\n", encoding="utf-8")
        self.listing_of(comment("c1", "p1", author="alice"))
        self.thread("p1", "c1", score=5)
        sync_and_refresh(self.conn, self.client, CFG, self.accounts, self.comments, now=1000.0)
        rows = self.conn.execute("SELECT * FROM replies").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["from_account"], rows[0]["from_link"]), (1, 1))
        # only one snapshot for this run even though both sources saw it
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)

    def test_linked_comment_from_other_account_is_tracked(self):
        self.accounts.write_text("", encoding="utf-8")
        self.comments.write_text("https://www.reddit.com/r/x/comments/p9/s/c9/\n", encoding="utf-8")
        self.thread("p9", "c9", score=4, author="someone_else")
        result = self.run_sync(now=1000.0)
        self.assertEqual(result.new_replies, 1)
        self.assertEqual(self.conn.execute("SELECT account FROM replies").fetchone()[0], "someone_else")
        self.assertEqual(self.snap("c9")["score"], 4)

    def test_share_link_resolved(self):
        self.accounts.write_text("", encoding="utf-8")
        share = "https://www.reddit.com/r/x/s/AbC123"
        self.comments.write_text(share + "\n", encoding="utf-8")
        self.fake.add("/r/x/s/AbC123", FakeResponse(200, {}, url="https://www.reddit.com/r/x/comments/p9/slug/c9/"))
        self.thread("p9", "c9", score=4)
        self.run_sync(now=1000.0)
        self.assertEqual(db.get_meta(self.conn, "share:" + share), "c9")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0], 1)

    def test_snapshots_over_time_and_removal_keeps_text(self):
        self.listing_of(comment("c1", "p1", author="alice", body="original"))
        self.thread("p1", "c1", score=2, body="original")
        self.run_sync(now=1000.0)
        self.thread("p1", "c1", score=9, n_replies=1, body="original")
        self.run_refresh(now=1000.0 + DAY)
        removed = comment("c1", "p1", author="[deleted]", body="[removed]", score=9)
        add_thread(self.fake, "p1", [removed])
        self.run_refresh(now=1000.0 + 2 * DAY)
        scores = [r["score"] for r in self.conn.execute("SELECT score FROM snapshots ORDER BY taken_at")]
        self.assertEqual(scores, [2, 9, 9])
        r = self.conn.execute("SELECT * FROM replies").fetchone()
        self.assertEqual((r["status"], r["text"], r["account"]), ("removed", "original", "alice"))
        self.assertEqual(float(db.get_meta(self.conn, "last_refresh")), 1000.0 + 2 * DAY)

    def test_deleted_replies_are_not_refreshed(self):
        self.listing_of(comment("c1", "p1", author="alice"))
        self.thread("p1", "c1", score=2)
        self.run_sync(now=1000.0)
        add_thread(self.fake, "p1", [comment("c1", "p1", author="[deleted]", body="[deleted]")])
        self.run_refresh(now=2000.0)
        self.assertEqual(self.conn.execute("SELECT status FROM replies").fetchone()[0], "deleted")
        before = len(self.fake.calls)
        self.run_refresh(now=3000.0)
        thread_calls = [c for c in self.fake.calls[before:] if c[0].startswith("/comments/")]
        self.assertEqual(thread_calls, [])

    def test_missing_comment_and_missing_thread(self):
        self.listing_of(comment("c1", "p1", author="alice"), comment("c2", "p2", author="alice"))
        add_thread(self.fake, "p1", [], permalinks=[])
        self.fake.add("/comments/p1/_/c1.json", ok([{"kind": "Listing", "data": {"children": []}},
                                                   {"kind": "Listing", "data": {"children": []}}]))
        self.run_sync(now=1000.0)  # p2 has no route at all -> 404
        statuses = dict(self.conn.execute("SELECT comment_id, status FROM replies").fetchall())
        self.assertEqual(statuses, {"c1": "removed", "c2": "removed"})

    def test_missing_and_suspended_accounts(self):
        self.accounts.write_text("ghost\nbanned\n", encoding="utf-8")
        self.fake.add("/user/banned/comments.json", FakeResponse(403, {}))
        self.fake.add("/user/banned/about.json", ok({"data": {"is_suspended": True}}))
        result = self.run_sync(now=1000.0)
        text = " ".join(result.warnings)
        self.assertIn("u/ghost: account not found", text)
        self.assertIn("suspended", text)
        status = dict(self.conn.execute("SELECT username, status FROM accounts").fetchall())
        self.assertEqual(status, {"ghost": "not_found", "banned": "unavailable"})

    def test_incremental_sync_stops_at_known_page(self):
        self.fake.add("/user/alice/comments.json", ok(listing([comment("c1", "p1", author="alice")], after="t1_c1")))
        self.fake.add("/user/alice/comments.json?after=t1_c1", ok(listing([comment("c0", "p0", author="alice")])))
        self.thread("p1", "c1", score=1)
        self.thread("p0", "c0", score=1)
        self.run_sync(now=1000.0)
        self.fake.calls.clear()
        self.run_sync(now=2000.0)
        self.assertEqual([c[0] for c in self.fake.calls], ["/user/alice/comments.json"])

    def test_rate_limit_abort_is_reported(self):
        self.fake.add("/user/alice/comments.json", FakeResponse(429, {}))
        result = self.run_sync(now=1000.0)
        self.assertTrue(result.aborted)
        self.assertTrue(any("rate-limiting" in w for w in result.warnings))

    def test_block_page_aborts_without_flagging_accounts(self):
        self.fake.add("/user/alice/comments.json", FakeResponse(403, None, {"content-type": "text/html"}))
        result = self.run_sync(now=1000.0)
        self.assertTrue(result.aborted)
        self.assertTrue(any("blocking logged-out requests" in w for w in result.warnings))
        self.assertIn("didn't finish", result.messages[0])
        self.assertIsNone(db.get_meta(self.conn, "last_sync"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)

    def test_refresh_with_nothing_tracked_does_not_claim_a_refresh(self):
        result = self.run_refresh(now=5000.0)
        self.assertEqual(result.refreshed, 0)
        self.assertIsNone(db.get_meta(self.conn, "last_refresh"))

    def test_removed_account_is_no_longer_refreshed(self):
        self.listing_of(comment("c1", "p1", author="alice"))
        self.thread("p1", "c1", score=1)
        self.run_sync(now=1000.0)
        self.accounts.write_text("", encoding="utf-8")
        self.fake.calls.clear()
        self.run_refresh(now=2000.0)
        self.assertEqual([c for c in self.fake.calls if c[0].startswith("/comments/")], [])


class NewMetricsTests(SyncBase):
    """Rank, depth, OP engagement, responses + sentiment, mentions, subreddit size, timing."""

    def setUp(self):
        super().setUp()
        self.fake.add("/r/IndianStockMarket/about.json", ok({"data": {"subscribers": 250_000}}))

    def test_rank_ignores_stickied_and_counts_hidden_top_level(self):
        mine = comment("c1", "p1", author="alice", score=5)
        others = [comment("o1", "p1", author="bob", score=10), comment("o2", "p1", author="eve", score=3),
                  comment("am", "p1", author="AutoModerator", score=100, stickied=True)]
        self.listing_of(mine)
        add_thread(self.fake, "p1", [others[2], others[0], mine, others[1]], permalinks=[mine],
                   more_ids=("h1", "h2"))
        self.run_sync(now=1000.0)
        s = self.snap("c1")
        self.assertEqual((s["thread_rank"], s["top_level_count"]), (2, 5))

    def test_top_comment_and_upvote_ratio(self):
        mine = comment("c1", "p1", author="alice", score=50)
        self.listing_of(mine)
        add_thread(self.fake, "p1", [mine, comment("o1", "p1", author="bob", score=2)], upvote_ratio=0.87)
        self.run_sync(now=1000.0)
        s = self.snap("c1")
        self.assertEqual((s["thread_rank"], s["upvote_ratio"]), (1, 0.87))

    def test_op_engagement_responses_and_sentiment(self):
        kids = tree(comment("k1", "p1", author="op_user", body="Thanks, this is helpful!", parent="t1_c1"),
                    comment("k2", "p1", author="carol", body="this is just a shill ad", parent="t1_c1"),
                    comment("k3", "p1", author="dave", body="what about margins?", parent="t1_c1"),
                    comment("k4", "p1", author="alice", body="thanks all", parent="t1_c1"))
        mine = comment("c1", "p1", author="alice", score=4, replies=kids)
        self.listing_of(comment("c1", "p1", author="alice"))
        add_thread(self.fake, "p1", [mine], author="op_user")
        self.run_sync(now=1000.0)
        self.assertEqual(self.conn.execute("SELECT op_engaged FROM replies").fetchone()[0], 1)
        self.assertEqual(self.snap("c1")["op_engaged"], 1)
        resp = dict(self.conn.execute("SELECT author, sentiment FROM responses").fetchall())
        # my own account's reply (k4) is not an audience reaction
        self.assertEqual(resp, {"op_user": "positive", "carol": "negative", "dave": "neutral"})

    def test_op_engagement_is_sticky(self):
        kids = tree(comment("k1", "p1", author="op_user", body="ok", parent="t1_c1"))
        self.listing_of(comment("c1", "p1", author="alice"))
        add_thread(self.fake, "p1", [comment("c1", "p1", author="alice", replies=kids)], author="op_user")
        self.run_sync(now=1000.0)
        add_thread(self.fake, "p1", [comment("c1", "p1", author="alice")], author="op_user")
        self.run_refresh(now=2000.0)
        self.assertEqual(self.conn.execute("SELECT op_engaged FROM replies").fetchone()[0], 1)

    def test_nested_reply_has_depth_and_no_rank_and_falls_back_to_permalink(self):
        nested = comment("c2", "p1", author="alice", score=7, parent="t1_o1")
        top = comment("o1", "p1", author="bob", score=10)  # thread fetch doesn't include the nested reply
        self.listing_of(nested)
        add_thread(self.fake, "p1", [top], permalinks=[nested])
        self.run_sync(now=1000.0)
        r = self.conn.execute("SELECT depth FROM replies WHERE comment_id='c2'").fetchone()
        s = self.snap("c2")
        self.assertEqual((r["depth"], s["score"], s["thread_rank"]), (1, 7, None))
        self.assertIn("/comments/p1/_/c2.json", [c[0] for c in self.fake.calls])

    def test_conversation_mentions_by_other_users(self):
        mine = comment("c1", "p1", author="alice", body="Nubra's API has greeks (I work at Nubra)")
        others = [comment("o1", "p1", author="bob", body="Zerodha kite is fine, zerodha zerodha"),
                  comment("o2", "p1", author="eve", body="I moved from Upstox to Nubra"),
                  comment("o3", "p1", author="AngelFan", body="SmartAPI works")]
        self.listing_of(mine)
        add_thread(self.fake, "p1", [mine] + others, permalinks=[mine], title="Dhan or Fyers?",
                   selftext="Also considering Groww")
        self.run_sync(now=1000.0)
        scan = self.conn.execute("SELECT * FROM thread_scans").fetchone()
        self.assertEqual(json.loads(scan["mentions"]),
                         {"Zerodha": 1, "Upstox": 1, "Nubra": 1, "Angel One": 1, "Dhan": 1, "Fyers": 1,
                          "Groww": 1})
        self.assertEqual(scan["comments_scanned"], 4)  # post + 3 other comments, not mine

    def test_subreddit_size_cached_daily(self):
        self.listing_of(comment("c1", "p1", author="alice"))
        self.thread("p1", "c1", score=1)
        self.run_sync(now=1000.0)
        self.run_refresh(now=1000.0 + 3600)
        about_calls = [c for c in self.fake.calls if c[0].endswith("/about.json")]
        self.assertEqual(len(about_calls), 1)
        self.assertEqual(self.conn.execute("SELECT subscribers FROM subreddits").fetchone()[0], 250_000)
        self.run_refresh(now=1000.0 + DAY + 1)
        self.assertEqual(len([c for c in self.fake.calls if c[0].endswith("/about.json")]), 2)

    def test_thread_timing_fields_stored(self):
        self.listing_of(comment("c1", "p1", author="alice", created=1_700_003_600))
        add_thread(self.fake, "p1", [comment("c1", "p1", author="alice", created=1_700_003_600)],
                   created=1_700_000_000, author="op_user")
        self.run_sync(now=1000.0)
        r = self.conn.execute("SELECT created_utc, thread_created_utc, thread_author FROM replies").fetchone()
        self.assertEqual((r[0] - r[1], r[2]), (3600, "op_user"))

    def test_one_thread_request_for_several_replies(self):
        a, b = comment("c1", "p1", author="alice"), comment("c2", "p1", author="alice", score=3)
        self.listing_of(a, b)
        add_thread(self.fake, "p1", [a, b])
        self.run_sync(now=1000.0)
        thread_calls = [c[0] for c in self.fake.calls if c[0].startswith("/comments/")]
        self.assertEqual(thread_calls, ["/comments/p1.json"])

    def test_existing_database_is_migrated(self):
        path = Path(self.tmp.name) / "old.db"
        import sqlite3
        old = sqlite3.connect(path)
        old.executescript("CREATE TABLE replies (comment_id TEXT PRIMARY KEY, post_id TEXT, account TEXT, "
                          "permalink TEXT, subreddit TEXT, thread_title TEXT, thread_link TEXT, created_utc REAL, "
                          "text TEXT, status TEXT, from_account INTEGER, from_link INTEGER, first_seen REAL, "
                          "last_seen REAL, note TEXT);"
                          "CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, comment_id TEXT, "
                          "taken_at REAL, score INTEGER, replies_count INTEGER, thread_score INTEGER, "
                          "thread_comments INTEGER);"
                          "INSERT INTO replies(comment_id, status) VALUES('x1', 'live');")
        old.close()
        conn = db.connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
        self.assertTrue({"thread_rank", "upvote_ratio", "op_engaged"} <= cols)
        self.assertEqual(conn.execute("SELECT comment_id FROM replies").fetchone()[0], "x1")
        conn.close()


class AutoRefreshTimingTests(unittest.TestCase):
    def test_refresh_due(self):
        hours = 6
        self.assertTrue(refresh_due(None, 10_000, hours))
        self.assertTrue(refresh_due("", 10_000, hours))
        self.assertFalse(refresh_due(10_000, 10_000 + 5 * 3600, hours))
        self.assertFalse(refresh_due("10000", 10_000 + 6 * 3600, hours))
        self.assertTrue(refresh_due(10_000, 10_000 + 6 * 3600 + 1, hours))


if __name__ == "__main__":
    unittest.main()
