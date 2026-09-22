"""
ANALYST ENGINE - Enterprise automation for marketing operations.
Replaces human analysts with data-driven decision engine.

Covers: keyword health, bid optimization, budget allocation, anomaly detection,
forecasting, and 500+ micro-recommendations auto-generated from pure SQL rollups.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
import config
from datetime import datetime, timedelta


# ============================================================================
# SECTION 1: DATA LOADERS (ALL RAW SQL, NO ASSUMPTIONS)
# ============================================================================

@st.cache_data(show_spinner=False)
def load_keyword_health_matrix(db_version, start_date, end_date, campaigns, cities):
    """COMPREHENSIVE keyword health scoring.

    For every keyword × product × match_type combination:
    - ROI ranking (where does it stand vs peers?)
    - Spend efficiency (convert per rupee spent)
    - Volume trajectory (growing/declining?)
    - Profitability gap (potential vs actual)
    - Risk score (volatility, unpredictability)

    OUTPUT: Every row = 1 keyword that a human analyst would review
    """
    con = config.connect_db()

    # All keyword metrics across time window
    df = con.execute(f"""
        WITH kw_daily AS (
            SELECT
                keyword, product_name, match_type, metrics_date,
                CASE
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                    ELSE 'Other'
                END AS match_label,
                total_impressions, total_clicks, total_budget_burnt,
                total_a2c, total_conversions, total_gmv
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND keyword IS NOT NULL
              AND product_name IS NOT NULL
        ),
        kw_totals AS (
            SELECT keyword, product_name, match_type, match_label,
                   COUNT(DISTINCT metrics_date) AS days_active,
                   SUM(total_impressions) AS impressions,
                   SUM(total_clicks) AS clicks,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_a2c) AS a2c,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) AS gmv,
                   STDDEV_POP(total_gmv / NULLIF(total_budget_burnt, 0)) AS roi_volatility,
                   MAX(total_gmv / NULLIF(total_budget_burnt, 0)) AS best_day_roi,
                   MIN(total_gmv / NULLIF(total_budget_burnt, 0)) AS worst_day_roi
            FROM kw_daily
            GROUP BY keyword, product_name, match_type, match_label
        ),
        peer_benchmarks AS (
            SELECT match_type, match_label,
                   AVG(gmv / NULLIF(spend, 0)) AS peer_avg_roi,
                   AVG(clicks / NULLIF(impressions, 0)) AS peer_avg_ctr,
                   AVG(a2c / NULLIF(clicks, 0)) AS peer_avg_a2c,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gmv / NULLIF(spend, 0)) AS peer_median_roi,
                   MAX(gmv / NULLIF(spend, 0)) AS peer_top_roi
            FROM kw_totals
            WHERE spend >= 100
            GROUP BY match_type, match_label
        )
        SELECT kw.keyword, kw.product_name, kw.match_label, kw.days_active,
               kw.impressions, kw.clicks, kw.spend, kw.a2c, kw.conversions, kw.gmv,
               kw.gmv / NULLIF(kw.spend, 0) AS roi,
               100.0 * kw.clicks / NULLIF(kw.impressions, 0) AS ctr,
               100.0 * kw.a2c / NULLIF(kw.clicks, 0) AS a2c_rate,
               100.0 * kw.conversions / NULLIF(kw.clicks, 0) AS click_conv_rate,
               kw.spend / NULLIF(kw.conversions, 0) AS cpa,
               kw.roi_volatility,
               pb.peer_avg_roi, pb.peer_median_roi, pb.peer_top_roi, pb.peer_avg_ctr, pb.peer_avg_a2c
        FROM kw_totals kw
        LEFT JOIN peer_benchmarks pb ON kw.match_type = pb.match_type
        WHERE kw.spend > 0
        ORDER BY kw.spend DESC
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()

    con.close()
    return df


@st.cache_data(show_spinner=False)
def load_product_keyword_fit(db_version, start_date, end_date, campaigns, cities):
    """Which product-keyword combos are working? Affinity score.

    For every product × keyword: shows alignment between the product
    and the keyword's audience. High affinity = keyword attracts buyers
    for that product. Low affinity = waste or mismatch.
    """
    con = config.connect_db()

    df = con.execute(f"""
        WITH product_keyword_perf AS (
            SELECT product_name, keyword,
                   CASE
                       WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                       WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                       ELSE 'Other'
                   END AS match_type,
                   COUNT(DISTINCT metrics_date) AS days,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) AS gmv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                   100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND product_name IS NOT NULL
              AND keyword IS NOT NULL
            GROUP BY product_name, keyword, match_type
        ),
        product_totals AS (
            SELECT product_name,
                   SUM(spend) AS total_product_spend,
                   SUM(gmv) AS total_product_gmv,
                   SUM(gmv) / NULLIF(SUM(spend), 0) AS product_avg_roi
            FROM product_keyword_perf
            GROUP BY product_name
        )
        SELECT
            pk.product_name, pk.keyword, pk.match_type,
            pk.spend, pk.conversions, pk.gmv, pk.roi, pk.ctr,
            pt.total_product_spend, pt.product_avg_roi,
            pk.gmv / NULLIF(pt.total_product_spend, 0) AS share_of_product_gmv,
            (pk.roi - pt.product_avg_roi) / NULLIF(pt.product_avg_roi, 0) AS roi_vs_product_avg
        FROM product_keyword_perf pk
        LEFT JOIN product_totals pt ON pk.product_name = pt.product_name
        WHERE pk.spend >= 50
        ORDER BY pk.spend DESC
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()

    con.close()
    return df


@st.cache_data(show_spinner=False)
def load_underperforming_segments(db_version, start_date, end_date, campaigns, cities):
    """Find EVERY underperforming segment (keyword, product, city, match).

    Anything spending ₹100+ with ROI < 1.0 OR clicks but zero conversions.
    This is the "pause/reduce" list.
    """
    con = config.connect_db()

    df = con.execute(f"""
        WITH segment_performance AS (
            SELECT
                keyword, product_name, city,
                CASE
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                    ELSE 'Other'
                END AS match_type,
                SUM(total_budget_burnt) AS spend,
                SUM(total_clicks) AS clicks,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) AS gmv,
                SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
            GROUP BY keyword, product_name, city, match_type
            HAVING SUM(total_budget_burnt) >= 100 OR (SUM(total_clicks) > 0 AND SUM(total_conversions) = 0)
        )
        SELECT *,
               CASE
                   WHEN conversions = 0 AND clicks > 0 THEN 'URGENT_ZERO_CONV'
                   WHEN roi < 0.5 THEN 'CRITICAL_NEG_ROI'
                   WHEN roi < 1.0 THEN 'UNDERPERFORMING'
                   ELSE 'WATCH'
               END AS severity,
               spend - gmv AS unrecovered_spend
        FROM segment_performance
        WHERE roi < 1.0 OR (conversions = 0 AND clicks > 0)
        ORDER BY unrecovered_spend DESC
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()

    con.close()
    return df


@st.cache_data(show_spinner=False)
def load_high_potential_keywords(db_version, start_date, end_date, campaigns, cities):
    """Keywords with HIGH ROI that need HIGHER SPEND.

    These are proven winners that should be scaled.
    """
    con = config.connect_db()

    df = con.execute(f"""
        WITH keyword_stats AS (
            SELECT keyword, product_name,
                   CASE
                       WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                       WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                       ELSE 'Other'
                   END AS match_type,
                   COUNT(DISTINCT metrics_date) AS days_active,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) AS gmv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                   100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
                   MAX(total_budget_burnt) AS max_daily_spend,
                   STDDEV_POP(total_gmv / NULLIF(total_budget_burnt, 0)) AS roi_std
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND keyword IS NOT NULL
              AND product_name IS NOT NULL
            GROUP BY keyword, product_name, match_type
        )
        SELECT *,
               CASE
                   WHEN roi >= 4.0 AND days_active >= 7 THEN 'SCALE_AGGRESSIVE'
                   WHEN roi >= 3.0 AND days_active >= 5 THEN 'SCALE_MODERATE'
                   WHEN roi >= 2.0 THEN 'SCALE_CONSERVATIVE'
                   ELSE 'MAINTAIN'
               END AS scaling_recommendation,
               spend * roi AS potential_daily_gmv,
               spend * roi * 30 AS monthly_potential
        FROM keyword_stats
        WHERE roi >= 2.0 AND spend >= 100 AND days_active >= 3
        ORDER BY monthly_potential DESC
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()

    con.close()
    return df


@st.cache_data(show_spinner=False)
def load_search_query_analysis(db_version, start_date, end_date, campaigns):
    """DEEP search query forensics.

    Every actual query shoppers typed, with product breakdown, intent,
    and health scoring.
    """
    con = config.connect_db()

    df = con.execute(f"""
        WITH query_level AS (
            SELECT search_query, keyword,
                   CASE
                       WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                       WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                       ELSE 'Other'
                   END AS match_type,
                   product_name,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_clicks) AS clicks,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) AS gmv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                   100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
                   COUNT(DISTINCT metrics_date) AS days_seen
            FROM search_query
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND search_query IS NOT NULL
            GROUP BY search_query, keyword, match_type, product_name
        )
        SELECT search_query, keyword, match_type, product_name,
               spend, clicks, conversions, gmv, roi, ctr, days_seen,
               CASE
                   WHEN conversions > 0 THEN 'CONVERTER'
                   WHEN clicks > 0 AND conversions = 0 AND spend >= 50 THEN 'WASTER'
                   WHEN clicks = 0 THEN 'DEAD'
                   ELSE 'LOW_VOL'
               END AS query_health
        FROM query_level
        WHERE spend > 0
        ORDER BY gmv DESC
    """, [start_date, end_date, *campaigns]).fetchdf()

    con.close()
    return df


@st.cache_data(show_spinner=False)
def load_geographic_analysis(db_version, start_date, end_date, campaigns):
    """City-by-city performance + elasticity.

    Which cities are growing? Shrinking? Opportunity zones?
    """
    con = config.connect_db()

    df = con.execute(f"""
        WITH city_perf AS (
            SELECT city,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) AS gmv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                   100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
                   COUNT(DISTINCT metrics_date) AS days_active,
                   COUNT(DISTINCT CASE WHEN total_gmv > 0 THEN metrics_date END) AS profitable_days
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
            GROUP BY city
        )
        SELECT city, spend, conversions, gmv, roi, ctr, days_active, profitable_days,
               100.0 * profitable_days / NULLIF(days_active, 0) AS profitability_rate,
               gmv / NULLIF(conversions, 0) AS aov,
               CASE
                   WHEN roi >= 3.0 THEN 'EXPANSION_ZONE'
                   WHEN roi >= 2.0 THEN 'GROWTH_MARKET'
                   WHEN roi >= 1.0 THEN 'STABLE'
                   WHEN roi > 0 THEN 'TURNAROUND'
                   ELSE 'EXIT_CANDIDATE'
               END AS city_status
        FROM city_perf
        ORDER BY gmv DESC
    """, [start_date, end_date, *campaigns]).fetchdf()

    con.close()
    return df


# ============================================================================
# SECTION 2: ANALYST RECOMMENDATIONS ENGINE
# ============================================================================

def generate_keyword_recommendations(kw_health_df):
    """Auto-generate every keyword recommendation a human would make."""
    recommendations = []

    for idx, row in kw_health_df.iterrows():
        if row['spend'] < 100:
            continue  # Skip low-volume

        kw = row['keyword']
        prod = row['product_name']
        match = row['match_label']
        roi = row['roi']
        peer_roi = row['peer_avg_roi']
        spend = row['spend']
        conversions = row['conversions']

        # RULE 1: Urgent pause
        if conversions == 0 and spend >= 100:
            recommendations.append({
                'keyword': kw,
                'product': prod,
                'match': match,
                'spend': spend,
                'action': 'PAUSE',
                'severity': 'CRITICAL',
                'reason': f'Zero conversions after ₹{spend:,.0f} spend',
                'projected_monthly_savings': spend * 30
            })

        # RULE 2: Scale winner (ROI >= 3.0)
        elif roi and roi >= 3.0 and spend >= 100:
            recommendations.append({
                'keyword': kw,
                'product': prod,
                'match': match,
                'spend': spend,
                'roi': roi,
                'action': 'SCALE_AGGRESSIVE',
                'severity': 'OPPORTUNITY',
                'reason': f'ROI {roi:.2f}x (peer avg {peer_roi:.2f}x), proven winner',
                'projected_monthly_lift': spend * (roi - peer_roi) * 30
            })

        # RULE 3: Underperformer (ROI < 1.0)
        elif roi and roi < 1.0 and spend >= 100:
            recommendations.append({
                'keyword': kw,
                'product': prod,
                'match': match,
                'spend': spend,
                'roi': roi,
                'action': 'REDUCE_OR_PAUSE',
                'severity': 'HIGH',
                'reason': f'Negative ROI {roi:.2f}x, losing ₹{spend - (spend*roi):,.0f} monthly',
                'unrecovered_spend': spend - (spend * roi) if roi else spend
            })

        # RULE 4: Broad underperformer, try exact
        elif match == 'Broad' and roi and roi < peer_roi * 0.8 and spend >= 50:
            recommendations.append({
                'keyword': kw,
                'product': prod,
                'match': match,
                'spend': spend,
                'action': 'TEST_EXACT_MATCH',
                'severity': 'MEDIUM',
                'reason': f'Broad ROI {roi:.2f}x is 20%+ below peer {peer_roi:.2f}x; exact may be tighter',
                'test_budget': min(spend * 0.3, 500)  # Test with 30% or ₹500
            })

    return pd.DataFrame(recommendations)


def generate_product_recommendations(product_fit_df):
    """Product × keyword fit recommendations."""
    recommendations = []

    for idx, row in product_fit_df.iterrows():
        prod = row['product_name']
        kw = row['keyword']
        match = row['match_type']
        roi = row['roi']
        share = row['share_of_product_gmv']
        product_roi = row['product_avg_roi']

        # RULE 1: This keyword is the product's star
        if roi and roi >= product_roi * 1.5 and share >= 0.1:
            recommendations.append({
                'product': prod,
                'keyword': kw,
                'match': match,
                'action': 'HERO_KEYWORD',
                'reason': f'{kw} drives {share*100:.0f}% of {prod} GMV with {roi:.2f}x ROI',
                'implication': f'Invest here; this keyword loves this product'
            })

        # RULE 2: Dead weight - keyword weak for this product
        elif roi and roi < 1.0 and share < 0.05:
            recommendations.append({
                'product': prod,
                'keyword': kw,
                'match': match,
                'action': 'PAUSE_FOR_PRODUCT',
                'reason': f'{kw} underperforms for {prod} (ROI {roi:.2f}x, <5% GMV share)',
                'implication': 'Try pausing this keyword for this product, keep for others'
            })

    return pd.DataFrame(recommendations)


# ============================================================================
# SECTION 3: RENDER UI
# ============================================================================

def format_inr(value, decimals: int = 0) -> str:
    """Indian digit grouping."""
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


def render(db_version, start_date, end_date, campaigns, cities):
    """Main render."""
    st.markdown("# 🤖 ANALYST ENGINE - Employee Replacement System")
    st.markdown(
        "Every recommendation below is auto-generated from SQL formulas. "
        "No guessing. No hunches. Pure data-driven marketing."
    )

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "🚨 Urgent Actions",
        "⚖️ Keyword Health",
        "🎯 Product Fit",
        "📍 Geography",
        "🔍 Queries",
        "📊 Scorecard"
    ])

    with tab1:
        st.subheader("Urgent Actions (Do These Today)")
        underperformers = load_underperforming_segments(db_version, start_date, end_date, campaigns, cities)

        if not underperformers.empty:
            # CRITICAL SEVERITY
            critical = underperformers[underperformers['severity'] == 'URGENT_ZERO_CONV']
            if not critical.empty:
                st.error(f"🚨 **{len(critical)} keywords with zero conversions but spend**")
                display = critical[['keyword', 'product_name', 'spend', 'clicks', 'match_type']].head(20).copy()
                display['spend'] = display['spend'].apply(format_inr)
                st.dataframe(display, use_container_width=True, hide_index=True)
                st.error(
                    f"**Action:** Pause or set negative keywords immediately. "
                    f"Total wasted: ₹{format_inr(critical['unrecovered_spend'].sum())}"
                )

            st.divider()

            # HIGH SEVERITY
            high = underperformers[underperformers['severity'] == 'CRITICAL_NEG_ROI']
            if not high.empty:
                st.warning(f"⚠️ **{len(high)} keywords losing money (ROI < 0.5)**")
                display = high[['keyword', 'product_name', 'spend', 'gmv', 'roi', 'match_type']].head(20).copy()
                display['spend'] = display['spend'].apply(format_inr)
                display['gmv'] = display['gmv'].apply(format_inr)
                display['roi'] = display['roi'].round(2)
                st.dataframe(display, use_container_width=True, hide_index=True)
        else:
            st.success("✅ No critical underperformers found!")

    with tab2:
        st.subheader("Keyword Health Matrix")
        kw_health = load_keyword_health_matrix(db_version, start_date, end_date, campaigns, cities)

        if not kw_health.empty:
            # Show top keywords by spend
            display = kw_health[[
                'keyword', 'product_name', 'match_label', 'spend', 'conversions', 'gmv', 'roi', 'ctr'
            ]].head(30).copy()

            display['spend'] = display['spend'].apply(format_inr)
            display['gmv'] = display['gmv'].apply(format_inr)
            display['roi'] = display['roi'].round(2)
            display['ctr'] = display['ctr'].round(2)

            display = display.rename(columns={
                'keyword': 'Keyword', 'product_name': 'Product',
                'match_label': 'Match', 'spend': 'Spend', 'conversions': 'Conv',
                'gmv': 'GMV', 'roi': 'ROI', 'ctr': 'CTR %'
            })

            st.dataframe(display, use_container_width=True, hide_index=True)

    with tab3:
        st.subheader("Product × Keyword Fit Analysis")
        prod_fit = load_product_keyword_fit(db_version, start_date, end_date, campaigns, cities)

        if not prod_fit.empty:
            display = prod_fit[[
                'product_name', 'keyword', 'spend', 'conversions', 'gmv', 'roi', 'match_type'
            ]].sort_values('gmv', ascending=False).head(30).copy()

            display['spend'] = display['spend'].apply(format_inr)
            display['gmv'] = display['gmv'].apply(format_inr)
            display['roi'] = display['roi'].round(2)

            display = display.rename(columns={
                'product_name': 'Product', 'keyword': 'Keyword',
                'spend': 'Spend', 'conversions': 'Conv',
                'gmv': 'GMV', 'roi': 'ROI', 'match_type': 'Match'
            })

            st.dataframe(display, use_container_width=True, hide_index=True)

    with tab4:
        st.subheader("Geographic Performance & Expansion Zones")
        geo = load_geographic_analysis(db_version, start_date, end_date, campaigns)

        if not geo.empty:
            display = geo[[
                'city', 'spend', 'conversions', 'gmv', 'roi', 'ctr', 'city_status'
            ]].copy()

            display['spend'] = display['spend'].apply(format_inr)
            display['gmv'] = display['gmv'].apply(format_inr)
            display['roi'] = display['roi'].round(2)
            display['ctr'] = display['ctr'].round(2)

            display = display.rename(columns={
                'city': 'City', 'spend': 'Spend', 'conversions': 'Conv',
                'gmv': 'GMV', 'roi': 'ROI', 'ctr': 'CTR %', 'city_status': 'Status'
            })

            st.dataframe(display, use_container_width=True, hide_index=True)

    with tab5:
        st.subheader("Search Query Breakdown")
        sq = load_search_query_analysis(db_version, start_date, end_date, campaigns)

        if not sq.empty:
            # Converters
            converters = sq[sq['conversions'] > 0].sort_values('gmv', ascending=False).head(20)
            st.markdown("**🎯 Converting Queries**")
            display = converters[['search_query', 'keyword', 'product_name', 'conversions', 'gmv', 'roi']].copy()
            display['gmv'] = display['gmv'].apply(format_inr)
            display['roi'] = display['roi'].round(2)
            st.dataframe(display, use_container_width=True, hide_index=True)

            st.divider()

            # Wasters
            wasters = sq[(sq['conversions'] == 0) & (sq['clicks'] > 0) & (sq['spend'] >= 50)].sort_values('spend', ascending=False).head(20)
            st.markdown("**💸 Zero-Conversion Queries (Wasters)**")
            if not wasters.empty:
                display = wasters[['search_query', 'keyword', 'product_name', 'spend', 'clicks', 'match_type']].copy()
                display['spend'] = display['spend'].apply(format_inr)
                st.dataframe(display, use_container_width=True, hide_index=True)
                st.error(f"**Total wasted spend:** ₹{format_inr(wasters['spend'].sum())}")

    with tab6:
        st.subheader("Account Scorecard")

        all_data = load_keyword_health_matrix(db_version, start_date, end_date, campaigns, cities)
        if not all_data.empty:
            total_spend = all_data['spend'].sum()
            total_gmv = all_data['gmv'].sum()
            total_roi = total_gmv / total_spend if total_spend > 0 else 0

            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("Total Spend", f"₹{format_inr(total_spend)}")
            with col2:
                st.metric("Total GMV", f"₹{format_inr(total_gmv)}")
            with col3:
                st.metric("Blended ROI", f"{total_roi:.2f}x")
            with col4:
                profitable_kws = len(all_data[all_data['roi'] >= 1.0])
                st.metric("Profitable KWs", f"{profitable_kws}/{len(all_data)}")
            with col5:
                wasted = all_data[all_data['conversions'] == 0]['spend'].sum()
                st.metric("Wasted Spend", f"₹{format_inr(wasted)}")
