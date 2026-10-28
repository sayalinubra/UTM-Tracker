"""Parsing of accounts.txt and comments.txt."""
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
# /r/<sub>/comments/<post>/<slug>/<comment>  or  /comments/<post>/<slug>/<comment>
# also new-style /r/<sub>/comments/<post>/comment/<comment>
COMMENT_PATH_RE = re.compile(
    r"^(?:/r/(?P<sub>[^/]+))?/comments/(?P<post>[a-z0-9]+)(?:/(?P<slug>[^/]*))?(?:/(?P<comment>[a-z0-9]+))?/?$",
    re.IGNORECASE,
)
SHARE_PATH_RE = re.compile(r"^/r/[^/]+/s/[A-Za-z0-9]+/?$")
REDDIT_HOSTS = ("reddit.com", "redd.it")


@dataclass
class AccountsResult:
    usernames: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class CommentLink:
    raw: str
    post_id: str = ""
    comment_id: str = ""
    needs_resolving: bool = False  # share-style link; resolved during sync


@dataclass
class CommentsResult:
    links: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _content_lines(text):
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        yield lineno, line


def parse_accounts(text):
    """Return usernames (deduplicated, original casing) plus warnings.

    Anything after a ',' or ':' is discarded and never returned or logged.
    """
    result = AccountsResult()
    seen = set()
    for lineno, line in _content_lines(text):
        name = line
        if "," in name or ":" in name:
            name, rest = (p.strip() for p in re.split(r"[,:]", name, maxsplit=1))
            if rest:
                result.warnings.append(
                    f"accounts.txt line {lineno} has extra text after the username (it looks like a "
                    f"password). Only '{name}' was used and nothing else was stored. "
                    f"Please delete the rest of that line from the file."
                )
        name = re.sub(r"^/?u/", "", name, flags=re.IGNORECASE).strip().strip("/")
        if not name:
            continue
        if not USERNAME_RE.match(name):
            result.warnings.append(
                f"accounts.txt line {lineno}: '{name}' doesn't look like a valid Reddit username, skipped."
            )
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        result.usernames.append(name)
    return result


def parse_comment_link(raw):
    """Parse one comment URL. Returns (CommentLink or None, warning or None)."""
    url = raw.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in REDDIT_HOSTS):
        return None, f"'{raw}' isn't a Reddit link, skipped."
    path = re.sub(r"/{2,}", "/", parsed.path)
    if SHARE_PATH_RE.match(path):
        return CommentLink(raw=raw, needs_resolving=True), None
    m = COMMENT_PATH_RE.match(path)
    if not m:
        return None, f"'{raw}' isn't a Reddit comment link, skipped."
    comment = m.group("comment")
    if m.group("slug") == "comment" and not comment:
        comment = None
    if not comment:
        return None, (f"'{raw}' is a link to a whole thread, not to one comment. "
                      f"Open your reply, click Share > Copy link, and paste that instead.")
    return CommentLink(raw=raw, post_id=m.group("post").lower(), comment_id=comment.lower()), None


def parse_comments(text):
    result = CommentsResult()
    seen = set()
    for lineno, line in _content_lines(text):
        link, warning = parse_comment_link(line)
        if warning:
            result.warnings.append(f"comments.txt line {lineno}: {warning}")
            continue
        key = link.comment_id or link.raw
        if key in seen:
            continue
        seen.add(key)
        result.links.append(link)
    return result


def read_text(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except FileNotFoundError:
        return ""
