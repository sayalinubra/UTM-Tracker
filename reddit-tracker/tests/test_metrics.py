import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from tracker import metrics as m
from tracker import report as R

IST = 5.5
DAY = 86400
# Monday 2026-10-19 00:00 IST
MON = datetime(2026, 10, 19, tzinfo=m.tz(IST)).timestamp()


def replies_df(rows):
    base = {"post_id": None, "account": "alice", "permalink": "https://r/x", "subreddit": "r/a",
            "thread_title": "T", "thread_link": "https://r/t", "text": "t", "status": "live", "note": None,
            "from_account": 1, "from_link": 0, "parent_id": None, "depth": 0, "thread_created_utc": None,
            "thread_author": "op", "op_engaged": 0, "mentions_nubra": False, "disclosed": False,
            "topic": "Other"}
    return pd.DataFrame([{**base, **r} for r in rows])


def snaps_df(rows):
    """rows: (comment_id, taken_at, score, replies, thread_score, thread_comments[, rank, top_count, ratio, op])"""
    full = [tuple(r) + (None,) * (len(m.SNAP_COLUMNS) - len(r)) for r in rows]
    return pd.DataFrame(full, columns=m.SNAP_COLUMNS).sort_values("taken_at")


def responses_df(rows):
    return pd.DataFrame(rows, columns=m.RESPONSE_COLUMNS)


def scans_df(rows):
    return pd.DataFrame(rows, columns=m.SCAN_COLUMNS).sort_values("scanned_at")


class WeekTests(unittest.TestCase):
    def test_week_start_is_monday_ist(self):
        wed_evening = MON + 2 * DAY + 20 * 3600
        self.assertEqual(m.week_start(wed_evening, IST), MON)
        self.assertEqual(m.week_start(MON, IST), MON)
        self.assertEqual(m.week_start(MON - 1, IST), MON - 7 * DAY)
        # Sunday 23:00 UTC is already Monday 04:30 IST
        sunday_utc = datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(m.week_start(sunday_utc, IST), MON + 7 * DAY)


class Fixture:
    """c1: posted last week (top-level, #1). c2: posted this week (top-level, #3). n1: nested, this week."""

    def __init__(self):
        self.now = MON + 7 * DAY + 3 * DAY
        self.replies = replies_df([
            {"comment_id": "c1", "post_id": "p1", "created_utc": MON + DAY, "subreddit": "r/a",
             "mentions_nubra": True, "disclosed": True, "topic": "Algo trading",
             "thread_created_utc": MON + DAY - 3600},
            {"comment_id": "c2", "post_id": "p1", "created_utc": MON + 8 * DAY, "subreddit": "r/b",
             "mentions_nubra": True, "disclosed": False, "topic": "Market data",
             "thread_created_utc": MON + 7 * DAY},
            {"comment_id": "n1", "post_id": "p2", "created_utc": MON + 9 * DAY, "subreddit": "r/b",
             "depth": 1, "thread_created_utc": MON + 9 * DAY - 5 * 3600},
        ])
        self.snaps = snaps_df([
            ("c1", MON + 2 * DAY, 5, 1, 10, 30, 2, 10, 0.9, 0),
            ("c1", MON + 7 * DAY - 10, 8, 2, 12, 35, 1, 12, 0.9, 1),
            ("c1", MON + 9 * DAY, 15, 4, 20, 50, 1, 15, 0.9, 0),
            ("c2", MON + 9 * DAY, 6, 1, 20, 50, 3, 15, 0.9, 0),
            ("n1", MON + 9 * DAY + 60, 2, 0, 4, 8, None, None, 0.7, 1),
        ])
        self.responses = responses_df([
            ("k1", "c1", "bob", "thanks", MON + 2 * DAY, "positive"),
            ("k2", "c1", "eve", "spam", MON + 8 * DAY, "negative"),
            ("k3", "c2", "joe", "ok", MON + 9 * DAY, "neutral"),
            ("k4", "c2", "amy", "helpful", MON + 9 * DAY, "positive"),
        ])
        self.scans = scans_df([
            ("p1", MON + 2 * DAY, 10, {"Nubra": 1, "Zerodha": 3}),
            ("p1", MON + 9 * DAY, 12, {"Nubra": 2, "Zerodha": 4}),
            ("p2", MON + 9 * DAY, 5, {"Dhan": 2}),
        ])


class KpiTests(unittest.TestCase):
    def setUp(self):
        f = Fixture()
        self.f = f
        # the original two-reply set (nested reply excluded) keeps the original expectations
        self.replies = f.replies[f.replies["comment_id"] != "n1"]
        self.snaps = f.snaps[f.snaps["comment_id"] != "n1"]
        self.now = f.now

    def test_values_at_uses_latest_snapshot_before_t(self):
        v = m.values_at(self.replies, self.snaps, MON + 7 * DAY, "score")
        self.assertEqual(v.to_dict(), {"c1": 8.0})

    def test_values_at_falls_back_to_earliest_snapshot(self):
        v = m.values_at(self.replies, self.snaps, MON + 1.5 * DAY, "score")
        self.assertEqual(v.to_dict(), {"c1": 5.0})

    def test_values_at_max(self):
        v = m.values_at(self.replies, self.snaps, self.now, "op_engaged", how="max")
        self.assertEqual(v.to_dict(), {"c1": 1.0, "c2": 0.0})

    def test_kpis_and_weekly_change(self):
        k = m.kpis_with_change(self.replies, self.snaps, self.now, IST)
        self.assertEqual(k["replies_posted"], (2, 1))
        self.assertEqual(k["total_upvotes"], (21.0, 13.0))
        self.assertEqual(k["avg_upvotes"], (10.5, 2.5))
        self.assertEqual(k["replies_received"], (5.0, 3.0))
        self.assertEqual(k["thread_reach"], (50.0, 15.0))  # same thread counted once
        self.assertEqual(k["disclosure_rate"], (50.0, -50.0))

    def test_disclosure_none_without_mentions(self):
        r = self.replies.assign(mentions_nubra=False)
        k = m.kpis_with_change(r, self.snaps, self.now, IST)
        self.assertEqual(k["disclosure_rate"], (None, None))

    def test_weekly_activity(self):
        w = m.weekly_activity(self.replies, self.snaps, self.now, IST)
        self.assertEqual(list(w["replies_posted"]), [1, 1])
        self.assertEqual(list(w["upvotes_gained"]), [8.0, 13.0])
        self.assertEqual(str(w["week"].iloc[0]), "2026-10-19")

    def test_by_group(self):
        g = m.by_group(self.replies, self.snaps, "subreddit")
        self.assertEqual(list(g["subreddit"]), ["r/a", "r/b"])
        self.assertEqual(list(g["avg_upvotes"]), [15.0, 6.0])

    def test_distinct_snapshot_times(self):
        self.assertEqual(m.distinct_snapshot_times(self.snaps), 3)
        self.assertEqual(m.distinct_snapshot_times(snaps_df([])), 0)

    def test_empty_inputs(self):
        empty = pd.DataFrame(columns=m.REPLY_COLUMNS)
        k = m.kpis_with_change(empty, snaps_df([]), self.now, IST)
        self.assertEqual(k["replies_posted"], (0, 0))
        self.assertIsNone(k["top_comment_rate"][0])
        self.assertTrue(m.weekly_activity(empty, snaps_df([]), self.now, IST).empty)


class NewMetricTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def test_top_comment_rate_uses_top_level_only(self):
        f = self.f
        # now: c1 #1, c2 #3, n1 nested (ignored) -> 1 of 2 top-level
        self.assertEqual(m.top_comment_rate_at(f.replies, f.snaps, f.now), 50.0)
        # at week start: only c1 existed, ranked #1 at that time
        self.assertEqual(m.top_comment_rate_at(f.replies, f.snaps, MON + 7 * DAY), 100.0)

    def test_op_engagement_rate_is_sticky(self):
        f = self.f
        # c1 had OP engagement in an earlier snapshot, n1 has it now, c2 never -> 2 of 3
        self.assertAlmostEqual(m.op_engagement_rate_at(f.replies, f.snaps, f.now), 200 / 3)

    def test_positive_reply_rate(self):
        f = self.f
        self.assertEqual(m.positive_reply_rate_at(f.replies, f.responses, f.now), 50.0)
        self.assertEqual(m.positive_reply_rate_at(f.replies, f.responses, MON + 7 * DAY), 100.0)
        self.assertIsNone(m.positive_reply_rate_at(f.replies, m.empty_responses(), f.now))

    def test_brand_mentions_and_nubra_share(self):
        f = self.f
        self.assertEqual(m.brand_mentions_at(f.replies, f.scans, f.now), {"Nubra": 2, "Zerodha": 4, "Dhan": 2})
        self.assertEqual(m.nubra_share_at(f.replies, f.scans, f.now), 25.0)
        # as of week start only p1's first scan counts
        self.assertEqual(m.nubra_share_at(f.replies, f.scans, MON + 7 * DAY), 25.0)
        self.assertIsNone(m.nubra_share_at(f.replies, m.empty_scans(), f.now))

    def test_kpis_include_new_cards(self):
        f = self.f
        k = m.kpis_with_change(f.replies, f.snaps, f.now, IST, f.responses, f.scans)
        self.assertEqual(k["top_comment_rate"], (50.0, -50.0))
        self.assertEqual(k["positive_reply_rate"], (50.0, -50.0))

    def test_velocity_24h(self):
        f = self.f
        snaps = pd.concat([f.snaps, snaps_df([("c2", MON + 8 * DAY + 3600, 4, 0, 1, 1),
                                              ("c2", MON + 8 * DAY + 25 * 3600, 99, 0, 1, 1)])])
        v = m.velocity_24h(f.replies, snaps.sort_values("taken_at"))
        # latest score within 24h of posting (inclusive); c2's 99 at +25h is outside the window
        self.assertEqual(v.to_dict(), {"c1": 5.0, "c2": 6.0, "n1": 2.0})

    def test_timing_and_size_buckets(self):
        self.assertEqual([m.timing_bucket(h) for h in (0.5, 2, 11.9, 12, None)],
                         ["Under 2h", "2–12h", "2–12h", "12h+", None])
        buckets = [[0, "Small"], [100_000, "Medium"], [1_000_000, "Large"]]
        self.assertEqual([m.size_bucket(s, buckets) for s in (5, 100_000, 2_500_000, None)],
                         ["Small", "Medium", "Large", None])

    def test_latest_stats_and_works_best(self):
        f = self.f
        stats = m.latest_stats(f.replies, f.snaps, f.responses)
        by_id = stats.set_index("comment_id")
        self.assertTrue(pd.isna(by_id.loc["n1", "thread_rank"]))  # nested: no rank
        self.assertEqual(by_id.loc["c1", "timing"], "Under 2h")
        self.assertEqual(by_id.loc["n1", "timing"], "2–12h")
        self.assertEqual(by_id.loc["c2", "resp_positive"], 1)
        self.assertTrue(by_id.loc["c1", "op_replied"])
        timing = m.works_best(stats, "timing", m.TIMING_BUCKETS)
        self.assertEqual(list(timing["timing"]), ["Under 2h", "2–12h", "12h+"])
        self.assertEqual(list(timing["avg_upvotes"]), [15.0, 2.0, 6.0])
        depth = m.works_best(stats, "depth_label", list(m.DEPTH_LABELS.values()))
        self.assertEqual(dict(zip(depth["depth_label"], depth["replies"])),
                         {"Top-level comment": 2, "Nested reply": 1})


class ReportTests(unittest.TestCase):
    def setUp(self):
        f = Fixture()
        self.f = f
        self.replies = f.replies[f.replies["comment_id"] != "n1"]
        self.snaps = f.snaps[f.snaps["comment_id"] != "n1"]
        self.now = f.now

    def test_week_stats(self):
        s = R.week_stats(self.replies, self.snaps, MON + 7 * DAY, self.now)
        self.assertEqual(s["replies_posted"], 1)
        self.assertEqual(s["upvotes_gained"], 13.0)
        self.assertEqual(s["best_reply"]["comment_id"], "c1")  # +7 beats c2's +6
        self.assertEqual([b["comment_id"] for b in s["best_replies"]], ["c1", "c2"])
        self.assertEqual(s["best_subreddit"]["name"], "r/b")
        self.assertEqual(s["disclosure_rate"], 0.0)

    def test_report_text_and_changes(self):
        text = R.build_report(self.replies, self.snaps, MON + 7 * DAY, self.now, IST)
        self.assertIn("so far", text)
        self.assertIn("Replies posted: 1 (no change vs last week)", text)
        self.assertIn("Upvotes gained: 13 (+5 vs last week)", text)
        self.assertIn("Disclosure rate: 0% of 1 Nubra-mentioning reply (-100 pts vs last week)", text)
        self.assertIn("*Quality of this week's replies*", text)

    def test_manager_report_compares_only_quality(self):
        f = self.f
        text = R.build_manager_report(f.replies, f.snaps, MON + 7 * DAY, f.now, IST, f.responses, f.scans)
        lines = text.splitlines()
        totals = [l for l in lines if l.startswith(("• Replies:", "• Upvotes gained", "• Replies received"))]
        self.assertEqual(len(totals), 3)
        self.assertTrue(all("vs last week" not in l for l in totals))
        self.assertIn("• Replies: 2", text)
        # quality metrics for replies posted this week (c2 #3, n1 nested) vs last week (c1 #1)
        self.assertIn("• Top-comment rate (top-level replies): 0% (-100 pts vs last week)", text)
        self.assertIn("• Avg upvotes per reply: 4.0 (-4.0 vs last week)", text)
        self.assertIn("*Best replies*", text)
        self.assertIn("*Best subreddits*", text)
        self.assertNotIn("Replies posted:", text)  # no posting-volume line with daily/weekly cadence

    def test_auto_report_writes_both_versions_once(self):
        with tempfile.TemporaryDirectory() as d:
            first = R.ensure_last_week_report(self.replies, self.snaps, Path(d), self.now, IST)
            self.assertEqual(sorted(p.name for p in first), ["weekly-2026-10-19-manager.md", "weekly-2026-10-19.md"])
            self.assertEqual(R.ensure_last_week_report(self.replies, self.snaps, Path(d), self.now, IST), [])


if __name__ == "__main__":
    unittest.main()
