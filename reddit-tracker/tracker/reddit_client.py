"""Minimal client for Reddit's public JSON endpoints (no login, no API keys)."""
import time

import requests

BASE = "https://www.reddit.com"


class RedditError(Exception):
    """A request failed in a way the caller should report in plain English."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NotFound(RedditError):
    pass


class Forbidden(RedditError):
    pass


class Blocked(RedditError):
    """Reddit served its HTML block page: anonymous access refused for this network."""


class RedditClient:
    def __init__(self, user_agent, interval=2.0, max_retries=5, backoff_base=5,
                 session=None, sleep=time.sleep, clock=time.monotonic):
        self.session = session or requests.Session()
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.interval = interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.sleep = sleep
        self.clock = clock
        self._last_request = None
        self.request_count = 0

    def _pace(self):
        if self._last_request is not None:
            wait = self.interval - (self.clock() - self._last_request)
            if wait > 0:
                self.sleep(wait)
        self._last_request = self.clock()

    def get(self, url, params=None):
        """GET a URL and return (json, final_url). Retries 429/5xx with backoff."""
        params = dict(params or {})
        params.setdefault("raw_json", 1)
        attempt = 0
        while True:
            self._pace()
            self.request_count += 1
            try:
                resp = self.session.get(url, params=params, headers=self.headers,
                                        timeout=30, allow_redirects=True)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RedditError(f"Couldn't reach Reddit ({exc.__class__.__name__}). "
                                      f"Check your internet connection.") from exc
                self.sleep(self.backoff_base * (2 ** attempt))
                attempt += 1
                continue
            status = resp.status_code
            if status == 429 or status >= 500:
                if attempt >= self.max_retries:
                    raise RedditError(
                        "Reddit is rate-limiting us right now. Try again in a few minutes."
                        if status == 429 else f"Reddit returned a server error ({status}).",
                        status)
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = self.backoff_base * (2 ** attempt)
                self.sleep(delay)
                attempt += 1
                continue
            if status == 404:
                raise NotFound("Not found on Reddit.", status)
            if status == 403 and "text/html" in (resp.headers.get("content-type") or "").lower():
                raise Blocked("Reddit is blocking logged-out requests from this internet connection "
                              "(it returned its 'blocked' page, not data). This isn't about your accounts. "
                              "Try again later or from a different network.", status)
            if status == 403:
                raise Forbidden("Reddit refused access (private, banned or suspended).", status)
            if status != 200:
                raise RedditError(f"Reddit returned an unexpected response ({status}).", status)
            try:
                return resp.json(), getattr(resp, "url", url)
            except ValueError as exc:
                raise RedditError("Reddit sent back a page instead of data "
                                  "(it may be blocking automated requests).", status) from exc

    # --- endpoints -------------------------------------------------------

    def user_comments(self, username, max_pages=10, stop_when=None):
        """Yield comment dicts from a user's public comment history, newest first.

        stop_when(page_items) -> True stops pagination after that page.
        """
        after = None
        for _ in range(max_pages):
            params = {"limit": 100}
            if after:
                params["after"] = after
            data, _ = self.get(f"{BASE}/user/{username}/comments.json", params)
            listing = data.get("data", {}) if isinstance(data, dict) else {}
            items = [c["data"] for c in listing.get("children", []) if c.get("kind") == "t1"]
            yield from items
            after = listing.get("after")
            if not after or not items or (stop_when and stop_when(items)):
                break

    def user_about(self, username):
        data, _ = self.get(f"{BASE}/user/{username}/about.json")
        return data.get("data", {})

    def comment_with_thread(self, post_id, comment_id, permalink=None):
        """Return (thread_dict, comment_dict_or_None) for one comment."""
        path = permalink or f"/comments/{post_id}/_/{comment_id}/"
        url = BASE + path.rstrip("/") + ".json"
        data, _ = self.get(url, {"depth": 2, "limit": 100})
        if not isinstance(data, list) or len(data) < 2:
            raise RedditError("Unexpected response shape for a comment.")
        thread_children = data[0].get("data", {}).get("children", [])
        thread = thread_children[0]["data"] if thread_children else {}
        comment = None
        for child in data[1].get("data", {}).get("children", []):
            if child.get("kind") == "t1" and child["data"].get("id") == comment_id:
                comment = child["data"]
                break
        return thread, comment

    def thread(self, post_id, limit=500, depth=8):
        """Whole thread, top-sorted: raw [post listing, comments listing]."""
        data, _ = self.get(f"{BASE}/comments/{post_id}.json",
                           {"sort": "top", "limit": limit, "depth": depth})
        if not isinstance(data, list) or len(data) < 2:
            raise RedditError("Unexpected response shape for a thread.")
        return data

    def subreddit_about(self, name):
        data, _ = self.get(f"{BASE}/r/{name}/about.json")
        return data.get("data", {}) if isinstance(data, dict) else {}

    def resolve_share_link(self, url):
        """Follow a share link (/r/<sub>/s/<code>) and return the final URL."""
        self._pace()
        self.request_count += 1
        try:
            resp = self.session.get(url, headers=self.headers, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            raise RedditError("Couldn't open the share link.") from exc
        return getattr(resp, "url", url)


class ThreadScan:
    """Parsed thread: post data, every visible comment by id, and top-level ranks by score."""

    def __init__(self, data):
        children = data[0].get("data", {}).get("children", [])
        self.post = children[0]["data"] if children else {}
        self.comments = {}
        top_level, self.hidden_top_level = [], 0

        def walk(nodes, top):
            for node in nodes:
                if node.get("kind") == "t1":
                    c = node["data"]
                    self.comments[c["id"]] = c
                    if top and not c.get("stickied"):
                        top_level.append(c)
                    replies = c.get("replies")
                    if isinstance(replies, dict):
                        walk(replies.get("data", {}).get("children", []), False)
                elif node.get("kind") == "more" and top:
                    self.hidden_top_level += len(node["data"].get("children", []))

        walk(data[1].get("data", {}).get("children", []), True)
        # stable sort: equal scores keep Reddit's own 'top' order
        ranked = sorted(top_level, key=lambda c: -(c.get("score") or 0))
        self.rank = {c["id"]: i + 1 for i, c in enumerate(ranked)}
        self.top_level_count = len(top_level) + self.hidden_top_level


def direct_replies(comment):
    """Visible direct replies (dicts) of a comment fetched with its replies."""
    replies = comment.get("replies") if comment else None
    if not isinstance(replies, dict):
        return []
    return [n["data"] for n in replies.get("data", {}).get("children", []) if n.get("kind") == "t1"]


def is_top_level(comment):
    return (comment.get("parent_id") or "").startswith("t3_")


def count_direct_replies(comment):
    """Count direct replies of a comment, including collapsed 'more' stubs."""
    replies = comment.get("replies")
    if not isinstance(replies, dict):
        return 0
    total = 0
    for child in replies.get("data", {}).get("children", []):
        if child.get("kind") == "t1":
            total += 1
        elif child.get("kind") == "more":
            # 'children' lists the hidden direct replies; 'count' would include their subtrees
            total += len(child["data"].get("children", []))
    return total


def detect_status(comment):
    """'live', 'removed' or 'deleted' from a comment dict (None = gone entirely)."""
    if comment is None:
        return "removed"
    body = (comment.get("body") or "").strip()
    author = comment.get("author")
    if author == "[deleted]" and body in ("[deleted]", "[removed]", ""):
        return "deleted" if body != "[removed]" else "removed"
    if body == "[removed]" or comment.get("removed") or comment.get("banned_by"):
        return "removed"
    if body == "[deleted]":
        return "deleted"
    return "live"
