"""A fake requests.Session that answers like Reddit's public JSON endpoints. No network."""
from urllib.parse import urlparse

from tracker.reddit_client import RedditClient


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, url=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.url = url

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeReddit:
    """Routes are keyed by URL path (+ '?after=X' for paginated listings).

    A route value can be a FakeResponse, or a list of FakeResponses served in order
    (the last one repeats).
    """

    def __init__(self):
        self.routes = {}
        self.calls = []

    def add(self, key, response):
        self.routes[key] = response

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        path = urlparse(url).path
        key = path + (f"?after={params['after']}" if params and params.get("after") else "")
        self.calls.append((key, dict(params or {}), dict(headers or {})))
        route = self.routes.get(key, self.routes.get(path))
        if route is None:
            return FakeResponse(404, {"error": 404}, url=url)
        if isinstance(route, list):
            resp = route.pop(0) if len(route) > 1 else route[0]
        else:
            resp = route
        if not resp.url:
            resp.url = url
        return resp


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds

    def clock(self):
        return self.t


def make_client(fake, clock=None, interval=2.0, max_retries=3):
    clock = clock or FakeClock()
    return RedditClient("test-agent", interval=interval, max_retries=max_retries, backoff_base=5,
                        session=fake, sleep=clock.sleep, clock=clock.clock), clock


# --- payload builders --------------------------------------------------------

def comment(cid, post, author="TraderIn93", body="hello", score=1, sub="IndianStockMarket",
            created=1_700_000_000, title="Thread title", replies=None, parent=None, stickied=False):
    return {
        "id": cid, "link_id": "t3_" + post, "author": author, "body": body, "score": score,
        "subreddit": sub, "created_utc": created, "link_title": title,
        "parent_id": parent or "t3_" + post, "stickied": stickied,
        "permalink": f"/r/{sub}/comments/{post}/slug/{cid}/",
        "link_permalink": f"https://www.reddit.com/r/{sub}/comments/{post}/slug/",
        "replies": replies if replies is not None else "",
    }


def tree(*children, more_ids=()):
    """A replies listing made of comment dicts (plus an optional 'more' stub)."""
    nodes = [{"kind": "t1", "data": c} for c in children]
    if more_ids:
        nodes.append({"kind": "more", "data": {"count": len(more_ids), "children": list(more_ids)}})
    return {"kind": "Listing", "data": {"children": nodes}}


def listing(items, after=None):
    return {"kind": "Listing", "data": {"after": after,
                                         "children": [{"kind": "t1", "data": i} for i in items]}}


def replies_tree(n_direct, more_ids=()):
    children = [{"kind": "t1", "data": {"id": f"r{i}"}} for i in range(n_direct)]
    if more_ids:
        children.append({"kind": "more", "data": {"count": 50, "children": list(more_ids)}})
    return {"kind": "Listing", "data": {"children": children}}


def thread_payload(post, comment_data, score=10, num_comments=40, sub="IndianStockMarket",
                   title="Thread title", author="op_user", created=1_699_990_000, selftext="",
                   upvote_ratio=0.9, more_ids=()):
    """comment_data: one comment dict, a list of them, or None."""
    thread = {"id": post, "title": title, "score": score, "num_comments": num_comments,
              "subreddit": sub, "permalink": f"/r/{sub}/comments/{post}/slug/", "author": author,
              "created_utc": created, "selftext": selftext, "upvote_ratio": upvote_ratio}
    items = comment_data if isinstance(comment_data, list) else ([comment_data] if comment_data else [])
    second = [{"kind": "t1", "data": c} for c in items]
    if more_ids:
        second.append({"kind": "more", "data": {"count": len(more_ids), "children": list(more_ids)}})
    return [{"kind": "Listing", "data": {"children": [{"kind": "t3", "data": thread}]}},
            {"kind": "Listing", "data": {"children": second}}]


def add_thread(fake, post, top_level, permalinks=(), **thread_kwargs):
    """Register a whole-thread route (top-level comments) and per-comment permalink routes.

    permalinks: comment dicts to serve at /comments/<post>/_/<id>.json (defaults to top_level).
    """
    fake.add(f"/comments/{post}.json", ok(thread_payload(post, list(top_level), **thread_kwargs)))
    for c in (permalinks or top_level):
        fake.add(f"/comments/{post}/_/{c['id']}.json", ok(thread_payload(post, c, **thread_kwargs)))


def ok(payload, headers=None):
    return FakeResponse(200, payload, headers)
