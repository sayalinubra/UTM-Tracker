"""Account health / visibility rules, their import, metric exclusions and the report section."""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests.fake_reddit import comment, thread_payload
from tests.test_metrics import MON, IST, DAY, replies_df, snaps_df
from tracker import browser_import as BI
from tracker import db
from tracker import health as H
from tracker import metrics as m
from tracker import report as R
from tracker.config import load_config

CFG = load_config()
T0 = 1_761_600_000.0


def account(http=200, suspended=False, json_body=True, normal=None, anon=None, error=None, anon_error=None):
    about = {"error": error} if error else {"http": http, "json": json_body, "is_suspended": suspended}
    rec = {"about": about, "normal_count": normal, "anon_count": anon}
    if anon_error:
        rec["anon_error"] = anon_error
    return rec


class AccountClassificationTests(unittest.TestCase):
    def test_about_json_responses(self):
        self.assertEqual(H.classify_account(account(404), CFG).status, H.SHADOWBANNED)
        self.assertEqual(H.classify_account(account(200, suspended=True), CFG).status, H.SUSPENDED)
        self.assertEqual(H.classify_account(account(200, normal=10, anon=10), CFG).status, H.VISIBLE)

    def test_failed_checks_are_never_called_shadowbans(self):
        # a 404 that isn't Reddit's JSON (e.g. a block page) and network errors are "couldn't check"
        self.assertEqual(H.classify_account(account(404, json_body=False), CFG).status, H.UNKNOWN)
        self.assertEqual(H.classify_account(account(error="Reddit returned a page instead of data"), CFG).status,
                         H.UNKNOWN)
        self.assertEqual(H.classify_account(account(403), CFG).status, H.UNKNOWN)
        self.assertEqual(H.classify_account(None, CFG).status, H.UNKNOWN)

    def test_anonymous_vs_normal_comment_counts(self):
        # under 50% of the normal count shows to logged-out visitors -> possibly hidden
        self.assertEqual(H.classify_account(account(normal=40, anon=19), CFG).status, H.POSSIBLY_HIDDEN)
        self.assertEqual(H.classify_account(account(normal=40, anon=20), CFG).status, H.VISIBLE)
        self.assertEqual(H.classify_account(account(normal=10, anon=0), CFG).status, H.POSSIBLY_HIDDEN)
        # too few comments to judge
        self.assertEqual(H.classify_account(account(normal=2, anon=0), CFG).status, H.VISIBLE)
        # comment check failed: the profile itself was visible
        check = H.classify_account(account(normal=40, anon_error="timeout"), CFG)
        self.assertEqual(check.status, H.VISIBLE)
        self.assertIn("failed", check.reason)

    def test_thresholds_come_from_config(self):
        cfg = dict(CFG, account_health={"possibly_hidden_ratio": 0.9, "possibly_hidden_min_comments": 1})
        self.assertEqual(H.classify_account(account(normal=10, anon=8), cfg).status, H.POSSIBLY_HIDDEN)


class ReplyVisibilityTests(unittest.TestCase):
    def test_removed_deleted_missing_and_failures(self):
        c = H.classify_reply
        self.assertEqual(c({"found": True, "body": "ok"})[0], H.PUBLIC)
        self.assertEqual(c({"found": True, "body": "removed"})[0], H.REMOVED)
        self.assertEqual(c({"found": True, "body": "deleted"})[0], H.DELETED)
        self.assertEqual(c({"found": False})[0], H.NOT_VISIBLE)              # missing = not visible
        self.assertEqual(c({"found": False, "http": 404})[0], H.NOT_VISIBLE)  # thread gone for visitors
        self.assertEqual(c({"found": False, "http": 403})[0], H.NOT_VISIBLE)  # private subreddit
        self.assertEqual(c({"found": False, "error": "timeout"})[0], H.VIS_UNKNOWN)
        self.assertEqual(c(None), (None, None))

    def test_account_status_and_logged_in_deletion_override(self):
        self.assertEqual(H.classify_reply({"found": True, "body": "removed"}, H.SHADOWBANNED)[0], H.NOT_VISIBLE)
        self.assertEqual(H.classify_reply(None, H.SUSPENDED)[0], H.NOT_VISIBLE)
        self.assertEqual(H.classify_reply({"found": False}, H.VISIBLE, "deleted")[0], H.DELETED)


def history(rows):
    return pd.DataFrame(rows, columns=["username", "checked_at", "status", "reason"])


class HistoryAndAlertTests(unittest.TestCase):
    def test_status_changes_skip_repeats_and_failed_checks(self):
        h = history([("alice", 1, H.VISIBLE, "ok"), ("alice", 2, H.VISIBLE, "ok"), ("alice", 3, H.UNKNOWN, "x"),
                     ("alice", 4, H.SHADOWBANNED, "404"), ("bob", 1, H.VISIBLE, "ok")])
        ch = H.status_changes(h)
        # pandas stores "no previous status" as NaN; treat it as None
        self.assertEqual([(r.username, r.changed_at, r.from_status if isinstance(r.from_status, str) else None,
                           r.to_status) for r in ch.itertuples()],
                         [("alice", 1, None, H.VISIBLE), ("alice", 4, H.VISIBLE, H.SHADOWBANNED),
                          ("bob", 1, None, H.VISIBLE)])

    def test_current_status_first_detected_and_last_check(self):
        h = history([("alice", 1, H.VISIBLE, "ok"), ("alice", 4, H.SHADOWBANNED, "404"),
                     ("alice", 6, H.SHADOWBANNED, "404"), ("alice", 9, H.UNKNOWN, "blocked")])
        c = H.current_status(h)["alice"]
        self.assertEqual((c["status"], c["first_detected"], c["last_checked"], c["last_check_failed"]),
                         (H.SHADOWBANNED, 4.0, 9.0, True))

    def test_new_problem_alert_only_for_visible_to_problem_since_previous_import(self):
        h = history([("alice", 1, H.VISIBLE, "ok"), ("alice", 2, H.SHADOWBANNED, "404"),
                     ("bob", 1, H.VISIBLE, "ok"), ("bob", 2, H.VISIBLE, "ok"),
                     ("carol", 1, H.SHADOWBANNED, "404"), ("carol", 2, H.SHADOWBANNED, "404"),
                     ("dave", 1, H.VISIBLE, "ok"), ("dave", 2, H.POSSIBLY_HIDDEN, "few")])
        self.assertEqual(sorted(p["username"] for p in H.new_problems(h)), ["alice", "dave"])
        # once a later import happens, the alert is no longer "new"
        h2 = pd.concat([h, history([("alice", 3, H.SHADOWBANNED, "404"), ("dave", 3, H.POSSIBLY_HIDDEN, "few")])])
        self.assertEqual(H.new_problems(h2), [])
        # a failed check in between doesn't hide the change
        h3 = history([("eve", 1, H.VISIBLE, "ok"), ("eve", 2, H.UNKNOWN, "x"), ("eve", 3, H.SUSPENDED, "s")])
        self.assertEqual([p["username"] for p in H.new_problems(h3)], ["eve"])
        self.assertEqual(H.new_problems(history([("x", 1, H.SHADOWBANNED, "404")])), [])


class AtRiskTests(unittest.TestCase):
    NOW = MON + 20 * DAY

    def stats(self, rows):
        base = {"account": "alice", "score": 5, "replies_count": 1, "visibility": H.PUBLIC, "status": "live",
                "mentions_nubra": False}
        return pd.DataFrame([{**base, "comment_id": f"c{i}", "created_utc": self.NOW - (2 + i) * DAY, **r}
                             for i, r in enumerate(rows)])

    def test_stuck_replies(self):
        stuck = {"score": 1, "replies_count": 0}
        risk = H.at_risk(self.stats([stuck, stuck, stuck, {}]), self.NOW, CFG)
        self.assertIn("3 of 4 recent replies stuck at 1 upvote", risk["alice"][0])
        self.assertEqual(H.at_risk(self.stats([stuck, stuck, {}, {}]), self.NOW, CFG), {})

    def test_new_replies_are_not_judged_as_stuck(self):
        rows = [{"score": 1, "replies_count": 0, "created_utc": self.NOW - 3600} for _ in range(4)]
        self.assertEqual(H.at_risk(self.stats(rows), self.NOW, CFG), {})

    def test_removed_share_and_nubra_mentions(self):
        risk = H.at_risk(self.stats([{"visibility": H.REMOVED}, {}, {}, {}, {}]), self.NOW, CFG)
        self.assertIn("1 of 5 recent replies removed by moderators", risk["alice"][0])
        risk = H.at_risk(self.stats([{"mentions_nubra": True}] * 3 + [{}] * 7), self.NOW, CFG)
        self.assertIn("3 of 10 recent replies mention or link Nubra", risk["alice"][0])
        risk = H.at_risk(self.stats([{"mentions_nubra": True}] * 2 + [{}] * 3), self.NOW, CFG)
        self.assertIn("2 of 5", risk["alice"][0])  # 40% share

    def test_needs_enough_recent_replies_and_ignores_old_ones(self):
        self.assertEqual(H.at_risk(self.stats([{"visibility": H.REMOVED}] * 2), self.NOW, CFG), {})
        old = [{"visibility": H.REMOVED, "created_utc": self.NOW - 30 * DAY}] * 5
        self.assertEqual(H.at_risk(self.stats(old), self.NOW, CFG), {})


class MetricExclusionTests(unittest.TestCase):
    def setUp(self):
        self.now = MON + 10 * DAY
        self.replies = replies_df([
            {"comment_id": "a", "post_id": "p1", "created_utc": MON, "visibility": H.PUBLIC},
            {"comment_id": "b", "post_id": "p2", "created_utc": MON, "visibility": H.NOT_VISIBLE},
            {"comment_id": "c", "post_id": "p3", "created_utc": MON, "visibility": H.REMOVED},
            {"comment_id": "d", "post_id": "p4", "created_utc": MON, "visibility": None},
        ])
        self.snaps = snaps_df([("a", MON + DAY, 10, 1, 5, 100, 1), ("b", MON + DAY, 30, 0, 5, 900, 1),
                               ("c", MON + DAY, 20, 0, 5, 500, 1), ("d", MON + DAY, 2, 0, 5, 50, 4)])

    def test_hidden_replies_left_out_of_reach_metrics_only(self):
        k = m.kpis_at(self.replies, self.snaps, self.now)
        self.assertEqual(k["replies_posted"], 4)            # counts every reply
        self.assertEqual(k["total_upvotes"], 62.0)          # totals too
        self.assertEqual(k["avg_upvotes"], 6.0)             # (10 + 2) / 2 visible
        self.assertEqual(k["thread_reach"], 150.0)          # 100 + 50
        self.assertEqual(k["top_comment_rate"], 50.0)       # a is #1, d is #4
        self.assertEqual(k["hidden_replies"], 2)

    def test_group_averages_and_breakdown(self):
        g = m.by_group(self.replies, self.snaps, "subreddit")
        self.assertEqual((int(g.iloc[0]["replies"]), float(g.iloc[0]["avg_upvotes"])), (4, 6.0))
        n, by = H.hidden_breakdown(self.replies)
        self.assertEqual((n, by), (2, {"Not visible publicly": 1, "Removed by moderators": 1}))


class ImportHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.accounts, self.comments = d / "accounts.txt", d / "comments.txt"
        self.accounts.write_text("alice\nbob\n", encoding="utf-8")
        self.comments.write_text("", encoding="utf-8")
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def export(self, t, alice, bob, replies):
        a = [comment("a1", "p1", author="alice", created=T0 - DAY), comment("a2", "p1", author="alice", created=T0 - DAY)]
        b = [comment("b1", "p1", author="bob", created=T0 - DAY)]
        return {"format": "nubra-reddit-export", "version": 1, "exported_at": t,
                "accounts": {"alice": {"status": "ok", "items": a}, "bob": {"status": "ok", "items": b}},
                "threads": {"p1": thread_payload("p1", a + b)}, "links": [], "comments": {}, "subreddits": {},
                "health": {"accounts": {"alice": alice, "bob": bob}, "replies": replies}}

    def run_import(self, raw):
        return BI.import_export(self.conn, CFG, BI.parse_export(json.dumps(raw)), self.accounts, self.comments)

    def test_import_stores_history_visibility_and_alerts_on_change(self):
        visible = account(normal=2, anon=2)
        public = {"found": True, "body": "ok"}
        res = self.run_import(self.export(T0, visible, visible, {"a1": public, "a2": {"found": True, "body": "removed"},
                                                                  "b1": public}))
        self.assertEqual(res.new_problems, [])
        vis = dict(self.conn.execute("SELECT comment_id, visibility FROM replies").fetchall())
        self.assertEqual(vis, {"a1": H.PUBLIC, "a2": H.REMOVED, "b1": H.PUBLIC})

        # next day bob is shadowbanned: his reply becomes not visible and an alert is raised
        res = self.run_import(self.export(T0 + DAY, visible, account(404), {"a1": public, "a2": public,
                                                                             "b1": {"found": True, "body": "removed"}}))
        self.assertEqual([p["username"] for p in res.new_problems], ["bob"])
        self.assertTrue(res.warnings[0].startswith("u/bob changed from Visible to Shadowbanned"))
        vis = dict(self.conn.execute("SELECT comment_id, visibility FROM replies").fetchall())
        self.assertEqual(vis["b1"], H.NOT_VISIBLE)
        hist = H.load_history(self.conn)
        self.assertEqual(len(hist), 4)

        # importing the same file again adds no history rows and no repeat alert data
        self.run_import(self.export(T0 + DAY, visible, account(404), {}))
        self.assertEqual(len(H.load_history(self.conn)), 4)

    def test_older_import_does_not_overwrite_newer_visibility(self):
        visible = account(normal=1, anon=1)
        self.run_import(self.export(T0 + DAY, visible, visible, {"a1": {"found": True, "body": "removed"}}))
        self.run_import(self.export(T0, visible, visible, {"a1": {"found": True, "body": "ok"}}))
        self.assertEqual(self.conn.execute("SELECT visibility FROM replies WHERE comment_id='a1'").fetchone()[0],
                         H.REMOVED)

    def test_failed_check_keeps_last_known_shadowban_for_replies(self):
        self.run_import(self.export(T0, account(normal=1, anon=1), account(404), {}))
        self.run_import(self.export(T0 + DAY, account(normal=1, anon=1), account(error="blocked"),
                                    {"b1": {"found": True, "body": "ok"}}))
        self.assertEqual(self.conn.execute("SELECT visibility FROM replies WHERE comment_id='b1'").fetchone()[0],
                         H.NOT_VISIBLE)
        self.assertEqual(H.current_status(H.load_history(self.conn))["bob"]["status"], H.SHADOWBANNED)

    def test_export_without_health_warns_about_old_bookmark(self):
        raw = self.export(T0, {}, {}, {})
        del raw["health"]
        res = self.run_import(raw)
        self.assertTrue(any("no account health checks" in w for w in res.warnings))


class ReportHealthTests(unittest.TestCase):
    def test_health_section_in_both_versions(self):
        replies = replies_df([
            {"comment_id": "a", "post_id": "p1", "created_utc": MON + DAY, "visibility": H.PUBLIC},
            {"comment_id": "b", "post_id": "p2", "created_utc": MON + DAY, "account": "bob",
             "visibility": H.NOT_VISIBLE},
            {"comment_id": "c", "post_id": "p3", "created_utc": MON + DAY, "visibility": H.REMOVED}])
        snaps = snaps_df([("a", MON + 2 * DAY, 5, 1, 1, 10), ("b", MON + 2 * DAY, 1, 0, 1, 10),
                          ("c", MON + 2 * DAY, 1, 0, 1, 10)])
        hist = pd.DataFrame([("alice", MON - DAY, H.VISIBLE, "ok"), ("bob", MON - DAY, H.VISIBLE, "ok"),
                             ("alice", MON + 3 * DAY, H.VISIBLE, "ok"),
                             ("bob", MON + 3 * DAY, H.SHADOWBANNED, "Profile returns 'not found'")],
                            columns=["username", "checked_at", "status", "reason"])
        now = MON + 5 * DAY
        for build in (R.build_report, R.build_manager_report):
            text = build(replies, snaps, MON, now, IST, history=hist, cfg=CFG)
            self.assertIn("*Account health*", text)
            self.assertIn("• u/alice: Visible", text)
            self.assertIn("• u/bob: Shadowbanned or deleted (since 22 Oct)", text)
            self.assertIn("• Changed this week: u/bob Visible → Shadowbanned or deleted (22 Oct)", text)
            self.assertIn("• Hidden replies: 2 not visible to the public (1 not visible publicly, "
                          "1 removed by moderators)", text)

    def test_health_section_without_checks(self):
        text = R.build_manager_report(replies_df([{"comment_id": "a", "created_utc": MON + DAY}]),
                                      snaps_df([]), MON, MON + 5 * DAY, IST)
        self.assertIn("No account health checks yet", text)


if __name__ == "__main__":
    unittest.main()
