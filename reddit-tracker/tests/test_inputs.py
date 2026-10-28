import unittest

from tracker.inputs import parse_accounts, parse_comment_link, parse_comments


class AccountsTests(unittest.TestCase):
    def test_prefixes_comments_blank_lines_and_dedupe(self):
        text = "﻿# header\n\n  u/TraderIn93  \n/u/candlesandstock\nTheFinanceGuy67\n\n#skip\ntraderin93\n"
        r = parse_accounts(text)
        self.assertEqual(r.usernames, ["TraderIn93", "candlesandstock", "TheFinanceGuy67"])
        self.assertEqual(r.warnings, [])

    def test_password_looking_lines_keep_only_username(self):
        r = parse_accounts("alice_1,hunter2\nbob-2:s3cret pass\n")
        self.assertEqual(r.usernames, ["alice_1", "bob-2"])
        self.assertEqual(len(r.warnings), 2)
        joined = " ".join(r.warnings)
        self.assertNotIn("hunter2", joined)
        self.assertNotIn("s3cret", joined)

    def test_invalid_username_skipped_with_warning(self):
        r = parse_accounts("ok_name\nhas spaces in it\nab\n")
        self.assertEqual(r.usernames, ["ok_name"])
        self.assertEqual(len(r.warnings), 2)


class CommentLinkTests(unittest.TestCase):
    def check(self, url, post, cid):
        link, warning = parse_comment_link(url)
        self.assertIsNone(warning, url)
        self.assertEqual((link.post_id, link.comment_id), (post, cid), url)

    def test_formats(self):
        self.check("https://www.reddit.com/r/NSEbets/comments/1x1hp15/options_buying/abc123/", "1x1hp15", "abc123")
        self.check("https://old.reddit.com/r/NSEbets/comments/1x1hp15/options_buying/abc123/?context=3",
                   "1x1hp15", "abc123")
        self.check("https://new.reddit.com/r/NSEbets/comments/1x1hp15/slug/abc123", "1x1hp15", "abc123")
        self.check("https://www.reddit.com/r/NSEbets/comments/1x1hp15/comment/abc123/", "1x1hp15", "abc123")
        self.check("reddit.com/comments/1x1hp15/_/ABC123", "1x1hp15", "abc123")
        self.check("https://np.reddit.com/r/x/comments/1x1hp15/slug/abc123/?utm_source=share", "1x1hp15", "abc123")

    def test_share_link_needs_resolving(self):
        link, warning = parse_comment_link("https://www.reddit.com/r/IndianStockMarket/s/AbCdEf123")
        self.assertIsNone(warning)
        self.assertTrue(link.needs_resolving)

    def test_thread_link_and_non_reddit_rejected(self):
        link, warning = parse_comment_link("https://www.reddit.com/r/NSEbets/comments/1x1hp15/options_buying/")
        self.assertIsNone(link)
        self.assertIn("whole thread", warning)
        link, warning = parse_comment_link("https://example.com/r/x/comments/a/b/c")
        self.assertIsNone(link)

    def test_parse_comments_file_dedupes(self):
        text = ("# comment\n\nhttps://www.reddit.com/r/a/comments/p1/s/c1/\n"
                "https://old.reddit.com/r/a/comments/p1/s/c1/?context=3\n"
                "https://www.reddit.com/r/a/comments/p1/s/\n")
        r = parse_comments(text)
        self.assertEqual([l.comment_id for l in r.links], ["c1"])
        self.assertEqual(len(r.warnings), 1)


if __name__ == "__main__":
    unittest.main()
