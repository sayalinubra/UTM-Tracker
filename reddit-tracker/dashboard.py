"""Nubra-branded Streamlit dashboard: streamlit run dashboard.py"""
import base64
import html
import time
from datetime import date, datetime

import plotly.graph_objects as go
import streamlit as st

from tracker import config as C
from tracker import db
from tracker import health as H
from tracker import metrics as m
from tracker import report as R
from tracker import bookmarklet as BM
from tracker import browser_import as BI
from tracker import service
from tracker import uploads as U
from tracker.inputs import parse_accounts, parse_comments, read_text

st.set_page_config(page_title="Reddit Reply Impact · Nubra", layout="wide",
                   initial_sidebar_state="expanded")

cfg = C.load_config()
brand = C.load_brand()
OFFSET = cfg["utc_offset_hours"]

st.session_state.setdefault("presentation", False)
st.session_state.setdefault("manager_view", False)
st.session_state.setdefault("run_log", None)
st.session_state.setdefault("upload_log", {})


L, D = brand["light"], brand["dark"]
ACCENT = brand["accent"]
# Chart ink that reads on both themes (charts are drawn once; the theme can change without a rerun).
CHART_INK, CHART_GRID = "#7C7F86", "rgba(128,130,136,0.25)"
POS, NEG = brand["status"]["positive"], brand["status"]["negative"]
WARN = brand["status"].get("warning", "#C98A1B")
PRESENT = st.session_state["presentation"]
# Manager view: overall impact and quality only - no per-week charts, weekly changes or dates.
MANAGER = st.session_state["manager_view"]
esc = html.escape


# --- styling ---------------------------------------------------------------

def inject_css():
    sidebar_hide = """
section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"] { display: none !important; }
""" if PRESENT else ""

    def ld(key):
        return f"light-dark({L[key]}, {D[key]})"

    # Streamlit sets color-scheme on .stApp, so light-dark() follows the active theme instantly.
    st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
.stApp {{ --bg:{ld('background')}; --surface:{ld('surface')}; --border:{ld('border')};
  --text:{ld('text')}; --muted:{ld('muted')}; --accent:{ACCENT}; --pos:{POS}; --neg:{NEG}; --warn:{WARN};
  --logo-bg: light-dark(#FFFFFF, {brand['logo']['navy']});
  --shadow: light-dark(rgba(3,7,18,0.06), rgba(3,7,18,0.35)); }}
.nb-logo-on-light {{ display: none; }}
html[data-nb-theme="light"] .nb-logo-on-dark {{ display: none; }}
html[data-nb-theme="light"] .nb-logo-on-light {{ display: inline; }}
.stApp, .stApp *:not([data-testid="stIconMaterial"]):not(.material-symbols-rounded) {{
  font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif; }}
footer, [data-testid="stDecoration"], [data-testid="stStatusWidget"], .stDeployButton,
[data-testid="stAppDeployButton"] {{ display: none !important; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container, [data-testid="stMainBlockContainer"] {{ padding: 1.2rem 2rem 2rem;
  max-width: {'100%' if PRESENT else '1600px'}; }}
section[data-testid="stSidebar"] {{ border-right: 1px solid var(--border); }}
.stButton > button, .stDownloadButton > button {{ border-radius: 10px; font-weight: 600; }}
.stButton > button[kind="primary"] p {{ color: #FFFFFF; }}
[data-testid="stExpander"] details {{ background: var(--surface); border: 1px solid var(--border); border-radius: 14px; }}
[data-testid="stCode"] pre, .stCode pre {{ background: var(--surface) !important; border: 1px solid var(--border); border-radius: 12px; }}
[data-testid="stCode"] code {{ color: var(--text) !important; white-space: pre-wrap !important; }}
.nb-header {{ display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap;
  background: var(--logo-bg); border:1px solid var(--border); border-radius:16px;
  padding: 14px 22px; margin-bottom: 18px; box-shadow: 0 2px 14px var(--shadow); }}
.nb-header .left {{ display:flex; align-items:center; gap:18px; }}
.nb-header img {{ height: 40px; }}
.nb-header .wordmark {{ font-size: 24px; font-weight: 700; color: var(--text); }}
.nb-header .title {{ font-size: {30 if PRESENT else 24}px; font-weight: 700; color: var(--text); line-height:1.2; }}
.nb-header .subtitle {{ font-size: 14px; color: var(--muted); }}
.nb-header .refreshed {{ font-size: 13px; color: var(--muted); text-align:right; }}
.nb-divider {{ width:1px; height:40px; background: var(--border); }}
.nb-section {{ font-size: {22 if PRESENT else 18}px; font-weight: 700; color: var(--text); margin: 26px 0 4px; }}
.nb-sub {{ font-size: 13px; color: var(--muted); margin-bottom: 10px; }}
.nb-kpis {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 14px; }}
.nb-card {{ background: var(--surface); border:1px solid var(--border); border-radius: 16px; padding: 16px 18px;
  box-shadow: 0 2px 12px var(--shadow); }}
.nb-kpi .label {{ font-size: 13px; color: var(--muted); font-weight: 500; }}
.nb-kpi .value {{ font-size: {34 if PRESENT else 28}px; font-weight: 700; color: var(--text); margin: 4px 0 2px; }}
.nb-kpi .delta {{ font-size: 12.5px; font-weight: 600; }}
.nb-kpi .note {{ font-size: 11.5px; color: var(--muted); margin-top: 4px; }}
.up {{ color: var(--pos) !important; }} .down {{ color: var(--neg) !important; }} .flat {{ color: var(--muted) !important; }}
.nb-placeholder {{ display:flex; flex-direction:column; justify-content:center; align-items:center; text-align:center;
  min-height: 240px; color: var(--muted); font-size: 14px; }}
.nb-placeholder b {{ color: var(--text); font-size: 15px; margin-bottom: 6px; }}
.nb-table-wrap {{ overflow-x:auto; background: var(--surface); border:1px solid var(--border); border-radius:16px;
  box-shadow: 0 2px 12px var(--shadow); }}
table.nb-table {{ width:100%; border-collapse:collapse; font-size: {15 if PRESENT else 13.5}px; }}
table.nb-table th {{ text-align:left; color: var(--muted); font-weight:600; font-size:12px; text-transform:uppercase;
  letter-spacing:.03em; padding: 12px 12px; border-bottom:1px solid var(--border); white-space:nowrap; }}
table.nb-table td {{ padding: 10px 12px; border-bottom:1px solid var(--border); color: var(--text); vertical-align:top; }}
table.nb-table tr:last-child td {{ border-bottom:none; }}
table.nb-table td.num {{ text-align:right; font-variant-numeric: tabular-nums; }}
table.nb-table td {{ white-space: nowrap; }}
table.nb-table td.wrap {{ white-space: normal; min-width: 220px; max-width: 420px; }}
table.nb-table th.num {{ text-align:right; }}
table.nb-table a {{ color: var(--text); text-decoration: underline; text-decoration-color: var(--accent);
  text-underline-offset: 3px; }}
.badge {{ display:inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11.5px; font-weight:600;
  border:1px solid var(--border); color: var(--text); white-space:nowrap; }}
.badge.accent {{ background: var(--accent); border-color: var(--accent); color:#FFFFFF !important; }}
.badge.neg {{ border-color: var(--neg); color: var(--neg) !important; }}
.badge.pos {{ border-color: var(--pos); color: var(--pos) !important; }}
.badge.muted {{ color: var(--muted) !important; }}
.nb-empty {{ text-align:center; padding: 48px 24px; }}
.nb-empty h3 {{ margin: 0 0 8px; }}
.nb-empty p {{ color: var(--muted) !important; }}
.nb-empty code {{ background: var(--bg); padding: 2px 6px; border-radius: 6px; }}
.nb-footer {{ margin-top: 34px; padding: 14px 18px; border-top: 1px solid var(--border); color: var(--muted);
  font-size: 12.5px; }}
.nb-footer * {{ color: var(--muted) !important; }}
.nb-bookmarklet {{ display:inline-block; padding: 8px 16px; border-radius: 10px; background: var(--accent);
  color: #FFFFFF !important; font-weight: 600; font-size: 14px; text-decoration: none !important; cursor: grab;
  border: 2px dashed rgba(255,255,255,0.55); }}
.nb-bookmarklet:active {{ cursor: grabbing; }}
.badge.warn {{ border-color: var(--warn); color: var(--warn) !important; }}
.badge.solid-neg {{ background: var(--neg); border-color: var(--neg); color: #FFFFFF !important; }}
.badge.solid-warn {{ background: var(--warn); border-color: var(--warn); color: #FFFFFF !important; }}
.badge.solid-pos {{ background: var(--pos); border-color: var(--pos); color: #FFFFFF !important; }}
.nb-alert {{ background: var(--neg); color: #FFFFFF; border-radius: 14px; padding: 14px 18px; margin-bottom: 16px;
  font-size: 14px; box-shadow: 0 2px 12px var(--shadow); }}
.nb-alert * {{ color: #FFFFFF !important; }}
.nb-health {{ padding: 6px 0 8px; border-bottom: 1px solid var(--border); }}
.nb-health:last-child {{ border-bottom: none; }}
.nb-health .top {{ display:flex; justify-content:space-between; align-items:center; gap:8px; font-size: 13px; }}
.nb-health .meta {{ font-size: 11px; color: var(--muted) !important; margin-top: 2px; }}
.nb-acct {{ display:flex; justify-content:space-between; font-size: 13px; padding: 3px 0; }}
.nb-acct .st {{ font-size: 11.5px; }}
{sidebar_hide}
</style>""", unsafe_allow_html=True)


def logo_html():
    """Both logos are sent; CSS shows the one matching the active theme (dark by default)."""
    parts = []
    for path, cls in ((C.LOGO_LIGHT_ON_DARK, "nb-logo-on-dark"), (C.LOGO_DARK_ON_LIGHT, "nb-logo-on-light")):
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode()
            parts.append(f'<img class="{cls}" src="data:image/png;base64,{data}" alt="Nubra">')
        else:
            parts.append(f'<span class="wordmark {cls}">Nubra</span>')
    return "".join(parts)


def theme_watcher():
    """Mirror Streamlit's active theme onto <html data-nb-theme> so CSS can swap the logo.

    Streamlit doesn't rerun the script when the theme changes, so this runs in the browser.
    """
    st.html("""<script>
(() => {
  if (window.__nbThemeWatcher) return;
  window.__nbThemeWatcher = true;
  const sync = () => {
    const app = document.querySelector('.stApp');
    if (!app) return;
    const scheme = getComputedStyle(app).colorScheme || '';
    document.documentElement.setAttribute('data-nb-theme', scheme.includes('light') ? 'light' : 'dark');
  };
  sync();
  new MutationObserver(sync).observe(document.head, {childList: true, subtree: true, characterData: true});
  setInterval(sync, 1000);
})();
</script>""", unsafe_allow_javascript=True, width="content")


# --- formatting helpers ----------------------------------------------------

def fmt_num(v, decimals=0):
    if v is None or (isinstance(v, float) and v != v):
        return "–"
    return f"{v:,.{decimals}f}"


def fmt_ts(ts):
    if not ts:
        return "never"
    return m.fmt_date(float(ts), OFFSET, "%d %b %Y, %H:%M") + " IST"


def delta_html(change, unit="", decimals=0):
    if change is None:
        return '<div class="delta flat">– vs last week</div>'
    if abs(change) < 0.5 * 10 ** -decimals:
        return '<div class="delta flat">No change this week</div>'
    cls, arrow = ("up", "▲") if change > 0 else ("down", "▼")
    return (f'<div class="delta {cls}">{arrow} {"+" if change > 0 else ""}'
            f'{change:,.{decimals}f}{unit} this week</div>')


def cell(v):
    if v is None or (isinstance(v, float) and v != v) or v == "":
        return "–"
    return esc(str(v))


def text_or_empty(v):
    return v if isinstance(v, str) else ""


def link(url, label):
    url = text_or_empty(url)
    return f'<a href="{esc(url)}" target="_blank">{label}</a>' if url else label


# --- data refresh ----------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _cached_conn(path):
    return service.open_db(path)


def get_conn():
    """One SQLite connection per database file, reused across reruns."""
    return _cached_conn(str(C.DB_FILE))


def run_job(kind, where):
    """kind: 'sync', 'refresh' or 'both'. Shows a progress bar in `where`."""
    conn = get_conn()
    client = service.make_client(cfg)
    bar = where.progress(0.0, text="Starting...")

    def progress(fraction, message):
        bar.progress(min(max(fraction, 0.0), 1.0), text=message)

    if kind == "sync":
        result = service.sync(conn, client, cfg, C.ACCOUNTS_FILE, C.COMMENTS_FILE, progress)
    elif kind == "refresh":
        result = service.refresh(conn, client, cfg, C.ACCOUNTS_FILE, C.COMMENTS_FILE, progress)
    else:
        result = service.sync_and_refresh(conn, client, cfg, C.ACCOUNTS_FILE, C.COMMENTS_FILE, progress)
    for path in service.auto_weekly_report(conn, cfg):
        result.messages.append(f"Last week's report was saved to reports/{path.name}.")
    bar.empty()
    st.session_state["run_log"] = (result.messages, result.warnings, result.aborted)


def show_run_log(where):
    log = st.session_state.get("run_log")
    if not log:
        return
    messages, warnings, aborted = log
    if messages:
        text = "\n\n".join(messages[:4]) + (f"\n\n…and {len(messages) - 4} more" if len(messages) > 4 else "")
        (where.info if aborted else where.success)(text)
    for w in warnings[:8]:
        where.warning(w)
    if len(warnings) > 8:
        where.warning(f"…and {len(warnings) - 8} more warnings.")


# --- charts ----------------------------------------------------------------

def base_layout(fig, height, x_title=None, y_title=None, horizontal=False):
    fs = 15 if PRESENT else 12
    fig.update_layout(
        template="none", height=height, margin=dict(l=8, r=16, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, sans-serif", size=fs, color=CHART_INK),
        hoverlabel=dict(bgcolor=D["surface"], bordercolor=D["border"],
                        font=dict(color=D["text"], family="Inter, system-ui, sans-serif")),
        bargap=0.35, showlegend=False, barcornerradius=4,
    )
    grid = dict(gridcolor=CHART_GRID, zerolinecolor=CHART_GRID, linecolor=CHART_GRID,
                tickfont=dict(color=CHART_INK), automargin=True, title_standoff=12)
    fig.update_xaxes(title=x_title, showgrid=horizontal, **grid)
    fig.update_yaxes(title=y_title, showgrid=not horizontal, **grid)
    if horizontal:
        fig.update_yaxes(autorange="reversed", showgrid=False)
        fig.update_xaxes(rangemode="tozero")
    else:
        fig.update_xaxes(type="category")
    return fig


def card_title(text, sub=None):
    st.markdown(f'<div style="font-weight:600;font-size:{16 if PRESENT else 14}px;margin-bottom:2px">{esc(text)}</div>'
                + (f'<div class="nb-sub">{esc(sub)}</div>' if sub else ""), unsafe_allow_html=True)


def placeholder(title, body, height):
    st.markdown(f'<div class="nb-card nb-placeholder" style="min-height:{height}px"><b>{esc(title)}</b>'
                f'{esc(body)}</div>', unsafe_allow_html=True)


def plot(fig):
    st.plotly_chart(fig, width="stretch", theme=None,
                    config={"displayModeBar": False, "responsive": True})


def week_bar(weeks, column, label, height):
    labels = [w.strftime("%d %b") for w in weeks["week"]]
    fig = go.Figure(go.Bar(
        x=labels, y=weeks[column], marker_color=ACCENT,
        customdata=[w.strftime("%d %b %Y") for w in weeks["week"]],
        hovertemplate="Week of %{customdata}<br>" + label + ": <b>%{y:,.0f}</b><extra></extra>"))
    base_layout(fig, height, x_title="Week starting (Mon)")
    return fig


def group_bars(df, column, value, label, height, fmt=",.1f"):
    df = df.head(10)
    fig = go.Figure(go.Bar(
        y=df[column], x=df[value], orientation="h", marker_color=ACCENT,
        text=[f"{v:{fmt}}" for v in df[value]], textposition="outside",
        textfont=dict(color=CHART_INK), cliponaxis=False,
        customdata=df[["replies", "avg_upvotes", "avg_replies_received"]].values,
        hovertemplate="<b>%{y}</b><br>Replies posted: %{customdata[0]:,.0f}"
                      "<br>Avg upvotes: %{customdata[1]:,.1f}"
                      "<br>Avg replies received: %{customdata[2]:,.1f}<extra></extra>"))
    base_layout(fig, height, horizontal=True)
    fig.update_layout(margin=dict(l=8, r=40, t=8, b=8))
    return fig


def bucket_bars(df, column, height):
    """Vertical bars: average upvotes per bucket, with the number of replies under each label."""
    # long bucket names like "Small (<100k members)" wrap onto two lines instead of being rotated
    labels = [f"{str(b).replace(' (', '<br>(')}<br><span style='font-size:0.85em'>{n} repl"
              f"{'y' if n == 1 else 'ies'}</span>" for b, n in zip(df[column], df["replies"])]
    fig = go.Figure(go.Bar(
        x=labels, y=df["avg_upvotes"], marker_color=ACCENT,
        text=[f"{v:.1f}" for v in df["avg_upvotes"]], textposition="outside",
        textfont=dict(color=CHART_INK), cliponaxis=False,
        customdata=df[[column, "replies"]].values,
        hovertemplate="<b>%{customdata[0]}</b><br>Avg upvotes: %{y:,.1f}"
                      "<br>Replies: %{customdata[1]:,.0f}<extra></extra>"))
    base_layout(fig, height)
    fig.update_layout(margin=dict(l=8, r=16, t=24, b=8))
    fig.update_xaxes(tickangle=0)
    return fig


def brand_bars(counts, height):
    """Mentions by other users per brand; Nubra in the accent colour, competitors in grey."""
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    fig = go.Figure(go.Bar(
        y=[k for k, _ in items], x=[v for _, v in items], orientation="h",
        marker_color=[ACCENT if k == "Nubra" else "#9D9FA3" for k, _ in items],
        text=[f"{v:,}" for _, v in items], textposition="outside", textfont=dict(color=CHART_INK),
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Comments mentioning it: %{x:,}<extra></extra>"))
    base_layout(fig, height, horizontal=True)
    fig.update_layout(margin=dict(l=8, r=40, t=8, b=8))
    return fig


def pct(v):
    return "–" if v is None else f"{v:.0f}%"


# --- view state --------------------------------------------------------------

def _set_manager(widget_key):
    st.session_state["manager_view"] = st.session_state[widget_key]


def manager_toggle(widget_key):
    # the toggle can live in the sidebar or (in presentation mode) the main area; one shared value
    st.session_state[widget_key] = st.session_state["manager_view"]
    st.toggle("Manager view", key=widget_key, on_change=_set_manager, args=(widget_key,),
              help="Overall impact and quality only: hides per-week charts, weekly changes and dates.")


def _on_presentation():
    if st.session_state["presentation"]:
        st.session_state["_manager_before"] = st.session_state["manager_view"]
        st.session_state["manager_view"] = True
    else:
        st.session_state["manager_view"] = st.session_state.pop("_manager_before", st.session_state["manager_view"])


def _exit_presentation():
    st.session_state["presentation"] = False
    _on_presentation()


# --- uploads -------------------------------------------------------------------

UPLOAD_KINDS = {
    "accounts": ("Upload usernames", C.ACCOUNTS_FILE, U.parse_usernames_upload, "usernames",
                 "Which column has the usernames?"),
    "comments": ("Upload comment links", C.COMMENTS_FILE, U.parse_links_upload, "links",
                 "Which column has the Reddit comment links?"),
}


def current_list(kind):
    text = read_text(UPLOAD_KINDS[kind][1])
    if kind == "accounts":
        return parse_accounts(text).usernames
    return [link.raw for link in parse_comments(text).links]


def upload_box(kind, job_area):
    title, path, parser, noun, column_q = UPLOAD_KINDS[kind]
    with st.expander(title):
        log = st.session_state["upload_log"].get(kind)
        if log:
            st.success(log)
        updated = path.stat().st_mtime if path.exists() else None
        st.caption(f"{path.name} last updated: **{fmt_ts(updated)}**")
        n = st.session_state.setdefault(f"up_{kind}_n", 0)
        f = st.file_uploader(f"{noun.capitalize()} file (.txt or .csv)", type=["txt", "csv"], key=f"up_{kind}_{n}",
                             help=".txt: one per line. .csv: a column of " + noun + ".")
        if f is None:
            return
        text = U.decode(f.getvalue())
        parsed = parser(f.name, text)
        if parsed.needs_column_choice:
            if not parsed.columns:
                st.error("No usable columns found in this file.")
                return
            col = st.selectbox(column_q, parsed.columns, key=f"up_{kind}_col_{n}")
            parsed = parser(f.name, text, col)
        elif parsed.column:
            st.caption(f"Using column **{parsed.column}**.")
        for w in parsed.warnings[:6]:
            st.warning(w)
        if len(parsed.warnings) > 6:
            st.warning(f"…and {len(parsed.warnings) - 6} more lines skipped.")
        if not parsed.values:
            st.info(f"No valid {noun} found in this file.")
            return
        mode_label = st.radio("Save as", ["Add to existing", "Replace list"], key=f"up_{kind}_mode_{n}",
                              horizontal=True)
        mode = "add" if mode_label.startswith("Add") else "replace"
        key = str.lower if kind == "accounts" else U.comment_key
        existing = current_list(kind)
        merged = U.merge(existing, parsed.values, mode, key)
        skipped = merged.duplicates + parsed.duplicates_in_file
        st.caption(f"{len(parsed.values) + parsed.duplicates_in_file} in file · **{len(merged.added)} new** · "
                   f"{skipped} duplicate{'s' if skipped != 1 else ''} skipped")
        if mode == "replace" and merged.removed:
            st.warning(f"Replace will remove {len(merged.removed)} {noun} that aren't in this file. "
                       f"Their past data stays saved but stops being shown.")
        if st.button("Save & sync", key=f"up_{kind}_save_{n}", type="primary", width="stretch"):
            changed = U.save_list(path, kind, merged, mode, read_text(path), f.name)
            msg = f"{len(merged.added)} added, {skipped} duplicate{'s' if skipped != 1 else ''} skipped"
            if mode == "replace":
                msg += f", {len(merged.removed)} removed"
            msg += f" (saved to {path.name})."
            if changed and sync_mode == "browser":
                msg += " Your Sync from Reddit bookmark has changed: drag the new one to your bookmarks bar."
            st.session_state["upload_log"][kind] = msg
            st.session_state[f"up_{kind}_n"] = n + 1  # clears the uploader
            if changed and (merged.added or mode == "replace") and sync_mode == "direct":
                run_job("sync", job_area)
            st.rerun()


# --- getting data: browser sync (default) or direct sync ------------------------

SYNC_MODES = {"browser": "Browser sync (recommended)", "direct": "Direct sync"}


def current_sync_mode(conn):
    mode = db.get_meta(conn, "sync_mode") or cfg.get("sync_mode", "browser")
    return mode if mode in SYNC_MODES else "browser"


def _save_sync_mode():
    db.set_meta(get_conn(), "sync_mode", st.session_state["sync_mode_radio"])


def bookmarklet_button(url):
    """Draggable "Sync from Reddit" link.

    Streamlit strips javascript: hrefs and inline handlers from HTML, so the URL travels in a data-
    attribute and a small script turns it into the real href once the element is on the page.
    Clicking it here only explains what to do; dragging it to the bookmarks bar keeps just the href.
    """
    st.html(f'<a class="nb-bookmarklet" data-bookmarklet="{esc(url, quote=True)}" '
            f'title="Drag me to your bookmarks bar">⇅ Sync from Reddit</a>')
    st.html("""<script>
(() => {
  if (window.__nbBookmarkletWatcher) return;
  window.__nbBookmarkletWatcher = true;
  const upgrade = () => document.querySelectorAll('a.nb-bookmarklet[data-bookmarklet]').forEach((a) => {
    const url = a.getAttribute('data-bookmarklet');
    if (a.getAttribute('href') === url) return;
    a.setAttribute('href', url);
    if (!a.__nbClick) {
      a.__nbClick = true;
      a.addEventListener('click', (e) => {
        e.preventDefault();
        alert('Drag this button to your bookmarks bar, then click the bookmark while you are on reddit.com.');
      });
    }
  });
  upgrade();
  setInterval(upgrade, 700);
})();
</script>""", unsafe_allow_javascript=True, width="content")


def import_box(conn, version):
    log = st.session_state.get("import_log")
    if log:
        text, warnings = log
        st.success(text)
        for w in warnings[:5]:
            st.warning(w)
    n = st.session_state.setdefault("import_n", 0)
    f = st.file_uploader("Import browser export (.json)", type=["json"], key=f"import_{n}",
                         help="The reddit_export_<date>.json file the bookmark downloaded.")
    if f is None:
        return
    try:
        export = BI.parse_export(f.getvalue())
    except BI.ExportError as exc:
        st.error(str(exc))
        return
    with st.spinner("Importing..."):
        res = BI.import_export(conn, cfg, export, C.ACCOUNTS_FILE, C.COMMENTS_FILE, version)
        for path in service.auto_weekly_report(conn, cfg):
            res.warnings.append(f"Last week's report was saved to reports/{path.name}.")
    when = fmt_ts(res.exported_at)
    if res.snapshots == 0 and res.already_imported:
        text = f"The export from {when} was already imported, so nothing new was added."
    else:
        text = (f"Imported the export from {when}: {res.added} new repl{'y' if res.added == 1 else 'ies'}, "
                f"{res.updated} updated, stats saved for {res.snapshots} across {res.threads} thread"
                f"{'' if res.threads == 1 else 's'}.")
    st.session_state["import_log"] = (text, res.warnings)
    st.session_state["import_n"] = n + 1  # clears the uploader so the file isn't imported again
    st.rerun()


def data_section(conn, job_area):
    st.markdown("### Get data from Reddit")
    st.session_state["sync_mode_radio"] = sync_mode
    st.radio("How to fetch", list(SYNC_MODES), format_func=SYNC_MODES.get, key="sync_mode_radio",
             on_change=_save_sync_mode, label_visibility="collapsed")
    if sync_mode == "browser":
        url, version, n_users, n_links = BM.for_files(C.ACCOUNTS_FILE, C.COMMENTS_FILE, cfg)
        st.caption("Reddit blocks this app's own requests, but reddit.com works in your browser. So your "
                   "browser does the fetching: a bookmark reads your public comments and their threads "
                   "while you're on reddit.com and saves them to a file, which you then import here. Nothing "
                   "is posted, and your login never leaves the browser.")
        st.markdown(f"**1.** Drag this button to your bookmarks bar ({n_users} usernames, {n_links} links):")
        bookmarklet_button(url)
        st.caption(f"Bookmark version **{version}**. It changes whenever accounts.txt or comments.txt change; "
                   f"drag the new one then (and delete the old bookmark).")
        with st.expander("Can't drag it?"):
            st.markdown("Create a new bookmark (Ctrl+D on any page, then **More** / **Edit**), name it "
                        "*Sync from Reddit*, and paste this whole text as its **URL**:")
            st.code(url, language=None, wrap_lines=True)
        st.markdown("**2.** Open **reddit.com** and click the bookmark. A small box shows progress; when it "
                    "finishes, `reddit_export_<date>.json` is downloaded.")
        st.markdown("**3.** Import that file:")
        import_box(conn, version)
        last_import = db.get_meta(conn, "last_browser_import")
        st.caption(f"Last browser import: **{fmt_ts(last_import)}**")
        last = db.get_meta(conn, "last_refresh")
        if parsed.usernames and service.refresh_due(last, time.time(), cfg["auto_refresh_hours"]):
            st.info(f"Your stats are more than {cfg['auto_refresh_hours']:g} hours old. Open reddit.com, click "
                    f"the bookmark and import the file to update them.")
    else:
        st.caption("The app asks Reddit for the data itself. It's automatic, but Reddit blocks it on many "
                   "networks; if you see a 'blocking' message, switch back to Browser sync.")
        c1, c2 = st.columns(2)
        sync_clicked = c1.button("Sync now", type="primary", width="stretch",
                                 help="Re-read accounts.txt and comments.txt and look for new replies")
        refresh_clicked = c2.button("Refresh stats", width="stretch",
                                    help="Take a new snapshot of upvotes, rank and replies for every tracked reply")
        if sync_clicked:
            run_job("sync", job_area)
        elif refresh_clicked:
            run_job("refresh", job_area)
        show_run_log(job_area)


# --- account health ------------------------------------------------------------------

ACCOUNT_BADGE = {H.VISIBLE: "solid-pos", H.POSSIBLY_HIDDEN: "solid-warn", H.SHADOWBANNED: "solid-neg",
                 H.SUSPENDED: "solid-neg", H.UNKNOWN: "muted"}
VISIBILITY_BADGE = {H.PUBLIC: "pos", H.REMOVED: "neg", H.DELETED: "muted", H.NOT_VISIBLE: "neg",
                    H.VIS_UNKNOWN: "muted"}
NOT_CHECKED = "Not checked yet"


def short_date(ts, with_time=False):
    return m.fmt_date(ts, OFFSET, "%d %b, %H:%M" if with_time else "%d %b") if ts else "–"


def health_panel(usernames, current, risk, legacy):
    """Sidebar list: status badge, first-detected date and last check per account."""
    if not usernames:
        st.caption("No usernames in accounts.txt yet.")
        return
    rows = []
    for name in usernames:
        c = current.get(name.lower())
        if c is None:
            old = legacy.get(name.lower())
            badge = ('<span class="badge neg">not found</span>' if old is not None and old["status"] == "not_found"
                     else f'<span class="badge muted">{NOT_CHECKED}</span>')
            meta = "Health is checked by the Sync from Reddit bookmark"
        else:
            badge = f'<span class="badge {ACCOUNT_BADGE[c["status"]]}">{H.ACCOUNT_LABELS[c["status"]]}</span>'
            since = f"Since {short_date(c['first_detected'])} · " if c["first_detected"] else ""
            failed = " (last check failed)" if c.get("last_check_failed") else ""
            meta = f"{since}checked {short_date(c['last_checked'], True)}{failed}"
            if c["status"] in H.PROBLEM_STATUSES:
                meta += f"<br>{esc(c['reason'] or '')}"
            if c["status"] == H.VISIBLE and name.lower() in risk:
                badge = f'<span class="badge warn">At risk</span> {badge}'
                meta += "<br>At risk: " + esc("; ".join(risk[name.lower()]))
        rows.append(f'<div class="nb-health"><div class="top"><span>u/{esc(name)}</span><span>{badge}</span></div>'
                    f'<div class="meta">{meta}</div></div>')
    st.markdown("".join(rows), unsafe_allow_html=True)


def alert_banner(problems):
    if not problems:
        return
    items = "".join(f"<li><b>u/{esc(p['username'])}</b>: Visible → {H.ACCOUNT_LABELS[p['status']]} "
                    f"(detected {short_date(p['detected_at'], True)}). {esc(p['reason'] or '')}</li>"
                    for p in problems)
    st.markdown(f'<div class="nb-alert"><b>⚠ Account visibility problem since the previous import</b>'
                f'<ul style="margin:6px 0 0 18px;padding:0">{items}</ul></div>', unsafe_allow_html=True)


def visibility_label(v):
    return H.VISIBILITY_LABELS.get(v, NOT_CHECKED) if isinstance(v, str) else NOT_CHECKED


def visibility_badge(v, reason):
    if not isinstance(v, str):
        return "–"
    return (f'<span class="badge {VISIBILITY_BADGE.get(v, "muted")}" title="{cell(reason)}">'
            f'{H.VISIBILITY_LABELS.get(v, v)}</span>')


# --- page ------------------------------------------------------------------

inject_css()
theme_watcher()

# Sidebar: always rendered (hidden by CSS in presentation mode) so filter state survives.
with st.sidebar:
    st.markdown("### Account health")
    parsed = parse_accounts(read_text(C.ACCOUNTS_FILE))
    conn = get_conn()
    health_area = st.container()  # filled in once the data is loaded (it needs recent reply stats)
    for w in parsed.warnings:
        st.warning(w)
    st.caption("Edit accounts.txt / comments.txt in the project folder, or upload a list below.")

    sync_mode = current_sync_mode(conn)
    st.divider()
    job_area = st.container()
    with job_area:
        data_section(conn, job_area)

    st.divider()
    st.markdown("### Upload lists")
    upload_box("accounts", job_area)
    upload_box("comments", job_area)

    st.divider()
    st.markdown("### Filters")
    filter_area = st.container()

    st.divider()
    st.markdown("### View")
    if not PRESENT:
        manager_toggle("mv_sidebar")
    st.toggle("Presentation mode", key="presentation", on_change=_on_presentation,
              help="Hides this sidebar, enlarges charts and turns on Manager view. Use the button at the top "
                   "to exit.")
    st.caption("Light / dark theme: ⋮ menu (top right) → Settings.")

if PRESENT:
    _, mv_col, exit_col = st.columns([5, 1, 1])
    with mv_col:
        manager_toggle("mv_main")
    exit_col.button("Exit presentation", on_click=_exit_presentation, width="stretch")

# Direct sync only: auto sync + refresh once per session when data is older than auto_refresh_hours.
# (Browser sync can't run by itself; the sidebar shows a reminder instead.)
conn = get_conn()
if sync_mode == "direct" and not st.session_state.get("auto_checked"):
    st.session_state["auto_checked"] = True
    last = db.get_meta(conn, "last_refresh")
    if parsed.usernames and service.refresh_due(last, time.time(), cfg["auto_refresh_hours"]):
        holder = st.container()
        holder.info(f"Stats are more than {cfg['auto_refresh_hours']:g} hours old, so they're being "
                    f"updated automatically. This can take a few minutes (Reddit allows about one "
                    f"request every {cfg['request_interval_seconds']:g} seconds).")
        run_job("both", holder)
        holder.empty()
        st.rerun()

last_refresh = db.get_meta(conn, "last_refresh")
full = service.tracked_data(conn)
replies_all = full.replies

health_now = H.current_status(full.history)
health_risk = H.at_risk(m.latest_stats(full.replies, full.snaps), time.time(), cfg) if not full.replies.empty else {}
legacy_status = {r["username"].lower(): r for r in conn.execute("SELECT * FROM accounts")}
with health_area:
    health_panel(parsed.usernames, health_now, health_risk, legacy_status)

st.markdown(
    f'<div class="nb-header"><div class="left">{logo_html()}<div class="nb-divider"></div><div>'
    f'<div class="title">Reddit Reply Impact</div>'
    f'<div class="subtitle">How my Reddit replies for Nubra are performing'
    f'{" · Manager view" if MANAGER else ""}</div></div></div>'
    f'<div class="refreshed">Last refreshed: <b>{esc(fmt_ts(last_refresh))}</b></div></div>',
    unsafe_allow_html=True)
alert_banner(H.new_problems(full.history))

if replies_all.empty:
    st.markdown(
        '<div class="nb-card nb-empty"><h3>No replies tracked yet</h3>'
        '<p>Add your Reddit usernames to <code>accounts.txt</code> (one per line) in the project folder, '
        'or upload a list in the sidebar. Optionally add specific comment links to <code>comments.txt</code>.</p>'
        '<p>Then follow <b>Get data from Reddit</b> in the sidebar: drag the <b>Sync from Reddit</b> button to '
        'your bookmarks bar, click it on reddit.com, and import the file it downloads.</p></div>',
        unsafe_allow_html=True)
    st.stop()

# Filters (rendered into the sidebar placeholder now that data is known).
created_local = replies_all["created_utc"].apply(
    lambda t: datetime.fromtimestamp(t, m.tz(OFFSET)).date() if t == t and t else None)
min_d = min((d for d in created_local if d), default=date.today())
today = datetime.fromtimestamp(time.time(), m.tz(OFFSET)).date()
with filter_area:
    accounts_opts = ["All accounts"] + sorted({a for a in replies_all["account"].dropna()}, key=str.lower)
    f_account = st.selectbox("Account", accounts_opts, key="f_account")
    f_dates = st.date_input("Posted between", value=(min_d, today), min_value=min_d,
                            max_value=today, key="f_dates", format="DD/MM/YYYY")
    f_subs = st.multiselect("Subreddit", sorted(replies_all["subreddit"].dropna().unique(), key=str.lower),
                            key="f_subs", placeholder="All subreddits")
    topic_names = list(cfg.get("topics", {}).keys()) + ["Other"]
    f_topics = st.multiselect("Topic", topic_names, key="f_topics", placeholder="All topics")
    vis_names = list(H.VISIBILITY_LABELS.values()) + [NOT_CHECKED]
    f_vis = st.multiselect("Visibility", vis_names, key="f_vis", placeholder="Any visibility",
                           help="How logged-out visitors see each reply (checked by the Sync from Reddit bookmark)")

mask = created_local.notna()
if f_account != "All accounts":
    mask &= replies_all["account"] == f_account
if isinstance(f_dates, (list, tuple)) and len(f_dates) == 2:
    mask &= created_local.apply(lambda d: d is not None and f_dates[0] <= d <= f_dates[1])
if f_subs:
    mask &= replies_all["subreddit"].isin(f_subs)
if f_topics:
    mask &= replies_all["topic"].isin(f_topics)
if f_vis:
    mask &= replies_all["visibility"].apply(visibility_label).isin(f_vis)
data = full.subset(replies_all[mask].copy())
replies, snaps = data.replies, data.snaps
now = time.time()

if replies.empty:
    st.info("No replies match these filters. Try widening the date range or clearing the filters.")
    st.stop()

latest = m.latest_stats(replies, snaps, data.responses, data.subreddits, OFFSET)
latest["size"] = latest["subscribers"].apply(lambda s: m.size_bucket(s, cfg["subreddit_size_buckets"]))

# 2. KPI cards
k = m.kpis_with_change(replies, snaps, now, OFFSET, data.responses, data.scans)
top_level = latest[latest["depth"].fillna(-1).astype(float) == 0]
others = data.responses
brands = m.brand_mentions_at(replies, data.scans, now)
hidden_n, hidden_by = H.hidden_breakdown(replies)
# top-comment rate leaves out hidden replies, so the card's note does too
top_level = H.publicly_visible(top_level)


def card(label, key, value, note="", unit="", decimals=0):
    delta = "" if MANAGER else delta_html(k[key][1], unit=unit, decimals=decimals)
    return (f'<div class="nb-card nb-kpi"><div class="label">{esc(label)}</div><div class="value">{value}</div>'
            f'{delta}' + (f'<div class="note">{esc(note)}</div>' if note else "") + '</div>')


cards = [
    card("Replies posted", "replies_posted", fmt_num(k["replies_posted"][0])),
    card("Total upvotes", "total_upvotes", fmt_num(k["total_upvotes"][0]), "Net (up minus down)"),
    card("Avg upvotes per reply", "avg_upvotes", fmt_num(k["avg_upvotes"][0], 1), decimals=1),
    card("Replies received", "replies_received", fmt_num(k["replies_received"][0]), "Direct replies to my comments"),
    card("Thread reach", "thread_reach", fmt_num(k["thread_reach"][0]),
         "Sum of thread comment counts – rough visibility proxy"),
    card("Top-comment rate", "top_comment_rate", pct(k["top_comment_rate"][0]),
         f"{int((top_level['thread_rank'] == 1).sum())} of {len(top_level)} top-level replies ranked #1",
         unit=" pts"),
    card("OP engagement rate", "op_engagement_rate", pct(k["op_engagement_rate"][0]),
         f"{int(latest['op_replied'].sum())} replies got a reply from the thread's poster", unit=" pts"),
    card("Positive reply rate", "positive_reply_rate", pct(k["positive_reply_rate"][0]),
         f"{int((others['sentiment'] == 'positive').sum())} of {len(others)} replies from others", unit=" pts"),
    card("Nubra share of broker mentions", "nubra_share", pct(k["nubra_share"][0]),
         f"{brands.get('Nubra', 0)} of {sum(brands.values())} broker mentions by others", unit=" pts"),
    card("Disclosed when mentioning Nubra", "disclosure_rate", pct(k["disclosure_rate"][0]),
         f"{int(replies['mentions_nubra'].sum())} replies mention Nubra", unit=" pts"),
    card("Hidden replies", "hidden_replies", fmt_num(k["hidden_replies"][0]),
         f"{hidden_n} repl{'y' if hidden_n == 1 else 'ies'} not visible to the public"
         + (": " + ", ".join(f"{v} {lbl.lower()}" for lbl, v in hidden_by.items()) if hidden_by else "")
         + ". Left out of reach, top-comment rate and upvote averages."),
]
st.markdown('<div class="nb-kpis">' + "".join(cards) + '</div>', unsafe_allow_html=True)
if not MANAGER:
    st.markdown('<div class="nb-sub" style="margin-top:8px">"This week" = since Monday 00:00 IST.</div>',
                unsafe_allow_html=True)

CH = 440 if PRESENT else 300

# 3. Activity over time (not in Manager view)
if not MANAGER:
    st.markdown('<div class="nb-section">Activity over time</div>', unsafe_allow_html=True)
    weeks = m.weekly_activity(replies, snaps, now, OFFSET)
    a1, a2 = st.columns(2, gap="large")
    with a1:
        with st.container(border=True):
            card_title("Replies posted per week", "Count of tracked replies by the week they were posted")
            plot(week_bar(weeks, "replies_posted", "Replies posted", CH))
    with a2:
        with st.container(border=True):
            card_title("Upvotes gained per week", "Net change in upvotes across all tracked replies")
            if m.distinct_snapshot_times(snaps) < 2:
                placeholder("Not enough history yet",
                            "Upvote growth appears after the second stats refresh. "
                            "Stats refresh automatically every few hours.", CH - 40)
            else:
                plot(week_bar(weeks, "upvotes_gained", "Upvotes gained", CH))

# What works best
st.markdown('<div class="nb-section">What works best</div>'
            '<div class="nb-sub">Average upvotes per reply, using the latest stats. Replies with unknown '
            'values (not refreshed yet) are left out.</div>', unsafe_allow_html=True)
SH = 340 if PRESENT else 250
size_order = [name for _, name in sorted(cfg["subreddit_size_buckets"], key=lambda b: b[0])]
w_cols = st.columns(3, gap="large")
for col, (title, sub, column, order) in zip(w_cols, [
        ("By reply timing", "Hours between the thread being posted and my reply", "timing", m.TIMING_BUCKETS),
        ("By reply depth", "Top-level comment vs reply to someone else", "depth_label",
         list(m.DEPTH_LABELS.values())),
        ("By subreddit size", "Members, from r/<sub>/about (refreshed daily)", "size", size_order)]):
    with col:
        with st.container(border=True):
            card_title(title, sub)
            g = m.works_best(latest, column, order)
            if g.empty:
                placeholder("No data yet", "Appears after the next stats refresh.", SH - 40)
            else:
                plot(bucket_bars(g, column, SH))

st.markdown('<div class="nb-section">Conversation share</div>'
            '<div class="nb-sub">Comments by other people in the threads I replied in (plus each thread\'s own '
            'title and text), counted once per brand they mention. Names are listed in config.json.</div>',
            unsafe_allow_html=True)
with st.container(border=True):
    if not brands:
        placeholder("No broker mentions found yet", "Appears after the next stats refresh.", 160)
    else:
        card_title("Broker mentions by other users",
                   f"Nubra share: {pct(k['nubra_share'][0])} of {sum(brands.values())} mentions")
        plot(brand_bars(brands, max(200, 36 * len(brands) + 40)))


def group_section(title, column, sub):
    st.markdown(f'<div class="nb-section">{title}</div><div class="nb-sub">{sub}</div>', unsafe_allow_html=True)
    g = m.by_group(replies, snaps, column)
    h = max(CH - 60, 34 * min(len(g), 10) + 40)
    c1, c2 = st.columns(2, gap="large")
    with c1:
        with st.container(border=True):
            card_title("Average upvotes per reply", "Latest net upvotes, averaged")
            plot(group_bars(g, column, "avg_upvotes", "Avg upvotes", h))
    with c2:
        with st.container(border=True):
            card_title("Replies posted", "Same order as the left chart")
            plot(group_bars(g, column, "replies", "Replies", h, fmt=",.0f"))


group_section("Best subreddits", "subreddit", "Top 10 by average upvotes, using the latest stats.")
group_section("Best topics", "topic", "Topics are detected automatically from keywords in config.json.")

# 6. Top replies
st.markdown('<div class="nb-section">Top replies</div>', unsafe_allow_html=True)
s1, s2, s3 = st.columns([3, 1, 1])
query = s1.text_input("Search", placeholder="Search thread titles, reply text, subreddits, accounts...",
                      label_visibility="collapsed")
sort_opts = ["Upvotes", "Thread rank", "First 24h upvotes", "Replies received"] + ([] if MANAGER else ["Newest"])
sort_by = s2.selectbox("Sort by", sort_opts, label_visibility="collapsed")
limit = s3.selectbox("Show", [25, 50, 100, "All"], label_visibility="collapsed")
table = latest
if query:
    q = query.lower()
    hay = (table["thread_title"].fillna("") + " " + table["text"].fillna("") + " " +
           table["subreddit"].fillna("") + " " + table["account"].fillna("")).str.lower()
    table = table[hay.str.contains(q, regex=False)]
sort_col, ascending = {"Upvotes": ("score", False), "Thread rank": ("thread_rank", True),
                       "First 24h upvotes": ("upvotes_24h", False), "Replies received": ("replies_count", False),
                       "Newest": ("created_utc", False)}[sort_by]
table = table.sort_values(sort_col, ascending=ascending, na_position="last")
if limit != "All":
    table = table.head(int(limit))


def status_badge(s):
    cls = {"live": "pos", "removed": "neg"}.get(s, "muted")
    return f'<span class="badge {cls}">{cell(s)}</span>'


def rank_cell(r):
    if r.depth == 1:
        return '<span class="badge muted">Nested reply</span>'
    rank = r.thread_rank
    if rank is None or rank != rank:
        return "–"
    of = f" of {int(r.top_level_count)}" if r.top_level_count == r.top_level_count and r.top_level_count else ""
    badge = ('<span class="badge accent">Top comment</span> ' if rank == 1 else
             '<span class="badge pos">Top 3</span> ' if rank <= 3 else "")
    return f"{badge}#{int(rank)}{of}"


def small(text):
    return f"<div style='font-size:11px;color:var(--muted)'>{text}</div>" if text else ""


def members(n):
    if n is None or n != n:
        return ""
    return f"{n / 1e6:.1f}M members" if n >= 1e6 else f"{n / 1e3:.0f}k members" if n >= 1e3 else f"{int(n)} members"


def timing_text(h):
    if h is None or h != h:
        return "–"
    return f"{h * 60:.0f} min" if h < 1 else f"{h:.1f}h" if h < 48 else f"{h / 24:.0f} days"


rows = []
for i, r in enumerate(table.itertuples(index=False), start=1):
    mention = '<span class="badge accent">Mentions Nubra</span>' if r.mentions_nubra else "–"
    if r.mentions_nubra:
        disclosed = ('<span class="badge pos">Disclosed</span>' if r.disclosed
                     else '<span class="badge neg">Not disclosed</span>')
    else:
        disclosed = "–"
    title = cell(r.thread_title)
    thread = (f'<a href="{esc(r.thread_link)}" target="_blank" title="{cell(r.text)}">{title}</a>'
              if text_or_empty(r.thread_link) else title)
    reply_link = (f' <a href="{esc(r.permalink)}" target="_blank" style="font-size:11px">reply ↗</a>'
                  if text_or_empty(r.permalink) else "")
    ratio = f"thread {r.upvote_ratio * 100:.0f}% upvoted" if r.upvote_ratio == r.upvote_ratio and r.upvote_ratio else ""
    mood = " · ".join(p for p in (f"{r.resp_positive} positive" if r.resp_positive else "",
                                  f"{r.resp_negative} negative" if r.resp_negative else "") if p)
    op = '<span class="badge pos">OP replied</span>' if r.op_replied else "–"
    date_td = "" if MANAGER else f"<td style='white-space:nowrap'>{m.fmt_date(r.created_utc, OFFSET)}</td>"
    rows.append(
        f"<tr><td class='num'>{i}</td><td>u/{cell(r.account)}</td><td>{cell(r.subreddit)}{small(members(r.subscribers))}</td>"
        f"<td class='wrap'>{thread}{reply_link}{small(ratio)}</td>{date_td}"
        f"<td class='num'>{fmt_num(r.score)}</td><td class='num'>{fmt_num(r.upvotes_24h)}</td>"
        f"<td>{rank_cell(r)}</td><td class='num'>{timing_text(r.hours_after_post)}</td>"
        f"<td class='num'>{fmt_num(r.replies_count)}{small(mood)}</td><td>{op}</td>"
        f"<td>{cell(r.topic)}</td><td>{mention}</td><td>{disclosed}</td><td>{status_badge(r.status)}</td>"
        f"<td>{visibility_badge(r.visibility, r.visibility_reason)}</td></tr>")
headers = (['<th class="num">#</th>', "<th>Account</th>", "<th>Subreddit</th>", "<th>Thread</th>"]
           + ([] if MANAGER else ["<th>Date</th>"])
           + ['<th class="num">Upvotes</th>', '<th class="num" title="Upvotes in the first 24 hours">First 24h</th>',
              "<th>Rank in thread</th>", '<th class="num" title="Hours after the thread was posted">Replied after</th>',
              '<th class="num">Replies</th>', "<th>OP</th>", "<th>Topic</th>", "<th>Nubra</th>",
              "<th>Disclosed</th>", '<th title="What you see when logged in">Status</th>',
              '<th title="What logged-out visitors see (from the bookmark\'s checks)">Visibility</th>'])
st.markdown(
    '<div class="nb-table-wrap"><table class="nb-table"><thead><tr>' + "".join(headers) + '</tr></thead><tbody>'
    + ("".join(rows) or f"<tr><td colspan='{len(headers)}' style='text-align:center'>No replies match your "
                        f"search.</td></tr>")
    + "</tbody></table></div>", unsafe_allow_html=True)
st.caption(f"Showing {len(table)} of {len(latest)} replies. Hover a thread title to see the start of the reply. "
           f"'First 24h' needs a snapshot taken within a day of posting.")

if not MANAGER:
    with st.expander("Correct an automatic classification (optional)"):
        options = latest.sort_values("created_utc", ascending=False)
        labels = {r.comment_id: f"{m.fmt_date(r.created_utc, OFFSET)} · {r.subreddit} · "
                                f"{text_or_empty(r.thread_title)[:70]} · u/{r.account}" for r in options.itertuples()}
        pick = st.selectbox("Reply", list(labels), format_func=lambda c: labels[c])
        current = conn.execute("SELECT * FROM classifications WHERE comment_id=?", (pick,)).fetchone()
        auto_topic = current["topic"] if current and current["topic"] else "Other"
        yn = ["Automatic", "Yes", "No"]

        def _idx(v):
            return 0 if v is None else (1 if v else 2)

        o1, o2, o3 = st.columns(3)
        new_topic = o1.selectbox("Topic", ["Automatic"] + topic_names,
                                 index=(["Automatic"] + topic_names).index(current["override_topic"])
                                 if current and current["override_topic"] in topic_names else 0,
                                 help=f"Automatic currently says: {auto_topic}")
        new_mention = o2.selectbox("Mentions Nubra", yn,
                                   index=_idx(current["override_mentions"] if current else None))
        new_disc = o3.selectbox("Disclosed", yn, index=_idx(current["override_disclosed"] if current else None))
        if st.button("Save correction"):
            conv = {"Automatic": None, "Yes": True, "No": False}
            db.save_override(conn, pick, conv[new_mention], conv[new_disc],
                             None if new_topic == "Automatic" else new_topic)
            st.success("Saved. Choose 'Automatic' everywhere to undo.")
            st.rerun()

# 7. Removed or deleted
st.markdown('<div class="nb-section">Removed or deleted replies</div>'
            '<div class="nb-sub">Shows which subreddits remove replies. "Removed" = taken down by moderators, '
            'Reddit or a filter; "deleted" = deleted by the account.</div>', unsafe_allow_html=True)
gone = latest[latest["status"].isin(["removed", "deleted"])].sort_values("created_utc", ascending=False)
if gone.empty:
    st.markdown('<div class="nb-card" style="color:var(--muted)">None so far: every tracked reply is live.</div>',
                unsafe_allow_html=True)
else:
    per_sub = latest.groupby("subreddit").agg(total=("comment_id", "count"),
                                              gone=("status", lambda s: s.isin(["removed", "deleted"]).sum()))
    per_sub = per_sub[per_sub["gone"] > 0].sort_values("gone", ascending=False)
    st.markdown(" ".join(f'<span class="badge neg" style="margin:0 6px 8px 0">{esc(sub)}: {int(r.gone)} of '
                         f'{int(r.total)} removed/deleted</span>' for sub, r in per_sub.iterrows()),
                unsafe_allow_html=True)
    grows = "".join(
        f"<tr><td>{status_badge(r.status)}</td><td>{cell(r.subreddit)}</td>"
        f"<td class='wrap'>{link(r.thread_link, cell(r.thread_title))}</td><td>u/{cell(r.account)}</td>"
        + ("" if MANAGER else f"<td style='white-space:nowrap'>{m.fmt_date(r.created_utc, OFFSET)}</td>")
        + f"<td class='wrap' style='color:var(--muted)'>{cell(text_or_empty(r.text)[:140])}</td>"
          f"<td>{cell(r.note)}</td></tr>"
        for r in gone.itertuples())
    st.markdown('<div class="nb-table-wrap"><table class="nb-table"><thead><tr><th>Status</th><th>Subreddit</th>'
                '<th>Thread</th><th>Account</th>' + ("" if MANAGER else "<th>Date</th>")
                + '<th>Reply (saved text)</th><th>Note</th></tr></thead>'
                f'<tbody>{grows}</tbody></table></div>', unsafe_allow_html=True)

# Account health history
st.markdown('<div class="nb-section">Account health history</div>'
            '<div class="nb-sub">Every status change found by the bookmark\'s logged-out checks, newest first. '
            'An account\'s first check is listed as its starting status; failed checks are not changes.</div>',
            unsafe_allow_html=True)
changes = H.status_changes(full.history)
if changes.empty:
    st.markdown('<div class="nb-card" style="color:var(--muted)">No health checks yet. They run every time you use '
                'the Sync from Reddit bookmark and import the file.</div>', unsafe_allow_html=True)
else:
    hrows = "".join(
        f"<tr><td style='white-space:nowrap'>{short_date(r.changed_at, True)}</td><td>u/{cell(r.username)}</td>"
        f"<td>{'First check' if not isinstance(r.from_status, str) else H.ACCOUNT_LABELS[r.from_status]} → "
        f"<span class='badge {ACCOUNT_BADGE[r.to_status]}'>{H.ACCOUNT_LABELS[r.to_status]}</span></td>"
        f"<td class='wrap' style='color:var(--muted)'>{cell(r.reason)}</td></tr>"
        for r in changes.sort_values("changed_at", ascending=False).itertuples(index=False))
    st.markdown('<div class="nb-table-wrap"><table class="nb-table"><thead><tr><th>Detected</th><th>Account</th>'
                f'<th>Change</th><th>Reason</th></tr></thead><tbody>{hrows}</tbody></table></div>',
                unsafe_allow_html=True)

# Weekly report
st.markdown('<div class="nb-section">Weekly report</div>'
            '<div class="nb-sub">Last week\'s reports (detailed + manager version) are saved automatically to the '
            'reports folder. Copy one with the icon at the top-right of the box and paste it into Slack.</div>',
            unsafe_allow_html=True)
this_week = m.week_start(now, OFFSET)
week_opts = [this_week - i * m.WEEK for i in range(0, 9)]
w1, w2, w3 = st.columns([3, 2, 1.3])
chosen = w1.selectbox(
    "Week", week_opts, index=1, label_visibility="collapsed",
    format_func=lambda w: ("This week so far" if w == this_week else
                           "Last week" if w == this_week - m.WEEK else "Week of") +
    f" ({m.fmt_date(w, OFFSET, '%d %b')} – {m.fmt_date(w + m.WEEK - 1, OFFSET, '%d %b %Y')})")
if MANAGER:
    w2.markdown('<div class="nb-sub" style="padding-top:8px">Manager version</div>', unsafe_allow_html=True)
    manager_report = True
else:
    manager_report = w2.radio("Version", ["Detailed (for me)", "Manager version"], horizontal=True,
                              label_visibility="collapsed") == "Manager version"
# Reports always cover all tracked replies, not the sidebar filters.
build = R.build_manager_report if manager_report else R.build_report
report_text = build(full.replies, full.snaps, chosen, now, OFFSET, full.responses, full.scans, full.history, cfg)
if w3.button("Generate weekly report", type="primary", width="stretch"):
    path = R.save_report(C.REPORTS_DIR, chosen, OFFSET, report_text, partial=chosen == this_week,
                         manager=manager_report)
    st.success(f"Saved to reports/{path.name}")
st.code(report_text, language="markdown")

st.markdown(
    '<div class="nb-footer"><b>How to read these numbers.</b> Reddit doesn\'t show impressions or link clicks for '
    'other people\'s posts, so <b>thread comment count is only a rough visibility proxy</b>. Upvotes are '
    '<b>net</b> (upvotes minus downvotes, and Reddit fuzzes them slightly). Rank, OP replies and broker mentions '
    'come from the first ~500 comments Reddit returns for each thread; sentiment is a simple keyword match. '
    'Only public comments are tracked; removed, private-subreddit and suspended-account content can\'t be read. '
    'Times are IST.</div>',
    unsafe_allow_html=True)
