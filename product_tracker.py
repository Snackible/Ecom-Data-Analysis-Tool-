"""Per-product performance tracker - drill into any product's performance across
keywords, match types, cities, and search queries. Rendered as an alternate page
from dashboard.py; shares the same sidebar filters (date range, campaigns, cities).
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import config


def _blended_metrics(db_version, start_date, end_date, campaigns, cities):
    """Blended (aggregate) metrics for peer comparison."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_a2c) AS add_to_carts,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
            100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
            100.0 * SUM(total_a2c) / NULLIF(SUM(total_clicks), 0) AS a2c_rate,
            100.0 * SUM(total_conversions) / NULLIF(SUM(total_a2c), 0) AS conv_rate,
            SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()
    con.close()
    return df.iloc[0].to_dict()


@st.cache_data(show_spinner=False)
def _product_overview(db_version, start_date, end_date, campaigns, cities):
    """One row per product with all top-line metrics for sorting/filtering."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
            product_name,
            COUNT(DISTINCT metrics_date) AS days_active,
            COUNT(DISTINCT city) AS city_count,
            COUNT(DISTINCT keyword) AS keyword_count,
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_a2c) AS add_to_carts,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
            100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
            100.0 * SUM(total_a2c) / NULLIF(SUM(total_clicks), 0) AS a2c_rate,
            100.0 * SUM(total_conversions) / NULLIF(SUM(total_a2c), 0) AS conv_rate,
            SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
          AND product_name IS NOT NULL
        GROUP BY product_name
        ORDER BY gmv DESC
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _product_by_keyword(db_version, product_name, start_date, end_date, campaigns, cities):
    """For a given product: one row per (keyword, match_type) showing which
    keywords drive this product's performance."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
            keyword,
            match_type,
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_a2c) AS add_to_carts,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
            100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
            100.0 * SUM(total_a2c) / NULLIF(SUM(total_clicks), 0) AS a2c_rate,
            SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
          AND product_name = ?
          AND keyword IS NOT NULL
        GROUP BY keyword, match_type
        ORDER BY gmv DESC
    """, [start_date, end_date, *campaigns, *cities, product_name]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _product_by_city(db_version, product_name, start_date, end_date, campaigns, cities):
    """Geographic breakdown for this product."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
            city,
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_a2c) AS add_to_carts,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
            100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
            SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
          AND product_name = ?
        GROUP BY city
        ORDER BY gmv DESC
    """, [start_date, end_date, *campaigns, *cities, product_name]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _product_by_matchtype(db_version, product_name, start_date, end_date, campaigns, cities):
    """Broad vs Exact breakdown for this product."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
            CASE
                WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                ELSE 'Other'
            END AS match_type_label,
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_a2c) AS add_to_carts,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
            100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
            SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
          AND product_name = ?
        GROUP BY match_type
        ORDER BY spend DESC
    """, [start_date, end_date, *campaigns, *cities, product_name]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _product_over_time(db_version, product_name, start_date, end_date, campaigns, cities):
    """Daily trend for this product."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
            metrics_date,
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
            100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
          AND product_name = ?
        GROUP BY metrics_date
        ORDER BY metrics_date
    """, [start_date, end_date, *campaigns, *cities, product_name]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _product_search_queries(db_version, product_name, start_date, end_date, campaigns):
    """Top search queries that drove sales for this product (from search_query
    table - not city filterable due to CITY_COUNT being aggregate only)."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
            search_query,
            keyword,
            CASE
                WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                ELSE 'Other'
            END AS match_type,
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
            100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
            SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa
        FROM search_query
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND product_name = ?
          AND total_conversions > 0
        GROUP BY search_query, keyword, match_type
        ORDER BY gmv DESC
        LIMIT 20
    """, [start_date, end_date, *campaigns, product_name]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _product_underperformers(db_version, product_name, start_date, end_date, campaigns, cities):
    """Keywords for this product that are bleeding spend with low/zero ROI."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
            keyword,
            CASE
                WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                ELSE 'Other'
            END AS match_type,
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
            100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
          AND product_name = ?
          AND keyword IS NOT NULL
          AND SUM(total_budget_burnt) >= 100
        GROUP BY keyword, match_type
        HAVING SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) < 1.0
        ORDER BY spend DESC
    """, [start_date, end_date, *campaigns, *cities, product_name]).fetchdf()
    con.close()
    return df


def format_inr(value, decimals: int = 0) -> str:
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


def render(start_date, end_date, campaigns, cities):
    st.markdown("## 📦 Per-Product Tracker")
    st.markdown(
        "Click any product to see its deep-dive performance across keywords, "
        "match types, cities, and search queries."
    )

    db_version = config.get_db_version()
    blended = _blended_metrics(db_version, start_date, end_date, campaigns, cities)
    products = _product_overview(db_version, start_date, end_date, campaigns, cities)

    if products.empty:
        st.info("No product data for the selected filters.")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        sort_by = st.selectbox("Sort by:", ["GMV", "ROI", "Spend", "Conversions"], key="prod_sort")
    with col2:
        min_spend = st.number_input("Min spend (₹):", value=0, min_value=0, key="prod_min_spend")
    with col3:
        view_type = st.radio("View:", ["List", "Top 10"], horizontal=True, key="prod_view")

    sort_map = {"GMV": "gmv", "ROI": "roi", "Spend": "spend", "Conversions": "conversions"}
    products_filtered = products[products["spend"] >= min_spend].sort_values(
        by=sort_map[sort_by], ascending=False
    )

    if view_type == "Top 10":
        products_filtered = products_filtered.head(10)

    st.write(f"**{len(products_filtered)} products** (blended ROI: **{blended['roi']:.2f}x**)")

    for idx, row in products_filtered.iterrows():
        with st.expander(
            f"**{row['product_name']}** | GMV ₹{format_inr(row['gmv'])} | ROI {row['roi']:.2f}x | Spend ₹{format_inr(row['spend'])}"
        ):
            _render_product_deep_dive(
                row["product_name"],
                row,
                blended,
                db_version,
                start_date,
                end_date,
                campaigns,
                cities,
                products_filtered,
            )


def _render_product_deep_dive(product_name, row, blended, db_version, start_date, end_date, campaigns, cities, all_products):
    """Deep dive for a single product."""
    st.write(f"### {product_name}")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            "ROI",
            f"{row['roi']:.2f}x",
            f"{'+' if row['roi'] >= blended['roi'] else ''}{(row['roi'] - blended['roi']):.2f}x vs blended",
            delta_color="normal" if row['roi'] >= blended['roi'] else "inverse",
        )
    with col2:
        total_gmv = all_products['gmv'].sum()
        pct_of_total = (100 * row['gmv'] / total_gmv) if total_gmv > 0 else 0
        st.metric("GMV", f"₹{format_inr(row['gmv'])}", f"{pct_of_total:.1f}% of total")
    with col3:
        st.metric("Spend", f"₹{format_inr(row['spend'])}")
    with col4:
        st.metric("Conversions", f"{int(row['conversions'])}")

    st.divider()

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["Keywords", "Match Type", "Geography", "Search Queries", "Underperformers"]
    )

    with tab1:
        st.write("**Performance by Keyword**")
        kw_data = _product_by_keyword(db_version, product_name, start_date, end_date, campaigns, cities)
        if not kw_data.empty:
            kw_display = kw_data[[
                "keyword", "match_type", "spend", "conversions", "gmv", "roi", "ctr", "a2c_rate"
            ]].copy()
            kw_display["match_type"] = kw_display["match_type"].map({
                "KEYWORD_MATCH_TYPE_BROAD": "Broad",
                "KEYWORD_MATCH_TYPE_EXACT": "Exact",
                "KEYWORD_MATCH_TYPE_INVALID": "Other"
            }).fillna(kw_display["match_type"])
            st.dataframe(kw_display, use_container_width=True)
        else:
            st.info("No keyword data for this product.")

    with tab2:
        st.write("**Broad Match vs Exact Match**")
        mt_data = _product_by_matchtype(db_version, product_name, start_date, end_date, campaigns, cities)
        if not mt_data.empty:
            col1, col2 = st.columns(2)
            with col1:
                st.dataframe(mt_data[["match_type_label", "spend", "conversions", "roi"]].set_index("match_type_label"), use_container_width=True)
            with col2:
                chart = alt.Chart(mt_data).mark_bar().encode(
                    x="match_type_label:N",
                    y="roi:Q",
                    color="match_type_label:N",
                ).properties(height=300)
                st.altair_chart(chart, use_container_width=True)

    with tab3:
        st.write("**Top 10 Cities**")
        city_data = _product_by_city(db_version, product_name, start_date, end_date, campaigns, cities)
        if not city_data.empty:
            city_display = city_data.head(10)[["city", "spend", "gmv", "roi", "conversions"]].copy()
            st.dataframe(city_display, use_container_width=True)
        else:
            st.info("No city data for this product.")

    with tab4:
        st.write("**Top Search Queries (Converters)**")
        sq_data = _product_search_queries(db_version, product_name, start_date, end_date, campaigns)
        if not sq_data.empty:
            sq_display = sq_data[[
                "search_query", "keyword", "match_type", "conversions", "gmv", "roi", "ctr"
            ]].head(15).copy()
            st.dataframe(sq_display, use_container_width=True)
        else:
            st.info("No search query data for this product.")

    with tab5:
        st.write("**Underperforming Keywords (ROI < 1.0)**")
        under_data = _product_underperformers(db_version, product_name, start_date, end_date, campaigns, cities)
        if not under_data.empty:
            st.warning(f"🚩 {len(under_data)} keywords bleeding spend with sub-1.0 ROI")
            under_display = under_data[["keyword", "match_type", "spend", "conversions", "roi", "ctr"]].copy()
            st.dataframe(under_display, use_container_width=True)
            st.markdown(
                f"**Recommendation:** Pause or deeply audit these {len(under_data)} keywords. "
                f"Combined spend: ₹{format_inr(under_data['spend'].sum())} "
                f"with only {int(under_data['conversions'].sum())} conversions."
            )
        else:
            st.success("✅ No underperforming keywords (all have ROI ≥ 1.0)")

    col1, col2 = st.columns(2)
    with col1:
        st.write("**Daily GMV Trend**")
        trend_data = _product_over_time(db_version, product_name, start_date, end_date, campaigns, cities)
        if not trend_data.empty:
            chart = alt.Chart(trend_data).mark_line(point=True).encode(
                x="metrics_date:T",
                y="gmv:Q",
                tooltip=["metrics_date:T", "gmv:Q", "roi:Q"]
            ).properties(height=300)
            st.altair_chart(chart, use_container_width=True)

    with col2:
        st.write("**Daily ROI Trend**")
        if not trend_data.empty:
            chart = alt.Chart(trend_data).mark_line(point=True, color="orange").encode(
                x="metrics_date:T",
                y="roi:Q",
                tooltip=["metrics_date:T", "roi:Q", "conversions:Q"]
            ).properties(height=300)
            st.altair_chart(chart, use_container_width=True)
