"""
Streamlit dashboard over the DuckDB tables built by ingest.py.

Usage:
    streamlit run dashboard.py
"""
import os
import subprocess
from datetime import timedelta

import altair as alt
import duckdb
import numpy as np
import pandas as pd
import streamlit as st

import config
import ingest
import search_query_deep_dive as sqdd
import product_tracker as pt
import search_query_intelligence as sqi

st.set_page_config(page_title="Instamart Ads Dashboard", layout="wide")

# Auto-rebuild the DuckDB from the gzipped archives in data/processed/ if the
# DB file is missing. On Render's free tier the disk is ephemeral, so the DB
# is wiped on every redeploy - this repopulates it from the source data that
# IS committed to git (as .csv.gz), so users don't have to re-upload monthly.
if not config.DB_PATH.exists():
    if list(config.PROCESSED_DIR.glob("*.csv.gz")):
        with st.spinner("Rebuilding database from archived exports..."):
            n = ingest.rebuild_from_processed()
            st.toast(f"Rebuilt database from {n} archived file(s)")


def format_inr(value, decimals: int = 0) -> str:
    """Indian digit grouping (lakhs/crores), e.g. 2978378 -> '29,78,378'.

    Python's locale module isn't reliable for this across platforms - 'en_IN'
    often isn't installed at all in minimal Linux containers like Render's,
    so this groups digits by hand instead of depending on system locale data.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(round(value, decimals))
    frac = f"{value:.{decimals}f}".split(".")[1] if decimals else None
    s = str(whole)
    if len(s) > 3:
        last3, rest = s[-3:], s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = ",".join(parts) + "," + last3
    return f"{sign}{s}" + (f".{frac}" if frac else "")


def format_df_inr(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """Copy of df with the given numeric columns rendered as Indian-grouped
    strings for display - Streamlit's dataframe/column_config can't express
    Indian digit grouping natively, so this pre-formats them as text. Trades
    away native numeric column-sort (becomes lexicographic) for the format."""
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = df[c].apply(format_inr)
    return df


def commit_and_push_data() -> str | None:
    """After a dashboard upload, commit the new .csv.gz archives in
    data/processed/ and push them to GitHub so the source data survives
    Render's ephemeral filesystem on the next redeploy - the DB itself
    isn't pushed (it auto-rebuilds from the archives on startup).

    Requires GITHUB_TOKEN (a fine-grained token scoped to just this repo's
    Contents: read/write - see README) set as an env var. Also needs
    GITHUB_REPO ("owner/repo") when running outside a checkout that already
    has an origin remote. Returns None and does nothing if the token isn't
    set (e.g. local dev, where the developer's own `git push` workflow
    covers this). Never surfaces the token in any message shown to the UI,
    even on failure - only a generic string.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None

    repo_dir = str(config.BASE_DIR)

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)

    # Prefer whatever remote the deploy was cloned from; fall back to an
    # explicit GITHUB_REPO env var (owner/repo) for environments that don't
    # clone from GitHub directly.
    remote = run("remote", "get-url", "origin")
    if remote.returncode == 0 and remote.stdout.strip().startswith("https://"):
        remote_url = remote.stdout.strip()
    elif os.environ.get("GITHUB_REPO"):
        remote_url = f"https://github.com/{os.environ['GITHUB_REPO']}.git"
    else:
        return "Auto-push skipped: no https:// git remote configured and GITHUB_REPO not set."
    authed_remote = remote_url.replace("https://", f"https://x-access-token:{token}@", 1)

    run("config", "user.email", "dashboard-bot@snackible.com")
    run("config", "user.name", "Instamart Dashboard Bot")
    run("add", "data/processed/")

    status = run("status", "--porcelain", "data/processed/")
    if not status.stdout.strip():
        return "No new archives to commit."

    # Count what's actually being committed so the UI can show it.
    new_files = [line.split()[-1] for line in status.stdout.strip().splitlines()]

    commit = run("commit", "-m", f"Auto-add {len(new_files)} new archive(s) from dashboard upload")
    if commit.returncode != 0:
        return "Git commit failed - see server logs for details."

    push = subprocess.run(["git", "-C", repo_dir, "push", authed_remote, "HEAD:main"],
                           capture_output=True, text=True)
    if push.returncode != 0:
        return "Git push failed - check GITHUB_TOKEN is valid and has write access to this repo."

    return (f"Pushed {len(new_files)} new archive(s) to GitHub. Render will redeploy "
            f"in ~2 min - your data is now permanent.")

# --- Access control ------------------------------------------------------------
# Only enforced when DASHBOARD_PASSWORD is set (e.g. on a hosted deployment) -
# local `streamlit run` stays open with no password, same "optional secret"
# convention as hamper-personalizer's CRON_SECRET.
_required_password = os.environ.get("DASHBOARD_PASSWORD")
if _required_password and not st.session_state.get("authenticated"):
    st.title("Instamart Ads Dashboard")
    entered = st.text_input("Password", type="password")
    if st.button("Enter"):
        if entered == _required_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

# --- Theme -------------------------------------------------------------------
# Cream/beige base + blue accent design (per client UI spec). Every card,
# button, table and chart pulls from these tokens so the whole app stays
# on the same palette regardless of section.
PALETTES = {
    "light": dict(
        bg="#f5f4f0",       # page background (warm cream)
        surface="#ffffff",   # cards/sidebar/topbar (pure white)
        surface2="#f0efe9",  # sunken inputs, hover states
        border="#e2e0d8",    # 1px card borders + separators
        text="#1a1916",      # primary text (near-black)
        text2="#6b6960",     # secondary text
        text3="#9b9a93",     # muted (labels, sub-copy)
        accent="#2563eb",    # brand blue - buttons, links, active nav
        accent2="#1d4ed8",   # accent hover
        positive="#15803d",  # green text tone
        positive_bg="#f0fdf4",
        negative="#b91c1c",  # red text tone
        negative_bg="#fef2f2",
        warn="#b45309",      # amber text tone
        warn_bg="#fffbeb",
    ),
    "dark": dict(
        bg="#111110", surface="#1c1b19", surface2="#252422", border="#2e2d29",
        text="#f0efe9", text2="#a8a79f", text3="#6b6a62",
        accent="#3b82f6", accent2="#2563eb",
        positive="#4ade80", positive_bg="#052e16",
        negative="#f87171", negative_bg="#450a0a",
        warn="#fbbf24", warn_bg="#1c1100",
    ),
}
# Accent colour map used for KPI-card tints and section chips. Blue is
# the brand; the others are the standard status semantics from the
# reference UI (green = good, amber = watch, red = bad).
ACCENTS = {
    "light": {
        "blue":   dict(fg="#2563eb", bg="#eff6ff", border="#bfdbfe"),
        "green":  dict(fg="#15803d", bg="#f0fdf4", border="#bbf7d0"),
        "amber":  dict(fg="#b45309", bg="#fffbeb", border="#fde68a"),
        "red":    dict(fg="#b91c1c", bg="#fef2f2", border="#fecaca"),
    },
    "dark": {
        "blue":   dict(fg="#93c5fd", bg="#1e3a5f", border="#1e40af"),
        "green":  dict(fg="#4ade80", bg="#052e16", border="#166534"),
        "amber":  dict(fg="#fbbf24", bg="#1c1100", border="#78350f"),
        "red":    dict(fg="#f87171", bg="#450a0a", border="#991b1b"),
    },
}
# Categorical chart palette - blue-forward, matches the reference UI's
# calm-toned chart aesthetic (no neon, no bright pinks).
BOLD_CATEGORICAL = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed",
                    "#0891b2", "#65a30d", "#db2777"]

if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False
st.sidebar.toggle("🌙 Dark mode", key="dark_mode")
theme_mode = "dark" if st.session_state.dark_mode else "light"
pal = PALETTES[theme_mode]
accents = ACCENTS[theme_mode]

# The whole visual system is expressed as CSS variables on :root so every
# subsequent rule (KPI cards, chart wrappers, tables) reads from the same
# tokens - matching the CSS-variable convention in the reference UI.
st.markdown(f"""
<style>
:root {{
  --bg: {pal['bg']}; --surface: {pal['surface']}; --surface2: {pal['surface2']};
  --border: {pal['border']}; --text: {pal['text']}; --text2: {pal['text2']}; --text3: {pal['text3']};
  --accent: {pal['accent']}; --accent2: {pal['accent2']};
  --green: {pal['positive']}; --green-bg: {pal['positive_bg']};
  --red: {pal['negative']}; --red-bg: {pal['negative_bg']};
  --amber: {pal['warn']}; --amber-bg: {pal['warn_bg']};
  --shadow: 0 1px 3px rgba(0,0,0,{'0.3' if theme_mode == 'dark' else '0.08'});
  --radius: 10px; --radius-sm: 6px;
}}

/* Page + typography */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
  background: var(--bg) !important;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
}}
.stApp p, .stApp span, .stApp label, .stApp li, .stApp div {{ color: var(--text); }}
[data-testid="stMarkdownContainer"] p {{ font-size: 13px; color: var(--text2); }}

/* Hide the specific unwanted toolbar children (deploy button, main
   menu, decoration bar, footer) WITHOUT hiding the toolbar wrapper
   itself - because stExpandSidebarButton (the pop-out that reopens
   a collapsed sidebar) lives inside stToolbar, and hiding the
   toolbar traps the user in the collapsed state. */
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"],
[data-testid="stStatusWidget"], [data-testid="stDecoration"],
#MainMenu, footer {{
  visibility: hidden !important; display: none !important; height: 0 !important;
}}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stToolbarActions"] {{
  background: transparent !important;
}}
/* Force the sidebar collapse chevron AND the pop-out reopen button
   visible + clickable regardless of theme/state. */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stExpandSidebarButton"] {{
  visibility: visible !important; display: flex !important; opacity: 1 !important;
  color: var(--text) !important; z-index: 1000; pointer-events: auto !important;
}}
[data-testid="stAppViewContainer"] > .main > .block-container {{
  padding: 20px 24px 60px !important; max-width: none;
}}

/* Sidebar - restyled to look like the reference's fixed nav column */
[data-testid="stSidebar"] {{
  background: var(--surface) !important;
  border-right: 1px solid var(--border);
  padding-top: 0;
}}
[data-testid="stSidebar"] > div {{ padding-top: 8px; }}
[data-testid="stSidebar"] * {{ color: var(--text); font-size: 13px; }}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{
  font-size: 11px !important; font-weight: 700 !important; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--text3) !important; margin: 12px 0 4px !important;
  border: none !important; padding: 0 !important; background: none !important;
}}
[data-testid="stSidebar"] label {{ font-size: 11px !important; color: var(--text3); font-weight: 500; }}

/* Section headings (compact, no gradient, no coloured bars) */
h1, [data-testid="stMarkdownContainer"] h1 {{
  font-size: 20px !important; font-weight: 700 !important; color: var(--text) !important;
  letter-spacing: -0.3px; margin: 0 0 4px !important; padding: 0 !important;
  border: none !important; background: none !important;
}}
h1::after {{ display: none !important; content: none !important; }}
h2, [data-testid="stMarkdownContainer"] h2 {{
  font-size: 15px !important; font-weight: 600 !important; color: var(--text) !important;
  margin: 24px 0 6px !important; padding: 0 !important; border: none !important;
}}
h3, [data-testid="stMarkdownContainer"] h3 {{
  font-size: 13px !important; font-weight: 600 !important; color: var(--text) !important;
  margin: 20px 0 4px !important; padding: 0 !important; border: none !important;
}}

/* Widgets - inputs, selects, dates all sit on the sunken surface2 */
.stTextInput input, .stDateInput input, .stNumberInput input,
[data-baseweb="select"] > div, [data-baseweb="input"] > div {{
  background: var(--surface2) !important; border: 1px solid var(--border) !important;
  border-radius: var(--radius-sm) !important; font-size: 12px !important;
  color: var(--text) !important; min-height: 32px;
}}
[data-baseweb="tag"] {{
  background: var(--accent) !important; color: #fff !important;
  border-radius: 4px !important; font-size: 11px !important;
}}
[data-baseweb="tag"] span {{ color: #fff !important; }}

/* Buttons - primary is accent blue with hover to darker accent */
.stButton > button, .stDownloadButton > button {{
  background: var(--accent) !important; color: #ffffff !important;
  border: none !important; border-radius: var(--radius-sm) !important;
  font-weight: 500 !important; font-size: 12px !important; padding: 6px 14px !important;
  min-height: auto !important;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
  background: var(--accent2) !important; color: #ffffff !important;
}}
.stButton > button p {{ color: #ffffff !important; font-size: 12px !important; }}

/* Radio (view switcher) - buttons that highlight when selected */
[data-testid="stSidebar"] [role="radiogroup"] > label {{
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border-radius: var(--radius-sm); cursor: pointer; margin-bottom: 2px;
  transition: background 0.15s;
}}
[data-testid="stSidebar"] [role="radiogroup"] > label:hover {{ background: var(--surface2); }}
[data-testid="stSidebar"] [role="radiogroup"] > label[data-checked="true"] {{
  background: var(--accent); color: #fff !important;
}}
[data-testid="stSidebar"] [role="radiogroup"] > label[data-checked="true"] * {{ color: #fff !important; }}

/* Toggle (dark mode) styling */
[data-testid="stSidebar"] [data-baseweb="checkbox"] {{ font-size: 12px; }}

/* KPI cards - the reference UI's summary tiles */
.kpi-grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px; margin-bottom: 20px; max-width: none;
}}
.kpi-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 14px 16px; box-shadow: var(--shadow);
}}
.kpi-label {{
  font-size: 11px; color: var(--text3); font-weight: 500;
  margin-bottom: 6px; text-transform: none; letter-spacing: 0;
}}
.kpi-value {{
  font-size: 22px; font-weight: 700; color: var(--text);
  letter-spacing: -0.5px; margin-top: 0;
}}
.kpi-delta-up {{ color: var(--green); font-size: 11px; font-weight: 500; margin-top: 4px; }}
.kpi-delta-down {{ color: var(--red); font-size: 11px; font-weight: 500; margin-top: 4px; }}
.kpi-sub {{ font-size: 11px; color: var(--text3); margin-top: 4px; }}

/* Chart & table cards - the visual container the reference uses everywhere */
div[data-testid="stVegaLiteChart"], div[data-testid="stAltairChart"], .stVegaLite {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 16px; box-shadow: var(--shadow);
  margin-bottom: 14px;
}}
div[data-testid="stDataFrame"], div[data-testid="stTable"] {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow);
  margin-bottom: 20px;
}}

/* Expander (upload panel) - flat white card */
[data-testid="stExpander"] {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow); margin-bottom: 20px;
}}
[data-testid="stExpander"] summary {{ font-size: 13px; font-weight: 600; padding: 12px 16px; }}

/* Alerts - success/warning/error/info styled with the palette tokens */
[data-testid="stAlert"] {{
  border-radius: var(--radius); border: 1px solid var(--border);
  padding: 12px 14px; box-shadow: var(--shadow); font-size: 12px;
}}

/* Tabs - the reference UI's underline-only tab row */
[data-baseweb="tab-list"] {{
  border-bottom: 1px solid var(--border); gap: 0;
}}
[data-baseweb="tab"] {{
  padding: 8px 14px !important; font-size: 12px !important;
  color: var(--text3) !important; font-weight: 500 !important;
  border-bottom: 2px solid transparent !important;
}}
[data-baseweb="tab"][aria-selected="true"] {{
  color: var(--accent) !important; border-bottom-color: var(--accent) !important;
}}

/* Metric widget (st.metric on the deep-dive summary row) */
[data-testid="stMetric"] {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px 14px; box-shadow: var(--shadow);
}}
[data-testid="stMetricLabel"] p {{ color: var(--text3) !important; font-size: 11px !important; }}
[data-testid="stMetricValue"] {{ color: var(--text) !important; font-size: 22px !important; font-weight: 700 !important; }}

/* Dividers - a hairline, not the default Streamlit slab */
hr {{ border: none !important; border-top: 1px solid var(--border) !important; margin: 24px 0 !important; }}
</style>
""", unsafe_allow_html=True)

st.title("Instamart Ads Dashboard")

# --- Upload panel ------------------------------------------------------------
with st.expander("📤 Upload new CSV/Excel exports", expanded=not config.DB_PATH.exists()):
    uploaded_files = st.file_uploader(
        "Drop IM_SUMMARY_*/IM_GRANULAR_*/IM_..._SEARCH_QUERY_* files here, .csv or .xlsx "
        "(filename must contain SUMMARY, GRANULAR, or SEARCH_QUERY)",
        type=["csv", "xlsx", "xls"], accept_multiple_files=True,
    )
    if uploaded_files and st.button("Ingest uploaded files"):
        config.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        saved_paths = []
        conversion_failed = False
        for f in uploaded_files:
            dest = config.INCOMING_DIR / f.name
            dest.write_bytes(f.getvalue())
            if dest.suffix.lower() in (".xlsx", ".xls"):
                try:
                    csv_dest = ingest.convert_excel_to_csv(dest)
                    dest.unlink()
                    saved_paths.append(csv_dest)
                except ingest.SchemaError as exc:
                    st.error(str(exc))
                    conversion_failed = True
            else:
                saved_paths.append(dest)

        con = config.connect_db()
        ingest.ensure_schema(con)
        any_success = False
        for path in saved_paths:
            try:
                ingest.ingest_file(con, path)
                st.success(f"Loaded {path.name}")
                any_success = True
            except ingest.SchemaError as exc:
                st.error(f"{path.name}: {exc}")
        con.close()

        if any_success:
            st.cache_data.clear()
            git_message = commit_and_push_data()
            if git_message:
                st.info(git_message)
            st.rerun()
        elif not conversion_failed:
            st.warning("Nothing was ingested.")

if not config.DB_PATH.exists():
    st.info("No data yet - upload files above, or drop CSVs into data/incoming/ and run `python ingest.py`.")
    st.stop()

# Cache key tied to the DB file's mtime: reruns reuse cached results instantly,
# but a fresh ingest (CLI or the upload panel above) invalidates them
# automatically. Each cached function opens its own short-lived connection
# rather than sharing one across reruns, which is what caused stale/None
# results before.
DB_VERSION = config.DB_PATH.stat().st_mtime


@st.cache_data(show_spinner=False)
def load_filter_options(version: float):
    con = config.connect_db()
    try:
        min_date, max_date = con.execute(
            "SELECT min(metrics_date), max(metrics_date) FROM granular").fetchone()
        campaigns = [r[0] for r in con.execute(
            "SELECT DISTINCT campaign_name FROM granular ORDER BY campaign_name").fetchall()]
        cities = [r[0] for r in con.execute(
            "SELECT DISTINCT city FROM granular ORDER BY city").fetchall()]
        row_count = con.execute("SELECT count(*) FROM granular").fetchone()[0]
    finally:
        con.close()
    return min_date, max_date, campaigns, cities, row_count


@st.cache_data(show_spinner=False)
def load_keywords(version: float, campaigns: tuple) -> list:
    if not campaigns:
        return []
    con = config.connect_db()
    try:
        ph = ",".join(["?"] * len(campaigns))
        rows = con.execute(
            f"SELECT DISTINCT keyword FROM granular WHERE campaign_name IN ({ph}) "
            f"AND keyword IS NOT NULL ORDER BY keyword",
            list(campaigns),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


CORR_METRICS = {
    "impressions": "total_impressions", "clicks": "total_clicks", "spend": "total_budget_burnt",
    "add_to_cart": "total_a2c", "conversions": "total_conversions", "gmv": "total_gmv", "ecpm": "ecpm",
}


@st.cache_data(show_spinner=False, max_entries=5)
def load_aggregates(version: float, start_date, end_date, campaigns: tuple, cities: tuple,
                     keywords: tuple) -> dict:
    """Every number the dashboard needs, computed entirely as SQL aggregates -
    row-level data never leaves DuckDB into a full-size pandas DataFrame.

    That used to be exactly the problem: loading the whole filtered table
    (up to 315K rows) into pandas on every view is independently expensive
    regardless of DuckDB's own memory_limit setting (which only bounds
    DuckDB's internal buffers, not what gets returned to the calling
    process) - it's what caused real OOM crashes on Render's 512MB free
    tier. Every query here returns at most a few dozen/hundred rows.
    """
    con = config.connect_db()
    try:
        campaign_ph = ",".join(["?"] * len(campaigns))
        city_ph = ",".join(["?"] * len(cities))
        clauses = ["metrics_date BETWEEN ? AND ?", f"campaign_name IN ({campaign_ph})", f"city IN ({city_ph})"]
        params = [start_date, end_date, *campaigns, *cities]
        if keywords:
            kw_ph = ",".join(["?"] * len(keywords))
            clauses.append(f"keyword IN ({kw_ph})")
            params.extend(keywords)
        where = " AND ".join(clauses)

        totals_row = con.execute(f"""
            SELECT sum(total_gmv), sum(total_budget_burnt), sum(total_impressions),
                   sum(total_clicks), sum(total_a2c), sum(total_conversions), count(*)
            FROM granular WHERE {where}
        """, params).fetchone()
        keys = ["gmv", "spend", "impressions", "clicks", "a2c", "conversions", "row_count"]
        totals = {k: (v or 0) for k, v in zip(keys, totals_row)}

        by_campaign = con.execute(f"""
            SELECT campaign_name, sum(total_gmv) gmv, sum(total_budget_burnt) spend,
                   sum(total_impressions) impressions, sum(total_clicks) clicks,
                   sum(total_conversions) conversions
            FROM granular WHERE {where} GROUP BY campaign_name
        """, params).df()

        by_city = con.execute(f"""
            SELECT city, sum(total_gmv) gmv, sum(total_budget_burnt) spend
            FROM granular WHERE {where} GROUP BY city
        """, params).df()

        by_format = con.execute(f"""
            SELECT ad_property, sum(total_gmv) gmv, sum(total_budget_burnt) spend,
                   sum(total_impressions) impressions
            FROM granular WHERE {where} GROUP BY ad_property
        """, params).df()

        by_keyword_city = con.execute(f"""
            SELECT keyword, city, sum(total_gmv) gmv, sum(total_clicks) clicks,
                   sum(total_conversions) conversions
            FROM granular WHERE {where} AND keyword IS NOT NULL
            GROUP BY keyword, city ORDER BY gmv DESC LIMIT 15
        """, params).df()

        by_product = con.execute(f"""
            SELECT product_name, sum(total_gmv) gmv, sum(total_budget_burnt) spend,
                   sum(total_clicks) clicks, sum(total_conversions) conversions
            FROM granular WHERE {where} AND product_name IS NOT NULL
            GROUP BY product_name
        """, params).df()

        daily = con.execute(f"""
            SELECT metrics_date, sum(total_gmv) gmv, sum(total_budget_burnt) spend,
                   sum(total_impressions) impressions, sum(total_clicks) clicks,
                   sum(total_a2c) a2c, sum(total_conversions) conversions
            FROM granular WHERE {where} GROUP BY metrics_date ORDER BY metrics_date
        """, params).df()

        corr_select = ", ".join(
            f'corr({c1}, {c2}) AS "{n1}__{n2}"'
            for n1, c1 in CORR_METRICS.items() for n2, c2 in CORR_METRICS.items()
        )
        corr_row = con.execute(f"SELECT {corr_select} FROM granular WHERE {where}", params).fetchone()
        corr_records = []
        i = 0
        for n1 in CORR_METRICS:
            for n2 in CORR_METRICS:
                corr_records.append({"metric1": n1, "metric2": n2, "correlation": corr_row[i] or 0})
                i += 1
        corr_long = pd.DataFrame(corr_records)

        # search_query has no per-row city column (only an aggregate
        # city_count), so it can't be filtered by the city selector the way
        # granular can - date/campaign/keyword only. Guarded for older DBs
        # that predate this table (e.g. before the first search-query
        # upload).
        has_search_query = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'search_query'"
        ).fetchone()
        if has_search_query:
            sq_clauses = ["metrics_date BETWEEN ? AND ?", f"campaign_name IN ({campaign_ph})"]
            sq_params = [start_date, end_date, *campaigns]
            if keywords:
                sq_clauses.append(f"keyword IN ({kw_ph})")
                sq_params.extend(keywords)
            by_search_query = con.execute(f"""
                SELECT search_query, sum(total_gmv) gmv, sum(total_clicks) clicks,
                       sum(total_conversions) conversions
                FROM search_query WHERE {' AND '.join(sq_clauses)} AND search_query IS NOT NULL
                GROUP BY search_query ORDER BY gmv DESC LIMIT 15
            """, sq_params).df()
        else:
            by_search_query = pd.DataFrame(columns=["search_query", "gmv", "clicks", "conversions"])

        return dict(totals=totals, by_campaign=by_campaign, by_city=by_city, by_format=by_format,
                    by_keyword_city=by_keyword_city, by_product=by_product, daily=daily, corr_long=corr_long,
                    by_search_query=by_search_query)
    finally:
        con.close()


@st.cache_data(show_spinner=False)
def load_summary_table(version: float) -> pd.DataFrame:
    con = config.connect_db()
    try:
        return con.execute("SELECT * FROM summary ORDER BY period_start DESC").df()
    finally:
        con.close()


@st.cache_data(show_spinner=False, max_entries=50)
def load_product_deep_dive(version: float, product_name: str, start_date, end_date,
                           campaigns: tuple, cities: tuple) -> dict:
    """Per-product deep breakdown used by the AI-insights expander. Each
    sub-query runs against the granular table, filtered to this one
    product + the active sidebar filters. All numbers are direct SQL
    rollups - nothing derived or estimated.

    Returns a dict of DataFrames + scalar totals so the render code can
    weave them into the WHY/HOW/WHOM/CAUSE narrative without another
    round-trip to the DB."""
    con = config.connect_db()
    try:
        camp_ph = ",".join(["?"] * len(campaigns))
        city_ph = ",".join(["?"] * len(cities))
        params = [start_date, end_date, product_name, *campaigns, *cities]
        where = (f"metrics_date BETWEEN ? AND ? AND product_name = ? "
                 f"AND campaign_name IN ({camp_ph}) AND city IN ({city_ph})")

        totals = con.execute(f"""
            SELECT SUM(total_impressions) impressions, SUM(total_clicks) clicks,
                   SUM(total_budget_burnt) spend, SUM(total_a2c) a2c,
                   SUM(total_conversions) conv, SUM(total_gmv) gmv,
                   COUNT(DISTINCT city) n_cities,
                   COUNT(DISTINCT keyword) n_keywords,
                   COUNT(DISTINCT ad_property) n_formats
            FROM granular WHERE {where}
        """, params).fetchone()

        by_city = con.execute(f"""
            SELECT city, SUM(total_budget_burnt) spend, SUM(total_gmv) gmv,
                   SUM(total_conversions) conv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt),0) roi
            FROM granular WHERE {where}
            GROUP BY city HAVING SUM(total_budget_burnt) > 0
            ORDER BY gmv DESC
        """, params).fetchdf()

        by_keyword = con.execute(f"""
            SELECT keyword, match_type, SUM(total_budget_burnt) spend,
                   SUM(total_gmv) gmv, SUM(total_conversions) conv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt),0) roi
            FROM granular WHERE {where} AND keyword IS NOT NULL
            GROUP BY keyword, match_type HAVING SUM(total_budget_burnt) > 0
            ORDER BY spend DESC
        """, params).fetchdf()

        by_format = con.execute(f"""
            SELECT ad_property, SUM(total_budget_burnt) spend, SUM(total_gmv) gmv,
                   SUM(total_conversions) conv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt),0) roi
            FROM granular WHERE {where}
            GROUP BY ad_property HAVING SUM(total_budget_burnt) > 0
            ORDER BY spend DESC
        """, params).fetchdf()

        by_match = con.execute(f"""
            SELECT match_type, SUM(total_budget_burnt) spend, SUM(total_gmv) gmv,
                   SUM(total_conversions) conv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt),0) roi
            FROM granular WHERE {where}
            GROUP BY match_type HAVING SUM(total_budget_burnt) > 0
            ORDER BY spend DESC
        """, params).fetchdf()

        daily = con.execute(f"""
            SELECT metrics_date, SUM(total_budget_burnt) spend, SUM(total_gmv) gmv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt),0) roi
            FROM granular WHERE {where}
            GROUP BY metrics_date ORDER BY metrics_date
        """, params).fetchdf()

        # Product-universe averages (same filter window, all products) so
        # WHY/CAUSE lines can compare this product to peers, not just the
        # blended dashboard number.
        peer = con.execute(f"""
            SELECT 100.0*SUM(total_clicks)/NULLIF(SUM(total_impressions),0) avg_ctr,
                   100.0*SUM(total_a2c)/NULLIF(SUM(total_clicks),0) avg_a2c_rate,
                   100.0*SUM(total_conversions)/NULLIF(SUM(total_a2c),0) avg_conv_rate,
                   SUM(total_budget_burnt)/NULLIF(SUM(total_conversions),0) avg_cpa
            FROM granular
            WHERE metrics_date BETWEEN ? AND ? AND product_name IS NOT NULL
              AND campaign_name IN ({camp_ph}) AND city IN ({city_ph})
        """, [start_date, end_date, *campaigns, *cities]).fetchone()
    finally:
        con.close()

    return dict(
        totals=dict(impressions=totals[0] or 0, clicks=totals[1] or 0,
                    spend=totals[2] or 0, a2c=totals[3] or 0,
                    conv=totals[4] or 0, gmv=totals[5] or 0,
                    n_cities=totals[6] or 0, n_keywords=totals[7] or 0,
                    n_formats=totals[8] or 0),
        by_city=by_city, by_keyword=by_keyword, by_format=by_format,
        by_match=by_match, daily=daily,
        peer=dict(ctr=peer[0] or 0, a2c_rate=peer[1] or 0,
                  conv_rate=peer[2] or 0, cpa=peer[3] or 0),
    )


MATCH_LABEL_SHORT = {
    "KEYWORD_MATCH_TYPE_BROAD": "Broad",
    "KEYWORD_MATCH_TYPE_EXACT": "Exact",
    "KEYWORD_MATCH_TYPE_INVALID": "Other/None",
}


def render_product_deep_dive(product_name: str, row: pd.Series, blended_roi: float,
                              start_date, end_date, campaigns: tuple, cities: tuple) -> None:
    """Render the WHY / HOW / WHOM / CAUSE / RECOMMENDATION deep dive for
    one product inside its AI-insight expander. Every claim is grounded
    in the DataFrames returned by load_product_deep_dive() - if the data
    doesn't support a claim, it isn't shown.

    `row` carries this product's aggregated ROI/conv_rate/deviation from
    the outer AI-insights table so the calling loop doesn't re-query."""
    d = load_product_deep_dive(DB_VERSION, product_name, start_date, end_date, campaigns, cities)
    t, peer = d["totals"], d["peer"]
    if t["spend"] == 0:
        st.caption("No spend on this product in the current filters.")
        return

    good = row["deviation"] >= 0
    ctr = 100 * t["clicks"] / t["impressions"] if t["impressions"] else 0
    a2c_rate = 100 * t["a2c"] / t["clicks"] if t["clicks"] else 0
    click_conv = 100 * t["conv"] / t["clicks"] if t["clicks"] else 0
    a2c_conv = 100 * t["conv"] / t["a2c"] if t["a2c"] else 0
    cpa = t["spend"] / t["conv"] if t["conv"] else 0

    def cmp(v, avg, higher_is_better=True):
        """Return '(X% above/below peer average)' phrase."""
        if avg == 0 or pd.isna(avg): return ""
        diff = (v - avg) / avg * 100
        direction = "above" if diff > 0 else "below"
        sign = (diff > 0) == higher_is_better
        return f"({abs(diff):.0f}% {direction} peer avg — {'strong' if sign else 'weak'})"

    # === WHAT (the facts, no interpretation) =============================
    st.markdown("**📊 WHAT is happening**")
    st.markdown(
        f"- **ROI:** {row['roi']:.2f}x vs blended {blended_roi:.2f}x "
        f"({'+' if good else ''}{row['deviation']:.2f}x deviation)  \n"
        f"- **Money flow:** ₹{format_inr(t['spend'])} spend → ₹{format_inr(t['gmv'])} GMV "
        f"→ {int(t['conv']):,} orders  \n"
        f"- **Funnel:** {int(t['impressions']):,} impr → {int(t['clicks']):,} clicks "
        f"({ctr:.2f}% CTR) → {int(t['a2c']):,} carts ({a2c_rate:.1f}% A2C) "
        f"→ {int(t['conv']):,} orders ({a2c_conv:.1f}% cart-to-order)  \n"
        f"- **CPA:** ₹{format_inr(cpa, 0) if cpa else '—'} per order  \n"
        f"- **Distribution:** ran in **{t['n_cities']} cities**, "
        f"across **{t['n_keywords']} keywords** and **{t['n_formats']} ad formats**"
    )

    # === WHY (comparison to peer averages) ===============================
    st.markdown("**❓ WHY it's " + ("outperforming" if good else "underperforming") + " the blended average**")
    why_lines = []
    why_lines.append(
        f"- **CTR** {ctr:.2f}% vs peer {peer['ctr']:.2f}% {cmp(ctr, peer['ctr'])}"
    )
    why_lines.append(
        f"- **A2C rate** {a2c_rate:.1f}% vs peer {peer['a2c_rate']:.1f}% {cmp(a2c_rate, peer['a2c_rate'])}"
    )
    why_lines.append(
        f"- **Cart→Order conversion** {a2c_conv:.1f}% vs peer {peer['conv_rate']:.1f}% {cmp(a2c_conv, peer['conv_rate'])}"
    )
    if cpa and peer["cpa"]:
        why_lines.append(
            f"- **CPA** ₹{format_inr(cpa)} vs peer ₹{format_inr(peer['cpa'])} "
            f"{cmp(cpa, peer['cpa'], higher_is_better=False)}"
        )
    st.markdown("  \n".join(why_lines))

    # === HOW (mechanics - what's driving the spend & GMV) ================
    st.markdown("**⚙️ HOW the performance is being made (top drivers)**")
    how_col1, how_col2 = st.columns(2)
    with how_col1:
        st.markdown("*Top cities by GMV:*")
        top_cities = d["by_city"].head(5).copy()
        if not top_cities.empty:
            top_cities["roi"] = top_cities["roi"].round(2)
            top_cities = top_cities.rename(columns={"city": "City", "spend": "Spend",
                                                    "gmv": "GMV", "conv": "Conv", "roi": "ROI"})
            st.dataframe(format_df_inr(top_cities, ["Spend", "GMV"]),
                         width='stretch', hide_index=True, height=210)
    with how_col2:
        st.markdown("*Top keywords by spend:*")
        top_kw = d["by_keyword"].head(5).copy()
        if not top_kw.empty:
            top_kw["roi"] = top_kw["roi"].round(2)
            top_kw["match_type"] = top_kw["match_type"].map(MATCH_LABEL_SHORT).fillna(top_kw["match_type"])
            top_kw = top_kw.rename(columns={"keyword": "Keyword", "match_type": "Match",
                                            "spend": "Spend", "gmv": "GMV", "conv": "Conv", "roi": "ROI"})
            st.dataframe(format_df_inr(top_kw, ["Spend", "GMV"]),
                         width='stretch', hide_index=True, height=210)

    # Format + match-type breakdown
    fm_col1, fm_col2 = st.columns(2)
    with fm_col1:
        st.markdown("*Ad-format mix:*")
        fmt = d["by_format"].copy()
        if not fmt.empty:
            fmt["% of spend"] = (fmt["spend"] / fmt["spend"].sum() * 100).round(1)
            fmt["roi"] = fmt["roi"].round(2)
            fmt = fmt[["ad_property", "spend", "gmv", "roi", "% of spend"]].rename(columns={
                "ad_property": "Format", "spend": "Spend", "gmv": "GMV", "roi": "ROI",
            })
            st.dataframe(format_df_inr(fmt, ["Spend", "GMV"]),
                         width='stretch', hide_index=True, height=180)
    with fm_col2:
        st.markdown("*Match-type mix:*")
        mm = d["by_match"].copy()
        if not mm.empty:
            mm["match_type"] = mm["match_type"].map(MATCH_LABEL_SHORT).fillna(mm["match_type"])
            mm["% of spend"] = (mm["spend"] / mm["spend"].sum() * 100).round(1)
            mm["roi"] = mm["roi"].round(2)
            mm = mm[["match_type", "spend", "gmv", "roi", "% of spend"]].rename(columns={
                "match_type": "Match", "spend": "Spend", "gmv": "GMV", "roi": "ROI",
            })
            st.dataframe(format_df_inr(mm, ["Spend", "GMV"]),
                         width='stretch', hide_index=True, height=180)

    # === WHOM (audience concentration) ===================================
    if not d["by_city"].empty:
        st.markdown("**🎯 WHOM it's reaching (audience concentration)**")
        n_cities_active = len(d["by_city"])
        top5_share = d["by_city"].head(5)["gmv"].sum() / d["by_city"]["gmv"].sum() * 100 if d["by_city"]["gmv"].sum() else 0
        one_share = d["by_city"].iloc[0]["gmv"] / d["by_city"]["gmv"].sum() * 100 if d["by_city"]["gmv"].sum() else 0
        top_city = d["by_city"].iloc[0]["city"]
        concentration = (
            f"- Active in **{n_cities_active} cities** with spend  \n"
            f"- **{top_city}** alone drives **{one_share:.1f}%** of GMV; "
            f"top 5 cities = **{top5_share:.1f}%** of GMV"
        )
        st.markdown(concentration)
        if one_share > 40:
            st.warning(f"⚠️ Heavily concentrated in {top_city} — a single-city dependency. "
                       "GMV is fragile to any change in that market (delivery, competition, stock).")
        elif top5_share < 40 and n_cities_active > 10:
            st.info(f"✅ Well diversified — spread across {n_cities_active} cities with no single hotspot.")

    # === CAUSE (root-cause hypothesis based on the funnel shape) ==========
    st.markdown("**🧭 CAUSE — most likely root of this deviation**")
    causes = []
    if ctr < peer["ctr"] * 0.75 and peer["ctr"] > 0:
        causes.append("**Low CTR vs peers** → ad creative or product image isn't drawing the click. "
                      "The product isn't losing at conversion; it's losing at first-impression appeal.")
    if a2c_rate < peer["a2c_rate"] * 0.75 and peer["a2c_rate"] > 0:
        causes.append("**Low A2C rate** → shoppers click but don't add to cart. Usually a price/pack-size "
                      "issue, unclear listing, or the landing card fails to reinforce the ad promise.")
    if a2c_conv < peer["conv_rate"] * 0.75 and peer["conv_rate"] > 0 and t["a2c"] > 0:
        causes.append("**Low cart-to-order conversion** → shoppers add to cart but abandon. "
                      "Likely delivery friction, cart-level minimums, or a competing product in the same cart won.")
    # Broad-heavy waste?
    if not d["by_match"].empty:
        broad_row = d["by_match"][d["by_match"]["match_type"] == "KEYWORD_MATCH_TYPE_BROAD"]
        exact_row = d["by_match"][d["by_match"]["match_type"] == "KEYWORD_MATCH_TYPE_EXACT"]
        if not broad_row.empty and not exact_row.empty:
            br, ex = broad_row.iloc[0]["roi"] or 0, exact_row.iloc[0]["roi"] or 0
            broad_pct = broad_row.iloc[0]["spend"] / d["by_match"]["spend"].sum() * 100
            if broad_pct > 60 and br < ex * 0.7:
                causes.append(
                    f"**Broad match is eating budget** — {broad_pct:.0f}% of this product's spend is on broad "
                    f"({br:.2f}x ROI) while exact returns {ex:.2f}x. Broad expansion is dragging efficiency down."
                )
    # Concentrated in one keyword?
    if not d["by_keyword"].empty and len(d["by_keyword"]) > 1:
        top_kw_share = d["by_keyword"].iloc[0]["spend"] / d["by_keyword"]["spend"].sum() * 100
        if top_kw_share > 60:
            causes.append(f"**One keyword dominates** — *\"{d['by_keyword'].iloc[0]['keyword']}\"* drives "
                          f"{top_kw_share:.0f}% of spend. This product's fate is tied to one search pattern; "
                          "if that keyword's competition heats up, the whole line drops.")
    if good and not causes:
        causes.append("**All funnel stages hold up vs peers** — no single stage is doing the heavy lifting; "
                      "this is a broadly well-performing product.")
    if not causes:
        causes.append("Funnel stages are within 25% of peer averages — the deviation is coming from "
                      "cumulative small differences rather than one clear cause. Sample-size caveat applies.")
    for c in causes:
        st.markdown(f"- {c}")

    # === RECOMMENDATION ==================================================
    st.markdown("**🎬 RECOMMENDATION**")
    recs = []
    if good:
        # Scaling recs for winners
        if d["by_city"]["gmv"].sum() > 0:
            underused = d["by_city"][(d["by_city"]["roi"] > blended_roi) &
                                     (d["by_city"]["spend"] < d["by_city"]["spend"].median())]
            if not underused.empty:
                recs.append(f"**Scale winning cities:** {len(underused)} cities have above-blended ROI on this "
                            "product but below-median spend. Raise bids/budgets there before saturating current hotspots.")
        if not d["by_match"].empty:
            exact_row = d["by_match"][d["by_match"]["match_type"] == "KEYWORD_MATCH_TYPE_EXACT"]
            if not exact_row.empty and (exact_row.iloc[0]["roi"] or 0) > 2:
                recs.append("**Graduate winning broad queries to exact** — check the Search Query Deep Dive "
                            "for shopper-typed queries against this product's keywords and add the top-ROI ones as new exact keywords.")
    else:
        # Fix recs for laggards
        if ctr < peer["ctr"] * 0.8 and peer["ctr"] > 0:
            recs.append("**Fix the creative first** — a new hero image or price-forward copy usually lifts "
                        "CTR before any bid changes matter.")
        if a2c_conv < peer["conv_rate"] * 0.8 and t["a2c"] > 0:
            recs.append("**Investigate cart-drop causes** — check MOV (minimum order value), out-of-stock cities, "
                        "and whether a rival product sits alongside this one in cart bundles.")
        # Loser broad keywords under this product?
        loser_kws = d["by_keyword"][(d["by_keyword"]["spend"] >= 100) &
                                     ((d["by_keyword"]["roi"].fillna(0)) < 1)]
        if not loser_kws.empty:
            total_loser_spend = loser_kws["spend"].sum()
            recs.append(f"**Pause {len(loser_kws)} loser keywords** on this product "
                        f"(₹{format_inr(total_loser_spend)} spent at < 1x ROI). See table above.")
        if not recs:
            recs.append("**Cut budget by 30% and observe for a week** — the deviation isn't traceable to "
                        "one clear cause, so a controlled reduction protects spend without killing signal.")
    for r in recs:
        st.markdown(f"- {r}")


min_date, max_date, all_campaigns, all_cities, total_rows = load_filter_options(DB_VERSION)

if total_rows == 0:
    st.info("Database exists but the granular table is empty - upload a IM_GRANULAR_*.csv file above.")
    st.stop()

# --- View selector -----------------------------------------------------------
# Top-of-sidebar switch between the main dashboard and the Search Query
# Deep Dive. Both views read from the same DB and honor the same filter
# widgets below - a radio (not tabs) so the switch is unmistakable and
# so a heavy view doesn't re-render just because the user opened a tab.
view_mode = st.sidebar.radio(
    "📍 View",
    ["📊 Main dashboard", "🔍 Search Query deep dive", "📦 Per-Product Tracker", "🧠 Advanced Intelligence"],
    label_visibility="visible",
)
st.sidebar.markdown("---")

# --- Filters ---------------------------------------------------------------
st.sidebar.header("Filters")

compare_mode = st.sidebar.toggle("Compare two periods", value=False)

date_range = st.sidebar.date_input("Date range", (min_date, max_date), min_date, max_date, key="primary_range")
if len(date_range) != 2:
    st.stop()
start_date, end_date = date_range

compare_start = compare_end = None
if compare_mode:
    period_len = (end_date - start_date).days + 1
    default_compare_end = start_date - timedelta(days=1)
    default_compare_start = default_compare_end - timedelta(days=period_len - 1)
    compare_range = st.sidebar.date_input(
        "Compare to (freely editable)", (default_compare_start, default_compare_end),
        key="compare_range",
    )
    if len(compare_range) != 2:
        st.stop()
    compare_start, compare_end = compare_range

selected_campaigns = st.sidebar.multiselect("Campaigns", all_campaigns, default=all_campaigns)
if not selected_campaigns:
    st.warning("Select at least one campaign.")
    st.stop()

available_keywords = load_keywords(DB_VERSION, tuple(selected_campaigns))
selected_keywords = st.sidebar.multiselect(
    "Keywords (optional - narrows within selected campaigns)", available_keywords, default=[])

selected_cities = st.sidebar.multiselect("Cities", all_cities, default=all_cities)
if not selected_cities:
    st.warning("Select at least one city.")
    st.stop()

# If the user picked the deep-dive view, render it now and short-circuit
# the main-dashboard rendering below. Skips the load_aggregates() query
# (which the deep dive doesn't use) - the deep dive has its own cached
# loaders that pull straight from the granular/search_query tables.
if view_mode == "🔍 Search Query deep dive":
    sqdd.render(
        db_version=DB_VERSION,
        start_date=start_date,
        end_date=end_date,
        selected_campaigns=selected_campaigns,
        selected_cities=selected_cities,
        format_inr=format_inr,
        format_df_inr=format_df_inr,
        accents=accents,
    )
    st.stop()

if view_mode == "📦 Per-Product Tracker":
    pt.render(start_date, end_date, tuple(selected_campaigns), tuple(selected_cities))
    st.stop()

if view_mode == "🧠 Advanced Intelligence":
    sqi.render(
        db_version=DB_VERSION,
        start_date=start_date,
        end_date=end_date,
        campaigns=tuple(selected_campaigns),
        format_df_inr=format_df_inr,
    )
    st.stop()

agg = load_aggregates(DB_VERSION, start_date, end_date, tuple(selected_campaigns),
                       tuple(selected_cities), tuple(selected_keywords))
if agg["totals"]["row_count"] == 0:
    st.warning("No rows match the current filters.")
    st.stop()

agg_compare = None
if compare_mode:
    agg_compare = load_aggregates(DB_VERSION, compare_start, compare_end, tuple(selected_campaigns),
                                   tuple(selected_cities), tuple(selected_keywords))

# --- KPIs (top-left) ---------------------------------------------------------


def compute_kpis(totals: dict) -> dict:
    gmv, spend = totals["gmv"], totals["spend"]
    impressions, clicks = totals["impressions"], totals["clicks"]
    a2c, conversions = totals["a2c"], totals["conversions"]
    return dict(
        roi=(gmv / spend if spend else 0), gmv=gmv, spend=spend, impressions=impressions,
        ecpm=(spend / impressions * 1000 if impressions else 0), clicks=clicks,
        a2c_rate=(a2c / clicks if clicks else 0), conv_rate=(conversions / a2c if a2c else 0),
    )


current = compute_kpis(agg["totals"])
compare = (compute_kpis(agg_compare["totals"])
           if agg_compare is not None and agg_compare["totals"]["row_count"] > 0 else None)


def pct_delta(cur, prev):
    return None if not prev else (cur - prev) / prev * 100


def kpi_card(label: str, value_str: str, color_key: str, cur_val=None, prev_val=None) -> str:
    """Flat white card with a subtle colored accent dot next to the label -
    the reference UI's summary tile style. `color_key` still drives the
    dot color so callers can categorize by section."""
    c = accents[color_key]
    delta_html = ""
    if compare is not None and cur_val is not None and prev_val is not None:
        d = pct_delta(cur_val, prev_val)
        if d is not None:
            cls = "kpi-delta-up" if d >= 0 else "kpi-delta-down"
            arrow = "▲" if d >= 0 else "▼"
            delta_html = f'<div class="{cls}">{arrow} {abs(d):.1f}% vs compare</div>'
    dot = (f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
           f'background:{c["fg"]};margin-right:6px;vertical-align:middle"></span>')
    return (f'<div class="kpi-card">'
            f'<div class="kpi-label">{dot}{label}</div>'
            f'<div class="kpi-value">{value_str}</div>{delta_html}</div>')


cards_html = "".join([
    kpi_card("ROI", f"{current['roi']:.2f}x", "blue", current["roi"], compare["roi"] if compare else None),
    kpi_card("GMV", f"₹{format_inr(current['gmv'])}", "green", current["gmv"], compare["gmv"] if compare else None),
    kpi_card("Spend", f"₹{format_inr(current['spend'])}", "amber", current["spend"],
              compare["spend"] if compare else None),
    kpi_card("Impressions", format_inr(current['impressions']), "blue", current["impressions"],
              compare["impressions"] if compare else None),
    kpi_card("eCPM", f"₹{format_inr(current['ecpm'], 2)}", "blue", current["ecpm"],
              compare["ecpm"] if compare else None),
    kpi_card("Clicks", format_inr(current['clicks']), "green", current["clicks"],
              compare["clicks"] if compare else None),
])
total_impressions, total_clicks = current["impressions"], current["clicks"]
total_gmv, total_spend, blended_roi = current["gmv"], current["spend"], current["roi"]

# --- AI Insights column (formula-based, not an LLM call - see chat) --------
by_product = agg["by_product"].copy()
by_product["roi"] = by_product["gmv"] / by_product["spend"].replace(0, pd.NA)
by_product["conv_rate"] = by_product["conversions"] / by_product["clicks"].replace(0, pd.NA) * 100

min_spend = by_product["spend"].median() if not by_product.empty else 0
qualifying = by_product[by_product["spend"] >= min_spend].dropna(subset=["roi"]).copy()

top_outliers = pd.DataFrame()
if not qualifying.empty:
    qualifying["deviation"] = qualifying["roi"] - blended_roi
    top_outliers = qualifying.reindex(qualifying["deviation"].abs().sort_values(ascending=False).index).head(10)

st.markdown(f'<div class="kpi-grid">{cards_html}</div>', unsafe_allow_html=True)

st.markdown(
    f'<div class="kpi-label" style="margin-bottom:6px;color:{accents["blue"]["fg"]}">AI INSIGHTS — TOP 10 '
    f'OUTLIER PRODUCTS (BY ROI DEVIATION)</div>'
    f'<div style="font-size:11px;color:{pal["text3"]};margin-bottom:8px">'
    f'Click any row to open a per-product deep dive (WHAT / WHY / HOW / WHOM / CAUSE / RECOMMENDATION), '
    f'built live from this product\'s rows in the current filter window.</div>',
    unsafe_allow_html=True,
)
if top_outliers.empty:
    st.caption("No product-level data with meaningful spend in the current filter.")
else:
    for _, r in top_outliers.iterrows():
        good = r["deviation"] >= 0
        icon, verb = ("🟢", "outperforming") if good else ("🔴", "underperforming")
        conv_rate = r["conv_rate"] if pd.notna(r["conv_rate"]) else 0
        # Expander label mirrors the old one-liner so the collapsed view
        # is visually identical to the previous static list.
        label = (f"{icon}  {r['product_name']}  —  {r['roi']:.2f}x ROI vs {blended_roi:.2f}x avg "
                 f"({verb} by {abs(r['deviation']):.2f}x)  ·  "
                 f"₹{format_inr(r['spend'])} spend → ₹{format_inr(r['gmv'])} GMV, "
                 f"{conv_rate:.0f}% conv rate")
        with st.expander(label, expanded=False):
            render_product_deep_dive(
                product_name=r["product_name"],
                row=r, blended_roi=blended_roi,
                start_date=start_date, end_date=end_date,
                campaigns=tuple(selected_campaigns), cities=tuple(selected_cities),
            )

if compare_mode and compare is None:
    st.caption("No data in the comparison period for the current filters.")

# --- Conversion funnel -----------------------------------------------------
st.subheader("Conversion funnel — where the chain leaks")
total_a2c = agg["totals"]["a2c"]
total_conversions = agg["totals"]["conversions"]

funnel_col, rate_col = st.columns([2, 1])
with funnel_col:
    st.caption(f"{format_inr(total_impressions)} impressions (top KPI above) feed into the funnel below.")
    funnel = pd.DataFrame({
        "stage": ["1. Clicks", "2. Added to cart", "3. Converted"],
        "count": [total_clicks, total_a2c, total_conversions],
    })
    chart = alt.Chart(funnel).mark_bar().encode(
        x=alt.X("stage", sort=None, title=None),
        y=alt.Y("count", title="Count"),
        color=alt.Color("stage", scale=alt.Scale(range=BOLD_CATEGORICAL), legend=None),
        tooltip=["stage", "count"],
    )
    st.altair_chart(chart, width='stretch')
with rate_col:
    ctr = total_clicks / total_impressions * 100 if total_impressions else 0
    a2c_rate = total_a2c / total_clicks * 100 if total_clicks else 0
    conv_rate = total_conversions / total_a2c * 100 if total_a2c else 0
    st.metric("Impressions → Clicks", f"{ctr:.2f}%")
    st.metric("Clicks → Added to cart", f"{a2c_rate:.2f}%")
    st.metric("Added to cart → Converted", f"{conv_rate:.2f}%")

# --- Correlation ------------------------------------------------------------
st.subheader("What actually correlates with GMV?")
st.caption("Pearson correlation of each metric with GMV, across every row in the current filter "
           "(computed in SQL, not loaded into pandas). +1 = moves with GMV, -1 = moves opposite, "
           "0 = no linear relationship.")
corr_with_gmv = agg["corr_long"][
    (agg["corr_long"]["metric2"] == "gmv") & (agg["corr_long"]["metric1"] != "gmv")
].sort_values("correlation", ascending=False)

corr_bar = alt.Chart(corr_with_gmv).mark_bar().encode(
    x=alt.X("correlation", title="Correlation with GMV", scale=alt.Scale(domain=[-1, 1])),
    y=alt.Y("metric1", sort="-x", title=None),
    color=alt.Color("correlation", scale=alt.Scale(scheme="redblue", domain=[-1, 1]), legend=None),
    tooltip=["metric1", alt.Tooltip("correlation", format=".2f")],
)
st.altair_chart(corr_bar, width='stretch')

# --- GMV trend -----------------------------------------------------------
st.subheader("Daily GMV & spend")
daily = agg["daily"].copy()
daily["CTR"] = (daily["clicks"] / daily["impressions"].replace(0, pd.NA) * 100).fillna(0)
daily["CVR"] = (daily["conversions"] / daily["clicks"].replace(0, pd.NA) * 100).fillna(0)

money = daily.rename(columns={"gmv": "GMV", "spend": "Spend"}).melt(
    "metrics_date", value_vars=["GMV", "Spend"], var_name="series", value_name="value")
y_max = max(200_000, daily[["gmv", "spend"]].to_numpy().max())
money_chart = alt.Chart(money).mark_line(strokeWidth=3).encode(
    x=alt.X("metrics_date", title=None),
    y=alt.Y("value", title="₹", scale=alt.Scale(domain=[0, y_max])),
    color=alt.Color("series", title=None, scale=alt.Scale(range=BOLD_CATEGORICAL)),
    tooltip=["metrics_date", "series", "value"],
)
st.altair_chart(money_chart, width='stretch')

impressions_col, rate_col = st.columns(2)
with impressions_col:
    st.caption("Daily impressions")
    impressions_chart = alt.Chart(daily).mark_line(strokeWidth=3, color=BOLD_CATEGORICAL[0]).encode(
        x=alt.X("metrics_date", title=None), y=alt.Y("impressions", title="Impressions"),
        tooltip=["metrics_date", "impressions"],
    )
    st.altair_chart(impressions_chart, width='stretch')
with rate_col:
    st.caption("Daily CTR & CVR")
    rates = daily.melt("metrics_date", value_vars=["CTR", "CVR"], var_name="series", value_name="value")
    rate_chart = alt.Chart(rates).mark_line(strokeWidth=3).encode(
        x=alt.X("metrics_date", title=None), y=alt.Y("value", title="%"),
        color=alt.Color("series", title=None, scale=alt.Scale(range=BOLD_CATEGORICAL[1:])),
        tooltip=["metrics_date", "series", alt.Tooltip("value", format=".2f")],
    )
    st.altair_chart(rate_chart, width='stretch')

# --- By city ---------------------------------------------------------------
st.subheader("GMV by city — best vs. worst")
city_view = st.radio("Show", ["Top 15", "Worst 15"], horizontal=True, key="city_view")
by_city_all = agg["by_city"]
by_city_ranked = by_city_all.sort_values("gmv", ascending=(city_view == "Worst 15")).head(15)
by_city_long = by_city_ranked.melt("city", value_vars=["gmv", "spend"], var_name="metric", value_name="value")
by_city_long["metric"] = by_city_long["metric"].map({"gmv": "GMV", "spend": "Spend"})
city_chart = alt.Chart(by_city_long).mark_bar().encode(
    x=alt.X("value", title="₹"),
    y=alt.Y("city", sort=by_city_ranked["city"].tolist(), title=None),
    color=alt.Color("metric", title=None, scale=alt.Scale(range=BOLD_CATEGORICAL)),
    yOffset="metric",
    tooltip=["city", "metric", "value"],
)
st.altair_chart(city_chart, width='stretch')

# --- By ad format ------------------------------------------------------------
st.subheader("Performance by ad format")
by_format = agg["by_format"].copy()
by_format["roi"] = (by_format["gmv"] / by_format["spend"]).round(2)
by_format = by_format.sort_values("gmv", ascending=False)
format_chart = alt.Chart(by_format).mark_bar().encode(
    x=alt.X("gmv", title="GMV (₹)"),
    y=alt.Y("ad_property", sort="-x", title=None),
    color=alt.Color("roi", scale=alt.Scale(scheme="redyellowgreen"), title="ROI"),
    tooltip=["ad_property", "gmv", "spend", "roi"],
)
st.altair_chart(format_chart, width='stretch')

# --- Top keywords ------------------------------------------------------------
st.subheader("Top keywords by GMV")
by_keyword_city = agg["by_keyword_city"]
if by_keyword_city.empty:
    st.caption("No keyword-level data in the current filter (many ad formats target by category, not keyword).")
else:
    st.dataframe(format_df_inr(by_keyword_city, ["gmv", "clicks", "conversions"]), width='stretch')

# --- Top search queries -------------------------------------------------------
st.subheader("Top search queries by GMV")
st.caption("The actual terms shoppers typed, not the keyword you targeted - from the Search Query report "
           "(not filtered by city, since that report doesn't break out by city).")
by_search_query = agg["by_search_query"]
if by_search_query.empty:
    st.caption("No search query data loaded yet - upload an IM_..._SEARCH_QUERY_*.csv file above.")
else:
    st.dataframe(format_df_inr(by_search_query, ["gmv", "clicks", "conversions"]), width='stretch')

# --- Campaign rollup ---------------------------------------------------------------
st.subheader("Campaign performance (within current filters)")
by_campaign = agg["by_campaign"].copy()
by_campaign["roi"] = (by_campaign["gmv"] / by_campaign["spend"]).round(2)
money_cols = ["gmv", "spend", "impressions", "clicks", "conversions"]
st.dataframe(format_df_inr(by_campaign.sort_values("gmv", ascending=False), money_cols),
             width='stretch')

# --- Underperformers ---------------------------------------------------------
st.subheader("Underperformers to look at")
median_spend = by_campaign["spend"].median()
watchlist = by_campaign[
    (by_campaign["spend"] >= median_spend) & (by_campaign["roi"] < blended_roi)
].sort_values("spend", ascending=False)
if watchlist.empty:
    st.success("No above-median-spend campaign is returning below the blended ROI right now.")
else:
    st.caption(f"Spending at/above the median (₹{format_inr(median_spend)}) but returning less than the "
               f"blended ROI ({blended_roi:.2f}x) - budget worth re-examining first.")
    st.dataframe(format_df_inr(watchlist, money_cols), width='stretch')

# --- Spend vs outcome -------------------------------------------------------
st.subheader("Spend vs GMV by campaign — does more spend pay off?")
st.caption("Each point is one campaign, colored by ROI. The dashed line is the spend/GMV trend; labeled "
           "points are the campaigns furthest above or below it - the real outliers worth a closer look.")

# Residual from the trend (not just ROI) is what actually identifies an
# outlier here - a high-ROI campaign sitting right on the trend line isn't
# unusual, it's just small; residual distance is.
if len(by_campaign) >= 2:
    slope, intercept = np.polyfit(by_campaign["spend"], by_campaign["gmv"], 1)
    by_campaign["residual"] = by_campaign["gmv"] - (slope * by_campaign["spend"] + intercept)
else:
    by_campaign["residual"] = 0
outlier_labels = by_campaign.reindex(
    by_campaign["residual"].abs().sort_values(ascending=False).index
).head(3)

scatter = alt.Chart(by_campaign).mark_circle(size=140, opacity=0.85).encode(
    x=alt.X("spend", title="Spend (₹)"),
    y=alt.Y("gmv", title="GMV (₹)"),
    color=alt.Color("roi", scale=alt.Scale(scheme="redyellowgreen"), title="ROI"),
    tooltip=["campaign_name", "spend", "gmv", "roi"],
)
trend = alt.Chart(by_campaign).transform_regression("spend", "gmv").mark_line(
    color=pal["text3"], strokeDash=[4, 4]
).encode(x="spend", y="gmv")
labels = alt.Chart(outlier_labels).mark_text(dy=-12, fontWeight="bold").encode(
    x="spend", y="gmv", text="campaign_name",
)
st.altair_chart((scatter + trend + labels).interactive(), width='stretch')

# --- Raw monthly summary export, if any has been loaded --------------------
summary = load_summary_table(DB_VERSION)
if not summary.empty:
    with st.expander("Raw monthly summary exports"):
        st.dataframe(summary, width='stretch')
