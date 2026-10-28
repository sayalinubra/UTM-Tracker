"""Runs the real dashboard headless (Streamlit AppTest) on a seeded temporary database. No network."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.fake_reddit import FakeReddit, add_thread, comment, listing, make_client, ok, tree
from tracker import config as C
from tracker import db
from tracker.sync import refresh, sync

ROOT = Path(__file__).resolve().parent.parent
DAY = 86400


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        d = Path(cls.tmp)
        cls.saved = (C.DB_FILE, C.ACCOUNTS_FILE, C.COMMENTS_FILE, C.REPORTS_DIR)
        C.DB_FILE, C.ACCOUNTS_FILE, C.COMMENTS_FILE, C.REPORTS_DIR = (
            d / "t.db", d / "accounts.txt", d / "comments.txt", d / "reports")
        C.ACCOUNTS_FILE.write_text("alice\n", encoding="utf-8")
        C.COMMENTS_FILE.write_text("", encoding="utf-8")

        now = time.time()
        fake = FakeReddit()
        client, _ = make_client(fake, interval=0)
        kids = tree(comment("k1", "p1", author="op_user", body="thanks, helpful", parent="t1_c1"))
        top = comment("c1", "p1", author="alice", body="Try Nubra's API (I work at Nubra)", score=12,
                      created=now - 3 * DAY, replies=kids)
        other = comment("o1", "p1", author="bob", body="zerodha works too", score=3, created=now - 3 * DAY)
        nested = comment("c2", "p2", author="alice", body="backtest first", score=4, created=now - DAY,
                         parent="t1_x9", sub="IndiaAlgoTrading")
        fake.add("/user/alice/comments.json", ok(listing([nested, top])))
        add_thread(fake, "p1", [top, other], permalinks=[top], created=now - 3 * DAY - 3600, author="op_user")
        add_thread(fake, "p2", [comment("x9", "p2", author="eve", sub="IndiaAlgoTrading")], permalinks=[nested],
                   sub="IndiaAlgoTrading", created=now - DAY - 7200)
        fake.add("/r/IndianStockMarket/about.json", ok({"data": {"subscribers": 1_200_000}}))
        fake.add("/r/IndiaAlgoTrading/about.json", ok({"data": {"subscribers": 40_000}}))
        conn = db.connect(C.DB_FILE)
        sync(conn, client, C.load_config(), C.ACCOUNTS_FILE, C.COMMENTS_FILE, now=now - 2 * DAY)
        refresh(conn, client, C.load_config(), C.ACCOUNTS_FILE, C.COMMENTS_FILE, now=now - 60)
        # two health checks: alice went from Visible to Possibly hidden; c2 isn't visible publicly
        db.add_account_health(conn, "alice", now - 2 * DAY, "visible", "ok", 2, 2)
        db.add_account_health(conn, "alice", now - DAY, "possibly_hidden", "Only 0 of 4 recent comments show", 4, 0)
        db.set_visibility(conn, "c1", "public", "Visible to logged-out visitors", now - DAY)
        db.set_visibility(conn, "c2", "not_visible", "Missing from what logged-out visitors see", now - DAY)
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        C.DB_FILE, C.ACCOUNTS_FILE, C.COMMENTS_FILE, C.REPORTS_DIR = cls.saved
        # the dashboard keeps its cached SQLite connection open, which Windows won't let us delete
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_app(self, **state):
        at = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60)
        for key, value in state.items():
            at.session_state[key] = value
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    @staticmethod
    def page(at):
        return "\n".join(md.value for md in at.markdown)

    def test_default_view(self):
        page = self.page(self.run_app())
        for text in ("Activity over time", "<th>Date</th>", "this week</div>", "What works best",
                     "Top-comment rate", "OP engagement rate", "Positive reply rate",
                     "Nubra share of broker mentions", "Conversation share"):
            self.assertIn(text, page)
        self.assertIn("Top comment</span>", page)
        self.assertIn("Nested reply</span>", page)
        self.assertIn("OP replied", page)

    def test_manager_view_hides_activity_dates_and_weekly_changes(self):
        at = self.run_app(manager_view=True)
        page = self.page(at)
        self.assertNotIn("Activity over time", page)
        self.assertNotIn("<th>Date</th>", page)
        self.assertNotIn("this week</div>", page)
        self.assertNotIn("vs last week</div>", page)
        for text in ("Replies posted", "Total upvotes", "Avg upvotes per reply", "Thread reach",
                     "Top-comment rate", "What works best", "Manager view"):
            self.assertIn(text, page)
        self.assertIn("(summary)", at.code[0].value)  # manager version of the weekly report

    def test_presentation_mode_turns_manager_view_on_and_restores_it(self):
        at = self.run_app()
        self.assertFalse(at.session_state["manager_view"])
        at.toggle(key="presentation").set_value(True).run()
        self.assertTrue(at.session_state["manager_view"])
        self.assertNotIn("Activity over time", self.page(at))
        next(b for b in at.button if b.label == "Exit presentation").click().run()
        self.assertFalse(at.session_state["presentation"])
        self.assertFalse(at.session_state["manager_view"])

    def test_browser_sync_is_the_default_and_direct_sync_is_still_available(self):
        at = self.run_app()
        labels = [b.label for b in at.button]
        self.assertNotIn("Sync now", labels)
        self.assertEqual(at.radio(key="sync_mode_radio").value, "browser")
        self.assertTrue(any(u.label.startswith("Import browser export") for u in at.get("file_uploader")))
        bookmark = [e for e in at.get("html") if "nb-bookmarklet" in str(e.proto)]
        self.assertTrue(bookmark and "javascript:" in str(bookmark[0].proto))
        at.radio(key="sync_mode_radio").set_value("direct").run()
        self.assertIn("Sync now", [b.label for b in at.button])
        at.radio(key="sync_mode_radio").set_value("browser").run()  # leave the default for other tests

    def test_account_health_panel_banner_and_visibility(self):
        at = self.run_app()
        page = self.page(at)
        self.assertIn("⚠ Account visibility problem since the previous import", page)
        self.assertIn("<b>u/alice</b>: Visible → Possibly hidden", page)
        self.assertIn('class="badge solid-warn">Possibly hidden</span>', page)  # sidebar panel
        self.assertIn("Since ", page)
        self.assertIn("Not visible publicly</span>", page)                     # table column
        self.assertIn("1 reply not visible to the public: 1 not visible publicly", page)  # KPI card
        self.assertIn("Account health history", page)
        self.assertIn("First check → <span class='badge solid-pos'>Visible</span>", page)
        self.assertIn("*Account health*", at.code[0].value)
        # the visibility filter narrows the replies
        at.multiselect(key="f_vis").set_value(["Not visible publicly"]).run()
        self.assertIn("Showing 1 of 1 replies", " ".join(c.value for c in at.caption))
        at.multiselect(key="f_vis").set_value([]).run()

    def test_detailed_report_available_outside_manager_view(self):
        at = self.run_app()
        self.assertNotIn("(summary)", at.code[0].value)
        at.radio[0].set_value("Manager version").run()
        self.assertIn("(summary)", at.code[0].value)


if __name__ == "__main__":
    unittest.main()
