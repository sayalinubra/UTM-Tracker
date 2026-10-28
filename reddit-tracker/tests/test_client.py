import unittest

from tests.fake_reddit import (FakeReddit, FakeResponse, comment, listing, make_client, ok,
                               replies_tree, thread_payload)
from tracker.reddit_client import (Blocked, NotFound, RedditError, count_direct_replies,
                                   detect_status)


class ClientTests(unittest.TestCase):
    def test_pagination_follows_after(self):
        fake = FakeReddit()
        fake.add("/user/bob/comments.json", ok(listing([comment("c1", "p1"), comment("c2", "p1")], after="t1_c2")))
        fake.add("/user/bob/comments.json?after=t1_c2", ok(listing([comment("c3", "p2")], after=None)))
        client, _ = make_client(fake)
        ids = [c["id"] for c in client.user_comments("bob")]
        self.assertEqual(ids, ["c1", "c2", "c3"])
        self.assertEqual(fake.calls[0][1]["limit"], 100)
        self.assertEqual(fake.calls[1][1]["after"], "t1_c2")

    def test_user_agent_and_pacing(self):
        fake = FakeReddit()
        fake.add("/user/bob/comments.json", ok(listing([comment("c1", "p1")], after="x")))
        fake.add("/user/bob/comments.json?after=x", ok(listing([comment("c2", "p1")])))
        client, clock = make_client(fake)
        list(client.user_comments("bob"))
        self.assertEqual(fake.calls[0][2]["User-Agent"], "test-agent")
        self.assertEqual(clock.sleeps, [2.0])  # second request waited for the interval

    def test_429_backoff_honours_retry_after_then_succeeds(self):
        fake = FakeReddit()
        fake.add("/user/bob/comments.json", [
            FakeResponse(429, {}, {"Retry-After": "7"}),
            FakeResponse(429, {}),
            ok(listing([comment("c1", "p1")]))])
        client, clock = make_client(fake)
        self.assertEqual([c["id"] for c in client.user_comments("bob")], ["c1"])
        self.assertIn(7.0, clock.sleeps)
        self.assertIn(10, clock.sleeps)  # 5 * 2**1 exponential fallback
        self.assertEqual(len(fake.calls), 3)

    def test_429_gives_up_after_max_retries(self):
        fake = FakeReddit()
        fake.add("/user/bob/comments.json", FakeResponse(429, {}))
        client, _ = make_client(fake, max_retries=2)
        with self.assertRaises(RedditError):
            list(client.user_comments("bob"))
        self.assertEqual(len(fake.calls), 3)

    def test_404_raises_not_found(self):
        client, _ = make_client(FakeReddit())
        with self.assertRaises(NotFound):
            list(client.user_comments("ghost"))

    def test_network_block_page_is_not_mistaken_for_suspension(self):
        fake = FakeReddit()
        fake.add("/user/bob/comments.json", FakeResponse(403, None, {"content-type": "text/html; charset=utf-8"}))
        client, _ = make_client(fake)
        with self.assertRaises(Blocked):
            list(client.user_comments("bob"))

    def test_html_instead_of_json(self):
        fake = FakeReddit()
        fake.add("/user/bob/comments.json", FakeResponse(200, None))
        client, _ = make_client(fake)
        with self.assertRaises(RedditError):
            list(client.user_comments("bob"))

    def test_comment_with_thread(self):
        fake = FakeReddit()
        c = comment("c1", "p1", replies=replies_tree(2, more_ids=("x", "y", "z")))
        fake.add("/comments/p1/_/c1.json", ok(thread_payload("p1", c, score=55, num_comments=80)))
        client, _ = make_client(fake)
        thread, found = client.comment_with_thread("p1", "c1")
        self.assertEqual(thread["num_comments"], 80)
        self.assertEqual(count_direct_replies(found), 5)


class StatusTests(unittest.TestCase):
    def test_status_detection(self):
        self.assertEqual(detect_status({"author": "bob", "body": "hi"}), "live")
        self.assertEqual(detect_status({"author": "[deleted]", "body": "[deleted]"}), "deleted")
        self.assertEqual(detect_status({"author": "[deleted]", "body": "[removed]"}), "removed")
        self.assertEqual(detect_status({"author": "bob", "body": "[removed]"}), "removed")
        self.assertEqual(detect_status(None), "removed")

    def test_replies_count_handles_empty(self):
        self.assertEqual(count_direct_replies({"replies": ""}), 0)


if __name__ == "__main__":
    unittest.main()
