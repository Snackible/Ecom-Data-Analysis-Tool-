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
    """After a dashboard upload, commit db/ads.duckdb and push to GitHub so
    the data survives Render's ephemeral filesystem on the next redeploy.

    Requires GITHUB_TOKEN (a token scoped to just this repo's Contents:
    read/write - see README) set as an env var; returns None and does
    nothing if it isn't set (e.g. local dev, where the manual
    ingest -> commit -> push workflow in the README covers this instead).
    Never surfaces the token in any message shown to the UI, even on
    failure - only a generic string.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None

    repo_dir = str(config.BASE_DIR)

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)

    remote = run("remote", "get-url", "origin")
    if remote.returncode != 0 or not remote.stdout.strip().startswith("https://"):
        return "Auto-push skipped: no https:// git remote configured."
    authed_remote = remote.stdout.strip().replace("https://", f"https://x-access-token:{token}@", 1)

    run("config", "user.email", "dashboard-bot@snackible.com")
    run("config", "user.name", "Instamart Dashboard Bot")
    run("add", "db/ads.duckdb")

    status = run("status", "--porcelain", "db/ads.duckdb")
    if not status.stdout.strip():
        return "No data changes to commit."

    commit = run("commit", "-m", "Auto-update data from dashboard upload")
    if commit.returncode != 0:
        return "Git commit failed - see server logs for details."

    push = subprocess.run(["git", "-C", repo_dir, "push", authed_remote, "HEAD:main"],
                           capture_output=True, text=True)
    if push.returncode != 0:
        return "Git push failed - check GITHUB_TOKEN is valid and has write access to this repo."

    return "Data committed and pushed to GitHub - Render will redeploy shortly with the new data."

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
PALETTES = {
    "dark": dict(bg="#0B0F19", surface="#161B29", text="#F5F7FA", muted="#9AA4B2",
                 accent="#6C9BFF", positive="#4ADE80", negative="#FF6B6B", border="#232A3B"),
    "light": dict(bg="#FFFFFF", surface="#FFFFFF", text="#12141C", muted="#666E7D",
                  accent="#3B6FF6", positive="#16A34A", negative="#E5342E", border="#E7E9F0"),
}
# The four brand hues the dashboard is built around (white base, colored
# accents) - each carries a foreground (text/border) shade and a soft tint
# for card backgrounds, tuned separately per theme so tints stay readable in
# both light and dark mode.
ACCENTS = {
    "light": {
        "blue":   dict(fg="#3B6FF6", bg="#EEF3FF", border="#C9D9FF"),
        "pink":   dict(fg="#DB2777", bg="#FDEFF6", border="#F6C9E0"),
        "green":  dict(fg="#16A34A", bg="#E9F9EF", border="#BFEBD1"),
        "yellow": dict(fg="#B7791F", bg="#FFF6E0", border="#F7DFA0"),
    },
    "dark": {
        "blue":   dict(fg="#6C9BFF", bg="#16213D", border="#274073"),
        "pink":   dict(fg="#F472B6", bg="#3A1E2E", border="#5B2C46"),
        "green":  dict(fg="#4ADE80", bg="#173626", border="#215239"),
        "yellow": dict(fg="#FBBF24", bg="#3A2E10", border="#5C4718"),
    },
}
BOLD_CATEGORICAL = ["#3B6FF6", "#DB2777", "#16A34A", "#D98E00", "#8B5CF6", "#0EA5A6", "#E5342E", "#65A30D"]

if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False
st.sidebar.toggle("🌙 Dark mode", key="dark_mode")
theme_mode = "dark" if st.session_state.dark_mode else "light"
pal = PALETTES[theme_mode]
accents = ACCENTS[theme_mode]

st.markdown(f"""
<style>
.stApp {{ background-color: {pal['bg']}; }}
.stApp, .stApp p, .stApp span, .stApp label {{ color: {pal['text']}; }}
[data-testid="stHeader"] {{ background-color: {pal['bg']}; }}
[data-testid="stHeader"] button, [data-testid="stHeader"] svg {{ color: {pal['text']} !important; }}
[data-testid="stSidebar"] {{ background-color: {pal['surface']}; border-right: 1px solid {pal['border']}; }}
[data-testid="stSidebar"] * {{ color: {pal['text']}; }}

h1 {{ color: {pal['text']} !important; font-weight: 800; margin-bottom: 4px; }}
h1::after {{ content: ""; display: block; width: 150px; height: 5px; margin-top: 8px; border-radius: 3px;
             background: linear-gradient(90deg, {accents['blue']['fg']}, {accents['pink']['fg']},
             {accents['green']['fg']}, {accents['yellow']['fg']}); }}
h2, h3 {{ color: {pal['text']} !important; font-weight: 700; }}
h3 {{ border-left: 4px solid {accents['blue']['fg']}; padding-left: 10px; margin-top: 2.4rem !important; }}

.kpi-grid {{ display: grid; grid-template-columns: repeat(2, minmax(150px, 1fr)); gap: 12px;
             max-width: 560px; margin-bottom: 1.4rem; }}
.kpi-card {{ border-radius: 12px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06); }}
.kpi-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }}
.kpi-value {{ font-size: 23px; font-weight: 800; color: {pal['text']}; margin-top: 3px; }}
.kpi-delta-up {{ color: {pal['positive']}; font-size: 12px; font-weight: 700; margin-top: 3px; }}
.kpi-delta-down {{ color: {pal['negative']}; font-size: 12px; font-weight: 700; margin-top: 3px; }}

.stButton > button {{ background-color: {accents['blue']['fg']}; color: #FFFFFF; border: none;
                       border-radius: 8px; font-weight: 600; }}
.stButton > button:hover {{ background-color: {pal['text']}; color: #FFFFFF; }}
[data-testid="stExpander"] {{ border: 1px solid {pal['border']}; border-radius: 12px; }}
div[data-testid="stDataFrame"] {{ border: 1px solid {pal['border']}; border-radius: 10px; overflow: hidden; }}
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
    con = config.connect_db(read_only=True)
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
    con = config.connect_db(read_only=True)
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
    con = config.connect_db(read_only=True)
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
    con = config.connect_db(read_only=True)
    try:
        return con.execute("SELECT * FROM summary ORDER BY period_start DESC").df()
    finally:
        con.close()


min_date, max_date, all_campaigns, all_cities, total_rows = load_filter_options(DB_VERSION)

if total_rows == 0:
    st.info("Database exists but the granular table is empty - upload a IM_GRANULAR_*.csv file above.")
    st.stop()

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
    c = accents[color_key]
    delta_html = ""
    if compare is not None and cur_val is not None and prev_val is not None:
        d = pct_delta(cur_val, prev_val)
        if d is not None:
            cls = "kpi-delta-up" if d >= 0 else "kpi-delta-down"
            arrow = "▲" if d >= 0 else "▼"
            delta_html = f'<div class="{cls}">{arrow} {abs(d):.1f}% vs compare</div>'
    card_style = f'background:{c["bg"]}; border:1px solid {c["border"]}; border-left:4px solid {c["fg"]};'
    return (f'<div class="kpi-card" style="{card_style}">'
            f'<div class="kpi-label" style="color:{c["fg"]}">{label}</div>'
            f'<div class="kpi-value">{value_str}</div>{delta_html}</div>')


cards_html = "".join([
    kpi_card("ROI", f"{current['roi']:.2f}x", "blue", current["roi"], compare["roi"] if compare else None),
    kpi_card("GMV", f"₹{format_inr(current['gmv'])}", "pink", current["gmv"], compare["gmv"] if compare else None),
    kpi_card("Spend", f"₹{format_inr(current['spend'])}", "green", current["spend"],
              compare["spend"] if compare else None),
    kpi_card("Impressions", format_inr(current['impressions']), "yellow", current["impressions"],
              compare["impressions"] if compare else None),
    kpi_card("eCPM", f"₹{format_inr(current['ecpm'], 2)}", "blue", current["ecpm"],
              compare["ecpm"] if compare else None),
    kpi_card("Clicks", format_inr(current['clicks']), "pink", current["clicks"],
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

insight_rows = []
if not qualifying.empty:
    qualifying["deviation"] = qualifying["roi"] - blended_roi
    top_outliers = qualifying.reindex(qualifying["deviation"].abs().sort_values(ascending=False).index).head(10)
    for _, r in top_outliers.iterrows():
        good = r["deviation"] >= 0
        icon, verb = ("🟢", "outperforming") if good else ("🔴", "underperforming")
        conv_rate = r["conv_rate"] if pd.notna(r["conv_rate"]) else 0
        insight_rows.append(
            f'<div style="padding:6px 0;border-bottom:1px solid {accents["blue"]["border"]};font-size:13px">'
            f'{icon} <b>{r["product_name"]}</b> — {r["roi"]:.2f}x ROI vs {blended_roi:.2f}x average '
            f'({verb} by {abs(r["deviation"]):.2f}x)<br>'
            f'<span style="color:{pal["muted"]}">₹{format_inr(r["spend"])} spend → ₹{format_inr(r["gmv"])} GMV, '
            f'{conv_rate:.0f}% conversion rate</span></div>'
        )

st.markdown(f'<div class="kpi-grid">{cards_html}</div>', unsafe_allow_html=True)

st.markdown(
    f'<div class="kpi-label" style="margin-bottom:6px;color:{accents["blue"]["fg"]}">AI INSIGHTS — TOP 10 '
    f'OUTLIER PRODUCTS (BY ROI DEVIATION)</div>', unsafe_allow_html=True,
)
if insight_rows:
    st.markdown(
        f'<div style="max-height:280px;overflow-y:auto;background:{accents["blue"]["bg"]};'
        f'border:1px solid {accents["blue"]["border"]};border-radius:12px;padding:10px 14px;margin-bottom:1.4rem">'
        + "".join(insight_rows) + "</div>",
        unsafe_allow_html=True,
    )
else:
    st.caption("No product-level data with meaningful spend in the current filter.")

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
    st.altair_chart(chart, use_container_width=True)
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
st.altair_chart(corr_bar, use_container_width=True)

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
st.altair_chart(money_chart, use_container_width=True)

impressions_col, rate_col = st.columns(2)
with impressions_col:
    st.caption("Daily impressions")
    impressions_chart = alt.Chart(daily).mark_line(strokeWidth=3, color=BOLD_CATEGORICAL[0]).encode(
        x=alt.X("metrics_date", title=None), y=alt.Y("impressions", title="Impressions"),
        tooltip=["metrics_date", "impressions"],
    )
    st.altair_chart(impressions_chart, use_container_width=True)
with rate_col:
    st.caption("Daily CTR & CVR")
    rates = daily.melt("metrics_date", value_vars=["CTR", "CVR"], var_name="series", value_name="value")
    rate_chart = alt.Chart(rates).mark_line(strokeWidth=3).encode(
        x=alt.X("metrics_date", title=None), y=alt.Y("value", title="%"),
        color=alt.Color("series", title=None, scale=alt.Scale(range=BOLD_CATEGORICAL[1:])),
        tooltip=["metrics_date", "series", alt.Tooltip("value", format=".2f")],
    )
    st.altair_chart(rate_chart, use_container_width=True)

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
st.altair_chart(city_chart, use_container_width=True)

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
st.altair_chart(format_chart, use_container_width=True)

# --- Top keywords ------------------------------------------------------------
st.subheader("Top keywords by GMV")
by_keyword_city = agg["by_keyword_city"]
if by_keyword_city.empty:
    st.caption("No keyword-level data in the current filter (many ad formats target by category, not keyword).")
else:
    st.dataframe(format_df_inr(by_keyword_city, ["gmv", "clicks", "conversions"]), use_container_width=True)

# --- Top search queries -------------------------------------------------------
st.subheader("Top search queries by GMV")
st.caption("The actual terms shoppers typed, not the keyword you targeted - from the Search Query report "
           "(not filtered by city, since that report doesn't break out by city).")
by_search_query = agg["by_search_query"]
if by_search_query.empty:
    st.caption("No search query data loaded yet - upload an IM_..._SEARCH_QUERY_*.csv file above.")
else:
    st.dataframe(format_df_inr(by_search_query, ["gmv", "clicks", "conversions"]), use_container_width=True)

# --- Campaign rollup ---------------------------------------------------------------
st.subheader("Campaign performance (within current filters)")
by_campaign = agg["by_campaign"].copy()
by_campaign["roi"] = (by_campaign["gmv"] / by_campaign["spend"]).round(2)
money_cols = ["gmv", "spend", "impressions", "clicks", "conversions"]
st.dataframe(format_df_inr(by_campaign.sort_values("gmv", ascending=False), money_cols),
             use_container_width=True)

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
    st.dataframe(format_df_inr(watchlist, money_cols), use_container_width=True)

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
    color=pal["muted"], strokeDash=[4, 4]
).encode(x="spend", y="gmv")
labels = alt.Chart(outlier_labels).mark_text(dy=-12, fontWeight="bold").encode(
    x="spend", y="gmv", text="campaign_name",
)
st.altair_chart((scatter + trend + labels).interactive(), use_container_width=True)

# --- Raw monthly summary export, if any has been loaded --------------------
summary = load_summary_table(DB_VERSION)
if not summary.empty:
    with st.expander("Raw monthly summary exports"):
        st.dataframe(summary, use_container_width=True)
