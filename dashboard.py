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
import pandas as pd
import streamlit as st

import config
import ingest

st.set_page_config(page_title="Instamart Ads Dashboard", layout="wide")


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
                 accent="#2F6FFF", positive="#22D07E", negative="#FF4D4F", border="#232A3B"),
    "light": dict(bg="#F5F7FB", surface="#FFFFFF", text="#0B0F19", muted="#5B6472",
                  accent="#2F6FFF", positive="#10B981", negative="#EF4444", border="#E1E5EF"),
}
BOLD_CATEGORICAL = ["#2F6FFF", "#EC4899", "#F59E0B", "#22D07E", "#8B5CF6", "#06B6D4", "#FF4D4F", "#84CC16"]

if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = True
st.sidebar.toggle("🌙 Dark mode", key="dark_mode")
pal = PALETTES["dark" if st.session_state.dark_mode else "light"]

st.markdown(f"""
<style>
.stApp {{ background-color: {pal['bg']}; }}
.stApp, .stApp p, .stApp span, .stApp label {{ color: {pal['text']}; }}
[data-testid="stSidebar"] {{ background-color: {pal['surface']}; border-right: 1px solid {pal['border']}; }}
h1, h2, h3 {{ color: {pal['text']} !important; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(2, minmax(140px, 1fr)); gap: 10px;
             max-width: 520px; margin-bottom: 1.2rem; }}
.kpi-card {{ background: {pal['surface']}; border: 1px solid {pal['border']}; border-left: 4px solid {pal['accent']};
             border-radius: 10px; padding: 10px 14px; }}
.kpi-label {{ font-size: 11px; color: {pal['muted']}; text-transform: uppercase; letter-spacing: .05em; }}
.kpi-value {{ font-size: 22px; font-weight: 800; color: {pal['text']}; margin-top: 2px; }}
.kpi-delta-up {{ color: {pal['positive']}; font-size: 12px; font-weight: 700; margin-top: 2px; }}
.kpi-delta-down {{ color: {pal['negative']}; font-size: 12px; font-weight: 700; margin-top: 2px; }}
</style>
""", unsafe_allow_html=True)

st.title("Instamart Ads Dashboard")

# --- Upload panel ------------------------------------------------------------
with st.expander("📤 Upload new CSV/Excel exports", expanded=not config.DB_PATH.exists()):
    uploaded_files = st.file_uploader(
        "Drop IM_SUMMARY_*/IM_GRANULAR_* files here, .csv or .xlsx "
        "(filename must contain SUMMARY or GRANULAR)",
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
            SELECT metrics_date, sum(total_gmv) "GMV", sum(total_budget_burnt) "Spend"
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

        return dict(totals=totals, by_campaign=by_campaign, by_city=by_city, by_format=by_format,
                    by_keyword_city=by_keyword_city, by_product=by_product, daily=daily, corr_long=corr_long)
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


def kpi_card(label: str, value_str: str, cur_val=None, prev_val=None) -> str:
    delta_html = ""
    if compare is not None and cur_val is not None and prev_val is not None:
        d = pct_delta(cur_val, prev_val)
        if d is not None:
            cls = "kpi-delta-up" if d >= 0 else "kpi-delta-down"
            arrow = "▲" if d >= 0 else "▼"
            delta_html = f'<div class="{cls}">{arrow} {abs(d):.1f}% vs compare</div>'
    return (f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value_str}</div>{delta_html}</div>')


cards_html = "".join([
    kpi_card("ROI", f"{current['roi']:.2f}x", current["roi"], compare["roi"] if compare else None),
    kpi_card("GMV", f"₹{current['gmv']:,.0f}", current["gmv"], compare["gmv"] if compare else None),
    kpi_card("Spend", f"₹{current['spend']:,.0f}", current["spend"], compare["spend"] if compare else None),
    kpi_card("Impressions", f"{current['impressions']:,.0f}", current["impressions"],
              compare["impressions"] if compare else None),
    kpi_card("eCPM", f"₹{current['ecpm']:.2f}", current["ecpm"], compare["ecpm"] if compare else None),
    kpi_card("Clicks", f"{current['clicks']:,.0f}", current["clicks"], compare["clicks"] if compare else None),
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
            f'<div style="padding:6px 0;border-bottom:1px solid {pal["border"]};font-size:13px">'
            f'{icon} <b>{r["product_name"]}</b> — {r["roi"]:.2f}x ROI vs {blended_roi:.2f}x average '
            f'({verb} by {abs(r["deviation"]):.2f}x)<br>'
            f'<span style="color:{pal["muted"]}">₹{r["spend"]:,.0f} spend → ₹{r["gmv"]:,.0f} GMV, '
            f'{conv_rate:.0f}% conversion rate</span></div>'
        )

st.markdown(f'<div class="kpi-grid">{cards_html}</div>', unsafe_allow_html=True)

st.markdown(
    f'<div class="kpi-label" style="margin-bottom:6px">AI INSIGHTS — TOP 10 OUTLIER PRODUCTS '
    f'(BY ROI DEVIATION)</div>', unsafe_allow_html=True,
)
if insight_rows:
    st.markdown(
        f'<div style="max-height:280px;overflow-y:auto;background:{pal["surface"]};'
        f'border:1px solid {pal["border"]};border-radius:10px;padding:8px 12px;margin-bottom:1.2rem">'
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
    st.caption(f"{total_impressions:,.0f} impressions (top KPI above) feed into the funnel below.")
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
st.caption("Pearson correlation across every row in the current filter (computed in SQL, not loaded into "
           "pandas). +1 = move together, -1 = move opposite, 0 = no linear relationship.")
corr_long = agg["corr_long"]

heatmap = alt.Chart(corr_long).mark_rect().encode(
    x=alt.X("metric1", title=None), y=alt.Y("metric2", title=None),
    color=alt.Color("correlation", scale=alt.Scale(scheme="redblue", domain=[-1, 1]), title="Correlation"),
    tooltip=["metric1", "metric2", alt.Tooltip("correlation", format=".2f")],
)
labels = alt.Chart(corr_long).mark_text().encode(
    x="metric1", y="metric2", text=alt.Text("correlation", format=".2f"),
    color=alt.condition("abs(datum.correlation) > 0.6", alt.value("white"), alt.value("black")),
)
st.altair_chart(heatmap + labels, use_container_width=True)

# --- GMV trend -----------------------------------------------------------
st.subheader("Daily GMV & spend")
daily = agg["daily"]
daily_long = daily.melt("metrics_date", var_name="series", value_name="value")
y_max = max(200_000, daily[["GMV", "Spend"]].to_numpy().max())
daily_chart = alt.Chart(daily_long).mark_line(strokeWidth=3).encode(
    x=alt.X("metrics_date", title=None),
    y=alt.Y("value", title="₹", scale=alt.Scale(domain=[0, y_max])),
    color=alt.Color("series", title=None, scale=alt.Scale(range=BOLD_CATEGORICAL)),
    tooltip=["metrics_date", "series", "value"],
)
st.altair_chart(daily_chart, use_container_width=True)

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
    st.dataframe(by_keyword_city, use_container_width=True)

# --- Campaign rollup ---------------------------------------------------------------
st.subheader("Campaign performance (within current filters)")
by_campaign = agg["by_campaign"].copy()
by_campaign["roi"] = (by_campaign["gmv"] / by_campaign["spend"]).round(2)
st.dataframe(by_campaign.sort_values("gmv", ascending=False), use_container_width=True)

# --- Underperformers ---------------------------------------------------------
st.subheader("Underperformers to look at")
median_spend = by_campaign["spend"].median()
watchlist = by_campaign[
    (by_campaign["spend"] >= median_spend) & (by_campaign["roi"] < blended_roi)
].sort_values("spend", ascending=False)
if watchlist.empty:
    st.success("No above-median-spend campaign is returning below the blended ROI right now.")
else:
    st.caption(f"Spending at/above the median (₹{median_spend:,.0f}) but returning less than the "
               f"blended ROI ({blended_roi:.2f}x) - budget worth re-examining first.")
    st.dataframe(watchlist, use_container_width=True)

# --- Spend vs outcome -------------------------------------------------------
st.subheader("Spend vs GMV by campaign — does more spend pay off?")
st.caption("Each point is one campaign. Bubble size = impressions, color = ROI, dashed line = linear trend. "
           "A campaign sitting below/right of the trend is spending more per rupee of GMV returned.")
scatter = alt.Chart(by_campaign).mark_circle(opacity=0.8).encode(
    x=alt.X("spend", title="Spend (₹)"),
    y=alt.Y("gmv", title="GMV (₹)"),
    size=alt.Size("impressions", legend=None),
    color=alt.Color("roi", scale=alt.Scale(scheme="redyellowgreen"), title="ROI"),
    tooltip=["campaign_name", "spend", "gmv", "roi", "impressions"],
)
trend = alt.Chart(by_campaign).transform_regression("spend", "gmv").mark_line(
    color=pal["muted"], strokeDash=[4, 4]
).encode(x="spend", y="gmv")
st.altair_chart((scatter + trend).interactive(), use_container_width=True)

# --- Raw monthly summary export, if any has been loaded --------------------
summary = load_summary_table(DB_VERSION)
if not summary.empty:
    with st.expander("Raw monthly summary exports"):
        st.dataframe(summary, use_container_width=True)
