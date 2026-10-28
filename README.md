# UTM Tracker

UTM Tracker is a Python application for measuring how Reddit replies posted for Nubra perform over time. It collects public Reddit reply data, calculates engagement and visibility metrics, and presents the results in a Nubra-branded Streamlit dashboard.

The project is intended for teams and individuals who manage Reddit outreach and want to understand reply performance, account visibility, disclosure compliance, and weekly impact.

## Features

- Track Reddit replies by username or specific comment URL
- Monitor:
  - Upvotes and average upvotes
  - Replies received
  - Thread reach
  - Thread rank and top-comment rate
  - Original-poster engagement
  - First-24-hour performance
  - Removed and deleted replies
  - Public visibility
- Review account health, including:
  - Visible accounts
  - Possibly hidden accounts
  - Suspended or shadowbanned accounts
  - At-risk accounts
- Automatically classify replies by:
  - Topic
  - Nubra mentions
  - Disclosure status
  - Sentiment of responses
  - Broker and competitor mentions
- Filter results by account, date, subreddit, topic, and visibility
- Compare performance by timing, reply depth, subreddit size, topic, and subreddit
- Generate detailed and manager-friendly weekly reports
- Use browser-based Reddit synchronization when direct requests are blocked
- Optionally use direct Reddit synchronization
- Run daily synchronization and report generation through Windows Task Scheduler
- Upload username and comment-link lists from `.txt` or `.csv` files

## Repository layout

```text
.
├── reddit-tracker/
│   ├── accounts.txt             # Reddit usernames to track
│   ├── comments.txt             # Optional Reddit comment URLs
│   ├── config.json              # Classification and runtime settings
│   ├── dashboard.py             # Streamlit dashboard entry point
│   ├── requirements.txt         # Python dependencies
│   ├── run_daily.py             # Daily sync and report job
│   ├── run_daily.bat            # Windows Task Scheduler wrapper
│   ├── assets/                  # Logos and branding configuration
│   ├── data/                    # Local SQLite database and logs
│   ├── reports/                 # Generated weekly Markdown reports
│   ├── tracker/                 # Core application modules
│   └── tests/                   # Offline unit and integration tests
└── .playwright-mcp/             # Browser automation logs and artifacts
```

The detailed operating guide is available in [`reddit-tracker/README.md`](reddit-tracker/README.md).

## Requirements

- Windows PowerShell
- Python 3
- A modern browser such as Chrome or Edge for browser sync
- Access to Reddit through the browser or a network that permits direct Reddit requests

## Setup

From the repository root:

```powershell
cd reddit-tracker
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell prevents virtual-environment activation, enable locally signed scripts:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Configure tracked accounts and comments

Add one Reddit username per line to:

```text
reddit-tracker/accounts.txt
```

The following formats are accepted:

```text
username
u/username
/u/username
```

You can also add individual Reddit comment URLs to:

```text
reddit-tracker/comments.txt
```

Comment links should point to a specific comment rather than an entire thread.

The dashboard also supports uploading `.txt` and `.csv` files for usernames and comment links.

## Run the dashboard

```powershell
cd reddit-tracker
.\.venv\Scripts\Activate.ps1
streamlit run dashboard.py
```

The dashboard opens at:

```text
http://localhost:8501
```

Stop the dashboard with `Ctrl+C`.

## Collect Reddit data

### Browser sync — recommended

Browser sync is the default mode and is useful when Reddit blocks requests from the application’s network.

1. Start the dashboard.
2. In the sidebar, find **Get data from Reddit**.
3. Drag the **Sync from Reddit** button to your browser bookmarks bar.
4. Open Reddit in the browser.
5. Click the bookmark and wait for the export to finish.
6. Import the downloaded `reddit_export_<date>.json` file into the dashboard.

The browser performs the Reddit requests, while the dashboard imports and stores the resulting data locally. Your Reddit login is not sent to the application.

Whenever `accounts.txt` or `comments.txt` changes, generate and save the updated bookmark again.

### Direct sync

Direct sync allows the application to fetch data itself:

1. Select **Direct sync** in the dashboard sidebar.
2. Use **Sync now** to find new replies.
3. Use **Refresh stats** to collect new statistics for tracked replies.

Direct sync may fail on networks where Reddit blocks automated or logged-out requests. If that happens, switch back to Browser sync.

## Daily automation

Run the daily job manually:

```powershell
cd reddit-tracker
.\.venv\Scripts\python.exe run_daily.py
```

The job:

- Uses the selected sync mode
- Synchronizes and refreshes Reddit data when Direct sync is enabled
- Generates missing weekly reports
- Writes execution output to `data\daily.log` when run through `run_daily.bat`

To schedule it with Windows Task Scheduler:

```powershell
schtasks /Create /TN "Nubra Reddit Tracker" /SC DAILY /ST 10:00 /TR "\"C:\path\to\UTM-Tracker\reddit-tracker\run_daily.bat\""
```

Test the scheduled task:

```powershell
schtasks /Run /TN "Nubra Reddit Tracker"
```

Remove it:

```powershell
schtasks /Delete /TN "Nubra Reddit Tracker" /F
```

When Browser sync is selected, the scheduled job cannot fetch Reddit data automatically because it requires a browser session. It can still maintain weekly reports.

## Configuration

Application settings and classification rules are stored in:

```text
reddit-tracker/config.json
```

Configuration includes:

- Reddit request pacing and retry behavior
- Browser-sync limits
- Automatic refresh intervals
- India Standard Time reporting offset
- Nubra terms and disclosure phrases
- Topic keywords
- Competitor and broker aliases
- Sentiment keywords
- Subreddit-size buckets
- Account-health thresholds

Classification runs again during refresh. Manual corrections made in the dashboard take precedence over automatic classification.

## Local data and reports

The application stores local runtime data in:

```text
reddit-tracker/data/
```

The main database is:

```text
reddit-tracker/data/reddit.db
```

Weekly reports are saved as Markdown files in:

```text
reddit-tracker/reports/
```

Reports are generated for Monday–Sunday weeks using India Standard Time and include detailed and manager-oriented versions.

Runtime data, logs, and the SQLite database should remain local and should not be committed to source control.

## Tests

Tests run offline using fake Reddit responders and do not require network access:

```powershell
cd reddit-tracker
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

The test suite covers input parsing, uploads, classification, health checks, pagination, rate limits, synchronization, browser imports, metrics, and dashboard-related behavior.

## Metrics and limitations

- Only public Reddit comments are tracked.
- Reddit’s public user listings may expose only a limited number of recent comments.
- Thread reach is based on thread comment counts and is only a rough visibility proxy; it is not an impression or click metric.
- Reddit scores may be fuzzed and represent net upvotes.
- Rank, original-poster replies, and broker mentions are based on the portion of a thread returned by Reddit, typically the first several hundred comments.
- Direct synchronization is rate-limited and may be blocked by Reddit or by the local network.
- Removed, deleted, private, suspended, or otherwise unavailable content may not be readable publicly.
- Weekly reporting and timestamps use India Standard Time.
- The application is read-only with respect to Reddit; it does not post or edit Reddit content.

## Security and privacy

- Use usernames and public comment URLs only.
- Do not place passwords, API keys, tokens, or other credentials in input files.
- Uploaded CSV files are parsed with password- and secret-like columns excluded.
- Username values containing separators such as commas or colons are cleaned and generate warnings.
- Browser sync keeps the Reddit session in the browser and exports collected data as a local JSON file.

## Further documentation

For the complete setup guide, browser bookmark workflow, metric definitions, troubleshooting information, and report details, see:

[`reddit-tracker/README.md`](reddit-tracker/README.md)
