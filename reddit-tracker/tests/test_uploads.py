import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tracker import uploads as U
from tracker.inputs import parse_accounts, parse_comments


class UsernameUploadTests(unittest.TestCase):
    def test_txt_prefixes_comments_and_passwords(self):
        text = "# my list\nu/TraderIn93\n/u/candlesandstock\n\ntraderin93\nalice,hunter2\nbob:s3cret\n"
        p = U.parse_usernames_upload("names.txt", text)
        self.assertEqual(p.values, ["TraderIn93", "candlesandstock", "alice", "bob"])
        self.assertEqual(p.duplicates_in_file, 1)
        self.assertEqual(len(p.warnings), 2)
        self.assertNotIn("hunter2", " ".join(p.warnings))
        self.assertNotIn("s3cret", " ".join(p.warnings))

    def test_header_line_with_trailing_colon_is_not_a_password(self):
        p = U.parse_usernames_upload("Account names.txt", "Account names:\nTraderIn93\n")
        self.assertEqual(p.values, ["TraderIn93"])
        self.assertFalse(any("password" in w for w in p.warnings))

    def test_csv_detects_username_column_and_ignores_password_column(self):
        text = "Username,Password,Notes\nTraderIn93,hunter2,main\nu/candlesandstock,pw2,alt\n"
        p = U.parse_usernames_upload("accounts.csv", text)
        self.assertEqual(p.column, "Username")
        self.assertEqual(p.values, ["TraderIn93", "candlesandstock"])
        self.assertNotIn("Password", p.columns)
        self.assertTrue(any("Password" in w and "never read" in w for w in p.warnings))
        self.assertNotIn("hunter2", repr(p))

    def test_csv_ambiguous_column_needs_choice(self):
        text = "user_main,user_backup\nalice,bob\n"
        p = U.parse_usernames_upload("a.csv", text)
        self.assertTrue(p.needs_column_choice)
        self.assertEqual(p.columns, ["user_main", "user_backup"])
        p2 = U.parse_usernames_upload("a.csv", text, column="user_backup")
        self.assertEqual(p2.values, ["bob"])

    def test_csv_like_the_utm_sheet(self):
        text = ("Sr. No,Reddit Posts ,Origin Of Idea,Subreddit,Answer,Assigned to Account,Status\n"
                "1,https://www.reddit.com/r/a/comments/p1/s/,Beacon,r/a,\"text, with comma\",TraderIn,Posted\n"
                "2,https://www.reddit.com/r/a/comments/p2/s/,Own,r/a,hi,TheFinanceGuy67,Posted\n")
        p = U.parse_usernames_upload("UTM.csv", text)
        self.assertEqual(p.column, "Assigned to Account")
        self.assertEqual(p.values, ["TraderIn", "TheFinanceGuy67"])

    def test_semicolon_csv(self):
        p = U.parse_usernames_upload("a.csv", "account;pass\nalice;x\nbob;y\n")
        self.assertEqual((p.column, p.values), ("account", ["alice", "bob"]))


class LinkUploadTests(unittest.TestCase):
    def test_txt_links(self):
        text = ("https://www.reddit.com/r/a/comments/p1/s/c1/\nhttps://old.reddit.com/r/a/comments/p1/s/c1/?context=3\n"
                "https://www.reddit.com/r/a/comments/p2/s/\n")
        p = U.parse_links_upload("links.txt", text)
        self.assertEqual(len(p.values), 1)
        self.assertEqual(p.duplicates_in_file, 1)
        self.assertTrue(any("whole thread" in w for w in p.warnings))

    def test_csv_autodetects_link_column(self):
        text = ("id,note,url,token\n1,a,https://www.reddit.com/r/a/comments/p1/s/c1/,abc\n"
                "2,b,https://reddit.com/r/a/comments/p2/s/c2,def\n3,c,,ghi\n")
        p = U.parse_links_upload("links.csv", text)
        self.assertEqual(p.column, "url")
        self.assertEqual(len(p.values), 2)
        self.assertNotIn("token", p.columns)

    def test_csv_without_links_needs_choice(self):
        p = U.parse_links_upload("x.csv", "a,b\n1,2\n")
        self.assertTrue(p.needs_column_choice)


class MergeAndSaveTests(unittest.TestCase):
    def test_merge_add_and_replace(self):
        r = U.merge(["alice", "Bob"], ["bob", "carol", "Carol", "dave"], "add")
        self.assertEqual(r.values, ["alice", "Bob", "carol", "dave"])
        self.assertEqual(r.added, ["carol", "dave"])
        self.assertEqual(r.duplicates, 2)  # 'bob' already there + 'Carol' repeated in the file
        r = U.merge(["alice", "Bob"], ["bob", "erin"], "replace")
        self.assertEqual(r.values, ["bob", "erin"])
        self.assertEqual(r.removed, ["alice"])
        self.assertEqual(r.added, ["erin"])

    def test_link_merge_dedupes_by_comment_id(self):
        existing = ["https://www.reddit.com/r/a/comments/p1/s/c1/"]
        r = U.merge(existing, ["https://old.reddit.com/r/a/comments/p1/x/C1/?context=3",
                               "https://www.reddit.com/r/a/comments/p2/s/c2/"], "add", U.comment_key)
        self.assertEqual(len(r.added), 1)
        self.assertEqual(r.duplicates, 1)

    def test_save_add_keeps_existing_file_and_replace_rewrites(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "accounts.txt"
            original = "# my notes\nalice\n"
            path.write_text(original, encoding="utf-8")
            when = datetime(2026, 10, 28, 9, 30)
            r = U.merge(["alice"], ["alice", "bob"], "add")
            self.assertTrue(U.save_list(path, "accounts", r, "add", original, "new.csv", when))
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# my notes\nalice\n"))
            self.assertIn("# Added from upload 'new.csv' on 2026-10-28 09:30", text)
            self.assertEqual(parse_accounts(text).usernames, ["alice", "bob"])
            # nothing new -> file untouched
            r = U.merge(["alice", "bob"], ["bob"], "add")
            self.assertFalse(U.save_list(path, "accounts", r, "add", text, "again.txt", when))
            r = U.merge(["alice", "bob"], ["zed"], "replace")
            U.save_list(path, "accounts", r, "replace", text, "only.txt", when)
            self.assertEqual(parse_accounts(path.read_text(encoding="utf-8")).usernames, ["zed"])

    def test_saved_file_never_contains_passwords(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "accounts.txt"
            p = U.parse_usernames_upload("a.csv", "user,password\nalice:topsecret,hunter2\n")
            r = U.merge([], p.values, "add")
            U.save_list(path, "accounts", r, "add", "", "a.csv")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("hunter2", text)
            self.assertNotIn("topsecret", text)
            self.assertEqual(parse_accounts(text).usernames, ["alice"])

    def test_save_comments_list(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "comments.txt"
            links = ["https://www.reddit.com/r/a/comments/p1/s/c1/"]
            U.save_list(path, "comments", U.merge([], links, "add", U.comment_key), "add", "", "l.txt")
            self.assertEqual([l.comment_id for l in parse_comments(path.read_text(encoding="utf-8")).links], ["c1"])

    def test_decode(self):
        self.assertEqual(U.decode("﻿alice".encode("utf-8")), "alice")
        self.assertEqual(U.decode("caf\xe9".encode("cp1252")), "caf\xe9")


if __name__ == "__main__":
    unittest.main()
