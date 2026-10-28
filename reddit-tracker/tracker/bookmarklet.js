(async () => {
  /* Runs on reddit.com when the "Sync from Reddit" bookmark is clicked. Every request is to the page's
     own origin, paced, and errors are recorded per item. The result is downloaded as a JSON file
     for the dashboard's "Import browser export". The config below is filled in by tracker/bookmarklet.py. */
  const C = __NB_CONFIG__;
  if (!/(^|\.)reddit\.com$/.test(location.hostname)) {
    alert('Open reddit.com in this tab first, then click "Sync from Reddit" again.');
    return;
  }
  if (window.__nbSyncRunning) {
    alert('The Nubra sync is already running in this tab.');
    return;
  }
  window.__nbSyncRunning = true;

  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;z-index:2147483647;right:16px;bottom:16px;background:#030712;color:#fff;' +
    'font:13px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;padding:12px 16px;border-radius:12px;' +
    'box-shadow:0 6px 24px rgba(0,0,0,.45);max-width:340px;white-space:pre-line;border:1px solid #1E2433';
  document.body.appendChild(box);

  let done = 0;
  let total = C.users.length + C.links.length;
  let errors = 0;
  const show = (msg) => {
    box.textContent = 'Nubra Reddit tracker\n' + msg + (errors ? '\n' + errors + ' item(s) failed, carrying on' : '');
  };
  const step = (what) => {
    done += 1;
    show('Fetching ' + done + '/' + total + '...\n' + what);
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let last = 0;
  const pace = async () => {
    const wait = last + C.delay - Date.now();
    if (wait > 0) await sleep(wait);
    last = Date.now();
  };

  /* anon=true fetches as a logged-out visitor (no cookies, no cache), which is how other people see
     the accounts: a logged-in owner can still see their own shadowbanned content. */
  async function get(path, anon) {
    for (let attempt = 0; attempt < 4; attempt++) {
      await pace();
      let r;
      try {
        r = await fetch(path, anon
          ? { credentials: 'omit', cache: 'no-store', headers: { Accept: 'application/json' } }
          : { credentials: 'include', headers: { Accept: 'application/json' } });
      } catch (e) {
        await sleep(C.retryDelay);
        continue;
      }
      if (r.status === 429 || r.status >= 500) {
        await sleep(C.retryDelay * (attempt + 1));
        continue;
      }
      const type = r.headers.get('content-type') || '';
      if (!r.ok) {
        const e = new Error('HTTP ' + r.status);
        e.status = r.status;
        e.json = type.indexOf('json') >= 0;
        throw e;
      }
      if (type.indexOf('json') < 0) throw new Error('Reddit returned a page instead of data');
      return await r.json();
    }
    throw new Error('Reddit kept rate-limiting; try again later');
  }
  const failure = (e) => (e && e.status && e.json ? { http: e.status } : { error: String((e && e.message) || e) });

  /* keep only the fields the tracker uses, so the file stays small */
  const KEEP_COMMENT = ['id', 'link_id', 'parent_id', 'author', 'body', 'score', 'subreddit', 'created_utc',
    'link_title', 'link_permalink', 'permalink', 'stickied'];
  const KEEP_POST = ['id', 'title', 'selftext', 'score', 'num_comments', 'upvote_ratio', 'author', 'created_utc',
    'subreddit', 'permalink'];
  const pick = (d, keys) => {
    const o = {};
    keys.forEach((k) => { if (d[k] !== undefined) o[k] = d[k]; });
    return o;
  };
  function trim(t) {
    if (!t || !t.kind) return t;
    if (t.kind === 'more') return { kind: 'more', data: { children: (t.data && t.data.children) || [] } };
    if (t.kind === 't3') return { kind: 't3', data: pick(t.data, KEEP_POST) };
    if (t.kind === 't1') {
      const o = pick(t.data, KEEP_COMMENT);
      const r = t.data.replies;
      o.replies = r && r.data ? { kind: 'Listing', data: { children: (r.data.children || []).map(trim) } } : '';
      return { kind: 't1', data: o };
    }
    if (t.kind === 'Listing') {
      return { kind: 'Listing', data: { after: t.data.after || null, children: (t.data.children || []).map(trim) } };
    }
    return { kind: t.kind, data: {} };
  }

  const out = {
    format: 'nubra-reddit-export', version: 1, list_version: C.listVersion, started_at: Date.now() / 1000,
    origin: location.origin, accounts: {}, links: [], threads: {}, comments: {}, subreddits: {}, errors: []
  };
  const posts = new Map();
  const subs = new Set();
  const track = (post, id, sub) => {
    if (!post) return;
    if (!posts.has(post)) posts.set(post, new Set());
    posts.get(post).add(id);
    if (sub) subs.add(sub);
  };
  const fail = (what, e) => {
    errors += 1;
    out.errors.push({ item: what, error: String((e && e.message) || e) });
  };
  let replies = 0;

  for (const u of C.users) {
    const acc = { status: 'ok', items: [] };
    out.accounts[u] = acc;
    let after = null;
    for (let page = 0; page < C.maxPages; page++) {
      step('u/' + u + (page ? ' (page ' + (page + 1) + ')' : ''));
      try {
        const j = await get('/user/' + encodeURIComponent(u) + '/comments.json?limit=100&raw_json=1' +
          (after ? '&after=' + encodeURIComponent(after) : ''));
        const items = ((j.data && j.data.children) || []).filter((c) => c.kind === 't1').map((c) => trim(c).data);
        items.forEach((c) => {
          acc.items.push(c);
          track((c.link_id || '').slice(3), c.id, c.subreddit);
        });
        replies += items.length;
        if (page === 0) acc.first_page_count = items.length;
        after = j.data && j.data.after;
        if (!after || !items.length || page + 1 >= C.maxPages) break;
        total += 1;
      } catch (e) {
        acc.status = e.status === 404 ? 'not_found' : e.status === 403 ? 'unavailable' : 'error';
        acc.error = String((e && e.message) || e);
        if (acc.status === 'error') fail('u/' + u, e);
        break;
      }
    }
  }

  for (const L of C.links) {
    step('linked comment');
    try {
      let post = L.post;
      let cid = L.comment;
      if (L.share) {
        await pace();
        const r = await fetch(L.path, { credentials: 'include' });
        const m = /\/comments\/([a-z0-9]+)\/[^\/]*\/([a-z0-9]+)/i.exec(new URL(r.url).pathname);
        if (!m) throw new Error('the share link did not lead to a comment');
        post = m[1].toLowerCase();
        cid = m[2].toLowerCase();
      }
      const pair = (await get('/comments/' + post + '/_/' + cid + '.json?depth=2&raw_json=1')).map(trim);
      out.links.push({ raw: L.raw, post_id: post, comment_id: cid, listing: pair });
      const c = ((pair[1] && pair[1].data.children) || []).find((x) => x.kind === 't1' && x.data.id === cid);
      track(post, cid, c ? c.data.subreddit : null);
      replies += c ? 1 : 0;
    } catch (e) {
      fail(L.raw, e);
    }
  }

  total += posts.size + subs.size;
  for (const [post, mine] of posts) {
    step('thread ' + post);
    try {
      const pair = (await get('/comments/' + post + '.json?sort=top&limit=' + C.threadLimit +
        '&depth=8&raw_json=1')).map(trim);
      out.threads[post] = pair;
      const seen = new Set();
      const walk = (nodes) => nodes.forEach((n) => {
        if (n.kind !== 't1') return;
        seen.add(n.data.id);
        if (n.data.replies) walk(n.data.replies.data.children);
      });
      walk((pair[1] && pair[1].data.children) || []);
      const missing = Array.from(mine).filter((id) => !seen.has(id));
      total += missing.length;
      for (const id of missing) {
        step('reply ' + id + ' (too deep for the thread view)');
        try {
          out.comments[id] = (await get('/comments/' + post + '/_/' + id + '.json?depth=2&raw_json=1')).map(trim);
        } catch (e) {
          fail('reply ' + id, e);
        }
      }
    } catch (e) {
      fail('thread ' + post, e);
    }
  }

  for (const s of subs) {
    step('r/' + s + ' size');
    try {
      const j = await get('/r/' + encodeURIComponent(s) + '/about.json?raw_json=1');
      out.subreddits[s] = j.data && typeof j.data.subscribers === 'number' ? j.data.subscribers : null;
    } catch (e) {
      out.subreddits[s] = null;
    }
  }

  /* --- account health and reply visibility, as a logged-out visitor --- */
  const health = { accounts: {}, replies: {} };
  out.health = health;
  const bodyFlag = (d) => (d.body === '[removed]' ? 'removed' : d.body === '[deleted]' ? 'deleted' : 'ok');
  total += C.users.length + posts.size;
  for (const u of C.users) {
    step('checking u/' + u + ' as a logged-out visitor');
    const acc = out.accounts[u] || {};
    const rec = { about: {}, normal_count: acc.first_page_count === undefined ? null : acc.first_page_count,
      anon_count: null };
    try {
      const j = await get('/user/' + encodeURIComponent(u) + '/about.json?raw_json=1', true);
      rec.about = { http: 200, json: true, is_suspended: !!(j.data && j.data.is_suspended) };
    } catch (e) {
      rec.about = e && e.status ? { http: e.status, json: !!e.json } : { error: String((e && e.message) || e) };
    }
    if (rec.about.http === 200 && !rec.about.is_suspended) {
      total += 1;
      step('u/' + u + ' comments, logged-out view');
      try {
        const j = await get('/user/' + encodeURIComponent(u) + '/comments.json?limit=100&raw_json=1', true);
        rec.anon_count = ((j.data && j.data.children) || []).filter((c) => c.kind === 't1').length;
      } catch (e) {
        rec.anon_error = String((e && e.message) || e);
      }
    }
    health.accounts[u] = rec;
  }
  for (const [post, mine] of posts) {
    step('public view of thread ' + post);
    let pair = null;
    try {
      pair = await get('/comments/' + post + '.json?sort=top&limit=' + C.threadLimit + '&depth=8&raw_json=1', true);
    } catch (e) {
      mine.forEach((id) => { health.replies[id] = Object.assign({ found: false }, failure(e)); });
      continue;
    }
    const seen = new Map();
    const walk = (nodes) => (nodes || []).forEach((n) => {
      if (n.kind !== 't1') return;
      seen.set(n.data.id, n.data);
      if (n.data.replies && n.data.replies.data) walk(n.data.replies.data.children);
    });
    walk(pair[1] && pair[1].data.children);
    const missing = [];
    mine.forEach((id) => {
      if (seen.has(id)) health.replies[id] = { found: true, body: bodyFlag(seen.get(id)) };
      else missing.push(id);
    });
    total += missing.length;
    for (const id of missing) {
      /* not in the thread view: check the reply's own permalink before calling it hidden */
      step('public view of reply ' + id);
      try {
        const p = await get('/comments/' + post + '/_/' + id + '.json?depth=1&raw_json=1', true);
        const c = ((p[1] && p[1].data.children) || []).find((x) => x.kind === 't1' && x.data.id === id);
        health.replies[id] = c ? { found: true, body: bodyFlag(c.data) } : { found: false };
      } catch (e) {
        health.replies[id] = Object.assign({ found: false }, failure(e));
      }
    }
  }

  out.exported_at = Date.now() / 1000;
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const name = 'reddit_export_' + d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + '.json';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], { type: 'application/json' }));
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.__nbLastExport = out;
  window.__nbSyncRunning = false;
  const problems = Object.keys(health.accounts).filter((u) => {
    const a = health.accounts[u].about;
    return a.http === 404 || a.is_suspended;
  });
  show('Done: ' + replies + ' replies, ' + posts.size + ' threads.' +
    (problems.length ? '\nWARNING: not visible to logged-out visitors: u/' + problems.join(', u/') : '') +
    '\nDownloaded ' + name +
    '\nNow import it in the dashboard sidebar.\n(click to close)');
  box.style.cursor = 'pointer';
  box.onclick = () => box.remove();
})();
