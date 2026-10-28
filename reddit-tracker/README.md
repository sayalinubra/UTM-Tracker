# reddit-tracker

Tracks how the Reddit replies posted for Nubra are performing (upvotes, replies, thread reach, removals, disclosure) and shows it in a Nubra-branded dashboard. Everything runs automatically: you only maintain two text files.

## 1. The two input files

**`accounts.txt`**: one Reddit username per line.
- `u/name`, `/u/name` and `name` all work. Blank lines and lines starting with `#` are ignored.
- Usernames only. If a line looks like `name,password` or `name:password`, only the username is used, nothing else is stored, and the dashboard warns you to clean up the line.
- Every **public** comment these accounts have made is tracked. Reddit's public listing only goes back about 1,000 comments per account.

**`comments.txt`** (optional): one Reddit **comment** link per line.
- Works with `www.reddit.com`, `old.reddit.com`, `new.reddit.com` and share links (`reddit.com/r/<sub>/s/...`).
- These are always tracked, even if they're old or posted from an account that isn't in `accounts.txt`.
- A link to a whole thread (not a single comment) is skipped with a warning. Open your reply, click **Share → Copy link**, and paste that instead.

Both files are re-read every time, so just edit them. With **Browser sync** (the default), drag the regenerated bookmark afterwards (see section 3). If you remove an account or link, its replies stay in the database but stop being shown or refreshed.

**Uploading lists instead of editing files.** In the sidebar, under **Upload lists**:
- **Upload usernames** takes a `.txt` file (one per line) or a `.csv` file. In a CSV, the column is picked automatically when its header looks like *username / user / account / handle*, e.g. "Assigned to Account" in the UTM sheet. If that's unclear, you choose the column.
- **Upload comment links** takes a `.txt` file (one per line) or a `.csv` file. The column with the most reddit.com links is picked automatically; otherwise you choose.
- For each upload, choose **Add to existing** (merge, skipping duplicates) or **Replace list** (it tells you how many entries would be removed). Then click **Save & sync**. The list is saved back to `accounts.txt` / `comments.txt`. With Browser sync, the bookmark is regenerated and you're reminded to drag the new one. With Direct sync, a sync runs for anything new. The box shows how many were added, how many duplicates were skipped, and when the file was last updated.
- **Password safety:** CSV columns whose header looks like a password, secret, token, key, OTP or PIN are never read. Anything after `,` or `:` in a username is dropped. You get a warning, and only usernames or links are ever written to disk.

## 2. First-time setup (PowerShell)

```powershell
cd "C:\Users\Sayali Sachin\Desktop\UTM Tracker\reddit-tracker"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If `Activate.ps1` is blocked ("running scripts is disabled"), run this once and then activate again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## 3. Start the dashboard

```powershell
cd "C:\Users\Sayali Sachin\Desktop\UTM Tracker\reddit-tracker"
.\.venv\Scripts\Activate.ps1
streamlit run dashboard.py
```

Your browser opens at http://localhost:8501. Stop the dashboard with **Ctrl+C** in PowerShell.

### Getting data: Browser sync (default)

Reddit blocks this app's own requests on most networks, but reddit.com works in your normal browser. So with Browser sync, **your browser does the fetching**. A bookmark ("bookmarklet") reads the public comments of your accounts, plus each thread they're in, while you're on reddit.com, and saves them to a file. You then import that file. It only reads public data and never posts anything. Your Reddit login stays in your browser: the app never sees it.

**One-time setup: put the bookmark on your bookmarks bar**
1. Show the bookmarks bar in Chrome or Edge with **Ctrl+Shift+B**.
2. In the dashboard sidebar, under **Get data from Reddit**, find the blue **⇅ Sync from Reddit** button.
3. Drag that button with the mouse and drop it onto the bookmarks bar. (Clicking it in the dashboard just shows a reminder; it only works on reddit.com.)
4. **Can't drag it?** Open **Can't drag it?** under the button and copy the long text. Press **Ctrl+Shift+O** (bookmark manager), then **⋮ → Add new bookmark**. Name it `Sync from Reddit` and paste the text as the **URL**.

The bookmark has your usernames and links built in. Its **version code** (shown under the button) changes whenever `accounts.txt` or `comments.txt` change. When it does, drag the new button and delete the old bookmark. The import warns you if a file came from an older bookmark.

**Each time you want fresh numbers**
1. Open **https://www.reddit.com** in a tab (logged in or not; either works).
2. Click **Sync from Reddit** on the bookmarks bar. A small dark box in the bottom-right corner shows *Fetching 12/80…*. Leave the tab open until it says **Done**. It waits about 1.5 seconds between requests, so 10 accounts with ~100 threads take roughly 3–4 minutes. If something fails, like a deleted account or a removed thread, it notes it and carries on.
3. Your browser downloads **`reddit_export_<date>.json`** (normally into *Downloads*).
4. In the dashboard sidebar, under **3. Import that file**, click **Upload** and choose that file. It imports straight away. You see how many replies were added or updated, and every card, chart and report updates.

Each import is stored as a snapshot dated by the export time, so importing every day builds the same history as before. Importing the same file twice adds nothing, and an older file only fills in history. If stats are more than 6 hours old, the sidebar reminds you to run the bookmark again.

### Account health and visibility (from the bookmark)

On reddit.com you're usually logged in, and Reddit shows logged-in owners their own shadowbanned content as normal. So after fetching your data, the bookmark checks again **as a logged-out visitor** (no cookies). Each export includes these checks, and each import stores them as history.

**Account status** (sidebar → *Account health*)

| Badge | Meaning |
|---|---|
| 🟢 **Visible** | The profile and recent comments show to logged-out visitors. |
| 🟠 **Possibly hidden** | The profile loads, but fewer than half of the account's recent comments (first page) show to visitors. This is only judged when the account has at least 3 recent comments. Profile content is hidden, or the account is shadowbanned. |
| 🔴 **Shadowbanned or deleted** | `/user/<name>/about.json` returns Reddit's "not found" to visitors. |
| 🔴 **Suspended** | Reddit reports the account as suspended. |
| ⚪ **Couldn't check** | The check failed (e.g. Reddit showed a verification page). A failed check never counts as a shadowban, and the last known status is kept. |
| 🟠 **At risk** (next to Visible) | Early warning signs in the last 2 weeks (needs at least 3 new replies): ≥75% of replies older than a day sit at ≤1 upvote with no replies, or ≥20% were removed by moderators, or ≥3 replies (or ≥40%) mention or link Nubra. The reason is shown under the badge. |

Each account shows the date its current status was first detected and when it was last checked. A **red banner** at the top of the page names any account that went from Visible to a problem status since the previous import. **Account health history** (near the bottom) lists every status change with its date.

**Reply visibility** (the *Visibility* column in Top replies, plus a *Visibility* filter in the sidebar)
- **Public**: logged-out visitors see it normally.
- **Removed by moderators**: visitors see `[removed]`.
- **Deleted**: shows as `[deleted]`, or you deleted it.
- **Not visible publicly**: missing from what visitors see (checked in the thread and on its own link), or the thread or subreddit can't be viewed by visitors, or the account is shadowbanned or suspended.
- **Couldn't check**: the check request failed.

The *Status* column is what you see logged in; *Visibility* is what everyone else sees.

**How hidden replies affect the numbers.** Replies stay in the database, but anything Removed, Deleted or Not visible publicly is left out of **thread reach**, the **top-comment rate** and every **upvote average**: the KPI card, best subreddits and topics, "What works best", and the reports. Totals and counts (replies posted, total upvotes, replies received) still include every reply. The **Hidden replies** card shows how many replies aren't visible to the public, broken down by reason.

All thresholds are under `account_health` in `config.json`.

### Getting data: Direct sync (optional)

Choose **Direct sync** under *Get data from Reddit* to use the original automatic mode. The app fetches from Reddit itself. The dashboard syncs on open when stats are over 6 hours old, and the **Sync now** / **Refresh stats** buttons appear:
- **Sync now** re-reads both files, finds new replies, and takes a first snapshot of each new one.
- **Refresh stats** takes a fresh snapshot of every tracked reply.

It only works on networks where Reddit doesn't block it. If you see "Reddit is blocking logged-out requests", switch back to Browser sync. Your choice is remembered.

### Everything else
- **Filters**: account, date posted, subreddit and topic. They apply to everything except the weekly report, which always covers all tracked replies.
- **Light / dark theme**: the **⋮** menu (top right) → *System / Light / Dark*. *System* follows your Windows setting, and your choice is remembered in the browser. The logo, cards and charts switch with it. The rest of Streamlit's menu (Deploy, developer options) is hidden.
- **Manager view** (sidebar → View): shows overall impact and quality instead of activity. It hides the per-week charts, the "this week" changes on the cards, and the Date columns. Totals, averages, rates, "What works best", best subreddits/topics and top replies stay. The weekly report shows its manager version. Nothing stored changes; this only affects what's displayed.
- **Presentation mode**: hides the sidebar, enlarges the charts and **turns Manager view on**. You can switch it off with the toggle at the top. **Exit presentation** (top right) returns to the previous view.
- **Correct an automatic classification**: optional. It lets you override the topic, "Mentions Nubra" or "Disclosed" for one reply. Choose *Automatic* to undo.

With Direct sync, Reddit allows roughly one request every 2 seconds. A refresh makes one request per **thread**, which covers every tracked reply in it. Deeply nested replies get one extra request each, and each subreddit's size is fetched once a day. So 150 replies take about 5 minutes or less.

## 4. Daily automatic run (Windows Task Scheduler)

`run_daily.py` saves last week's reports if they don't exist yet. With **Direct sync** selected, it first runs a sync and refresh. With **Browser sync** (the default) it can't fetch anything, because that needs you on reddit.com. So it only keeps the weekly reports up to date and logs a reminder; the data comes from your imports. `run_daily.bat` runs it with the project's `.venv` and appends the output to `data\daily.log`.

**Option A: one command (PowerShell)**, which runs every day at 10:00:

```powershell
schtasks /Create /TN "Nubra Reddit Tracker" /SC DAILY /ST 10:00 /TR "\"C:\Users\Sayali Sachin\Desktop\UTM Tracker\reddit-tracker\run_daily.bat\""
```

**Option B: Task Scheduler window**
1. Press Start, type **Task Scheduler**, and open it.
2. **Create Basic Task…**: name it `Nubra Reddit Tracker`, then **Next**.
3. Trigger: **Daily**, at a time your PC is usually on (e.g. 10:00).
4. Action: **Start a program**. Program/script: browse to `run_daily.bat` in this folder.
5. Finish. Then open the task's **Properties → Settings** and tick **"Run task as soon as possible after a scheduled start is missed"** so a day isn't skipped if the laptop was off.

Test it with `schtasks /Run /TN "Nubra Reddit Tracker"` and check `data\daily.log`. To remove it: `schtasks /Delete /TN "Nubra Reddit Tracker" /F`.

## 5. Weekly reports

Weeks run **Monday 00:00 to Sunday 23:59, India time (IST)**. Two versions of last week's report are written automatically (by the daily run or when the dashboard syncs):

- `reports\weekly-YYYY-MM-DD.md`: **detailed** (for you). It has replies posted, upvotes gained and replies received, each with the change vs the previous week. It also has the best reply (biggest upvote gain, with link), the best subreddit and topic, the disclosure rate, and the quality of that week's replies.
- `reports\weekly-YYYY-MM-DD-manager.md`: **manager version**. It has totals (replies, upvotes gained, replies received) with no comparisons, and quality metrics *with* +/- vs the previous week: average upvotes per reply, top-comment rate, OP engagement rate, positive reply rate, and Nubra share of broker mentions. It also lists the top 3 replies and top 3 subreddits. Nothing is broken down by day.

In the dashboard, pick a week (any of the last 9, including "this week so far") and a version, then click **Generate weekly report** to save it. In Manager view only the manager version is shown. Use the copy icon on the report box to paste it into Slack. It uses Slack formatting: `*bold*` and `_italic_`.

Both versions end with an **Account health** section. It lists each account's status as of the end of the week (problem statuses show when they were first detected, and at-risk reasons are included), any status changes during the week, and how many replies aren't visible to the public, by reason.

Quality numbers in a report describe the replies **posted that week**, measured at the end of the week. "Best subreddit" means the highest average upvotes among replies posted that week.

## 6. How to read the numbers

| Number | Meaning |
|---|---|
| Replies posted | Tracked replies (after filters). "This week" = posted since Monday 00:00 IST. |
| Total upvotes | Sum of each reply's latest **net** score (upvotes minus downvotes; Reddit also fuzzes scores slightly). |
| Avg upvotes per reply | Total upvotes ÷ replies posted. |
| Replies received | Direct replies to the tracked comments (not replies-to-replies). |
| Thread reach | Sum of the comment counts of the threads replied in, each thread counted once. **A rough visibility proxy only.** Reddit doesn't publish impressions or link clicks. |
| Disclosed % | Of the replies that mention Nubra, how many contain a disclosure phrase. |
| Top-comment rate | Of my **top-level** replies, the % ranked **#1** in their thread by score. Nested replies have no rank. Stickied mod comments are ignored. |
| Rank in thread | Position by score among the thread's top-level comments, e.g. "#2 of 40". Badges: **Top comment** (#1) and **Top 3**. |
| Replied after | Hours between the thread being posted and my reply. "What works best" groups them as under 2h, 2–12h and 12h+. |
| Depth | Top-level comment vs a reply to someone else's comment. |
| OP engagement rate | % of my replies that the thread's original poster replied to directly. Once seen, it stays counted even if that reply is later removed. |
| First 24h | Upvotes reached within 24 hours of posting: the latest snapshot taken in that window. It shows "–" if no snapshot was taken in the first day. |
| Thread quality | The thread's upvote ratio (under the title) and the subreddit's member count (under the subreddit; from `r/<sub>/about.json`, refreshed once a day). |
| Positive reply rate | Direct replies to my comments from other people (my own accounts excluded), classified by keyword as positive, neutral or negative. This is the % that are positive. |
| Nubra share of broker mentions | In the threads I replied in, comments by other people (plus the thread's own title and text) are counted once per broker they mention. Nubra's share of all broker mentions. |
| Change this week | Value now minus value at Monday 00:00 IST, using the snapshot history. Hidden in Manager view. |

Rank, OP replies and broker mentions come from the first ~500 comments Reddit returns for each thread (sorted by top), so very large threads are only partly scanned.

Growth needs history: the "Upvotes gained per week" chart appears after the second stats refresh. For a reply posted before tracking started, its earliest snapshot is used as its value for earlier dates, so old upvotes don't show up as a sudden spike.

**Status:** *live*; *removed* (by moderators, Reddit's filters, or the thread is gone); *deleted* (by the account). The text of removed or deleted replies is kept from before removal, so you can see what was taken down.

## 7. Automatic classification (`config.json`)

- **Mentions Nubra**: the text contains any of `nubra_terms` (case-insensitive), e.g. "nubra" or "nubra.io".
- **Disclosed**: the text contains any of `disclosure_phrases` (case-insensitive; curly apostrophes and extra spaces are ignored). Add your own phrases to the list.
- **Topic**: the topic whose `topics` keywords appear most often (whole words). Ties go to the topic listed first, and no match gives `Other`. If `topic_uses_thread_title` is true, the thread title is used only when the reply itself matches nothing.

Classification re-runs on every refresh, so changes to `config.json` apply on the next refresh. Manual corrections in the dashboard always win.

- **Brands** (`competitors`): broker names and their aliases (e.g. Zerodha: zerodha, kite). Nubra uses `nubra_terms`.
- **Sentiment** (`sentiment.positive` / `sentiment.negative`): keyword lists for replies to my comments. More positive hits than negative = positive, the reverse = negative, otherwise neutral.
- **Subreddit size buckets** (`subreddit_size_buckets`): `[minimum members, label]` pairs. The defaults are under 100k, 100k–1M, and 1M+.

Other settings: `request_interval_seconds` (pacing), `auto_refresh_hours` (6), `max_pages_per_user` (10 × 100 comments), and `utc_offset_hours` (5.5 = IST).

## 8. Branding

- `assets/nubra-logo.png`: white logo, used in dark mode. `assets/nubra-logo-dark.png`: dark logo, used in light mode (generated from the white one). If either is missing, the text "Nubra" is shown.
- `assets/brand.json`: `#030712` and `#FFFFFF` are sampled from the logo. The accent `#6382D9` and the up/down colours are **neutral placeholders**. Replace them with Nubra's official hex codes.

## 9. Troubleshooting

- **"Reddit is blocking logged-out requests from this internet connection"** (Direct sync): Reddit returned its block page instead of data, for every request, so this isn't about your accounts. Switch to **Browser sync**. Nothing is lost: already-collected data stays.
- **The bookmark does nothing / says "Open reddit.com first"**: it only runs on a reddit.com tab, so open https://www.reddit.com, then click it.
- **The progress box shows many failures**: if it says "Reddit returned a page instead of data", Reddit is asking that browser to verify. Load any reddit.com page normally (solve the check if one appears), then run the bookmark again. Failed items are listed in the import message, and everything else is still imported.
- **"This export was made with an older version of the bookmark"** or **"This export has no account health checks"**: your lists or the bookmark's code changed since you saved it. Drag the new button from the sidebar and delete the old bookmark.
- **An account shows "Couldn't check"**: the logged-out check was blocked or failed for that run. Run the bookmark again later. Until then the last known status is kept.
- **"This isn't a browser export"**: choose the `reddit_export_<date>.json` file the bookmark downloaded, not a different JSON file.
- **An account shows "not found"**: check the spelling in `accounts.txt`, or the account was deleted.
- **"Account is suspended" / "private or unavailable"**: its comments can't be read publicly.
- **Rate limited**: the tool already waits and retries. If it still fails, try again in a few minutes.

## 10. Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

The tests run offline against a fake Reddit responder (no network). They cover input parsing, uploads and merging (including password safety), pagination and rate limits, thread scanning (rank, OP replies, sentiment, mentions), metrics and both report versions. `tests\test_dashboard.py` also runs the real dashboard headless to check Manager view, Presentation mode and that Browser sync is the default. `tests\test_health.py` covers account and reply classification (about.json answers, anonymous vs normal comment counts, removed/deleted/missing), status history and alerts, at-risk rules, metric exclusions and the report section. `tests\test_browser_import.py` covers export parsing, snapshot dating, de-duplication and the bookmarklet. If Node.js is installed, it also runs the real bookmarklet against a fake reddit.com and imports what it downloads.

Your existing `data\reddit.db` is upgraded in place the first time the new version opens it. Nothing is lost. The new metrics fill in from the next refresh onwards.

## Files

```
accounts.txt / comments.txt   your inputs
config.json                   classification rules + settings
assets/                       logos and brand.json
dashboard.py                  Streamlit dashboard
run_daily.py / run_daily.bat  scheduled run
tracker/                      fetching, storage, classification, metrics, reports
tests/                        offline unit tests
data/reddit.db                SQLite database (git-ignored)
reports/                      weekly Markdown reports
```
