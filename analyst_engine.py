"""
ANALYST ENGINE - Pure SQL-driven marketing automation.
Zero external dependencies beyond what Streamlit provides.
"""
from __future__ import annotations

import streamlit as st
import config
from datetime import datetime


# ============================================================================
# DATA LOADERS - RAW SQL ONLY
# ============================================================================

@st.cache_data(show_spinner=False)
def load_keyword_health_matrix(db_version, start_date, end_date, campaigns, cities):
    """Comprehensive keyword health scoring with peer benchmarks."""
    try:
        con = config.connect_db()

        df = con.execute(f"""
            WITH kw_totals AS (
                SELECT
                    keyword,
                    product_name,
                    CASE
                        WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                        WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                        ELSE 'Other'
                    END AS match_label,
                    COUNT(DISTINCT metrics_date) AS days_active,
                    SUM(total_impressions) AS impressions,
                    SUM(total_clicks) AS clicks,
                    SUM(total_budget_burnt) AS spend,
                    SUM(total_conversions) AS conversions,
                    SUM(total_gmv) AS gmv
                FROM granular
                WHERE metrics_date BETWEEN ? AND ?
                  AND campaign_name IN ({','.join(['?'] * len(campaigns))})
                  AND city IN ({','.join(['?'] * len(cities))})
                  AND keyword IS NOT NULL
                  AND product_name IS NOT NULL
                GROUP BY keyword, product_name, match_type
            )
            SELECT
                keyword, product_name, match_label,
                days_active, impressions, clicks, spend, conversions, gmv,
                CASE WHEN spend > 0 THEN gmv / spend ELSE 0 END AS roi,
                CASE WHEN impressions > 0 THEN 100.0 * clicks / impressions ELSE 0 END AS ctr,
                CASE WHEN clicks > 0 THEN 100.0 * conversions / clicks ELSE 0 END AS click_conv_rate,
                CASE WHEN conversions > 0 THEN spend / conversions ELSE 0 END AS cpa
            FROM kw_totals
            WHERE spend > 0
            ORDER BY spend DESC
        """, [start_date, end_date, *campaigns, *cities]).fetchdf()

        con.close()
        return df
    except Exception as e:
        st.error(f"Error loading keyword health: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_underperforming_segments(db_version, start_date, end_date, campaigns, cities):
    """Find all underperforming keyword-product-city combinations."""
    try:
        con = config.connect_db()

        df = con.execute(f"""
            SELECT
                keyword,
                product_name,
                city,
                CASE
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                    ELSE 'Other'
                END AS match_type,
                SUM(total_budget_burnt) AS spend,
                SUM(total_clicks) AS clicks,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) AS gmv,
                CASE WHEN SUM(total_budget_burnt) > 0 THEN SUM(total_gmv) / SUM(total_budget_burnt) ELSE 0 END AS roi,
                CASE WHEN SUM(total_impressions) > 0 THEN 100.0 * SUM(total_clicks) / SUM(total_impressions) ELSE 0 END AS ctr
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
            GROUP BY keyword, product_name, city, match_type
            HAVING (SUM(total_budget_burnt) >= 100 AND SUM(total_gmv) / SUM(total_budget_burnt) < 1.0)
                OR (SUM(total_clicks) > 0 AND SUM(total_conversions) = 0)
            ORDER BY SUM(total_budget_burnt) DESC
        """, [start_date, end_date, *campaigns, *cities]).fetchdf()

        con.close()
        return df
    except Exception as e:
        st.error(f"Error loading underperformers: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_high_performers(db_version, start_date, end_date, campaigns, cities):
    """Keywords with ROI >= 2.0 worth scaling."""
    try:
        con = config.connect_db()

        df = con.execute(f"""
            SELECT
                keyword,
                product_name,
                CASE
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                    ELSE 'Other'
                END AS match_type,
                COUNT(DISTINCT metrics_date) AS days_active,
                SUM(total_budget_burnt) AS spend,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) AS gmv,
                SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND keyword IS NOT NULL
            GROUP BY keyword, product_name, match_type
            HAVING SUM(total_budget_burnt) >= 100
              AND SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) >= 2.0
              AND COUNT(DISTINCT metrics_date) >= 3
            ORDER BY gmv DESC
        """, [start_date, end_date, *campaigns, *cities]).fetchdf()

        con.close()
        return df
    except Exception as e:
        st.error(f"Error loading high performers: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_search_queries(db_version, start_date, end_date, campaigns):
    """All search queries with health status."""
    try:
        con = config.connect_db()

        df = con.execute(f"""
            SELECT
                search_query,
                keyword,
                product_name,
                CASE
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                    ELSE 'Other'
                END AS match_type,
                SUM(total_budget_burnt) AS spend,
                SUM(total_clicks) AS clicks,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) AS gmv,
                SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi
            FROM search_query
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
            GROUP BY search_query, keyword, product_name, match_type
            HAVING SUM(total_budget_burnt) > 0
            ORDER BY gmv DESC
        """, [start_date, end_date, *campaigns]).fetchdf()

        con.close()
        return df
    except Exception as e:
        st.error(f"Error loading queries: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_geographic_analysis(db_version, start_date, end_date, campaigns):
    """City performance analysis."""
    try:
        con = config.connect_db()

        df = con.execute(f"""
            SELECT
                city,
                SUM(total_budget_burnt) AS spend,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) AS gmv,
                SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
                COUNT(DISTINCT metrics_date) AS days_active
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
            GROUP BY city
            ORDER BY gmv DESC
        """, [start_date, end_date, *campaigns]).fetchdf()

        con.close()
        return df
    except Exception as e:
        st.error(f"Error loading geography: {str(e)}")
        return None


# ============================================================================
# UTILITIES
# ============================================================================

def format_inr(value):
    """Indian digit grouping."""
    if value is None or (isinstance(value, float) and value != value):  # NaN check
        return "-"

    value = float(value) if not isinstance(value, float) else value
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(round(value, 0))

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

    return f"{sign}₹{s}"


# ============================================================================
# RENDER
# ============================================================================

def render(db_version, start_date, end_date, campaigns, cities):
    """Main render function."""
    st.markdown("# 🤖 ANALYST ENGINE")
    st.markdown("Data-driven recommendations that replace human analysts.")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🚨 Critical Actions",
        "⚖️ Keyword Health",
        "🚀 Scale Winners",
        "🔍 Queries",
        "📍 Geography"
    ])

    # ========== TAB 1: CRITICAL ACTIONS ==========
    with tab1:
        st.subheader("Urgent Actions (Do Today)")

        underperf = load_underperforming_segments(db_version, start_date, end_date, campaigns, cities)

        if underperf is None or len(underperf) == 0:
            st.success("✅ No critical underperformers")
        else:
            # Zero conversions
            zero_conv = underperf[underperf['conversions'] == 0]
            if len(zero_conv) > 0:
                st.error(f"🚨 {len(zero_conv)} keywords with clicks but ZERO conversions")
                display_data = []
                for _, row in zero_conv.head(20).iterrows():
                    display_data.append({
                        'Keyword': row['keyword'],
                        'Product': row['product_name'],
                        'Match': row['match_type'],
                        'Spend': format_inr(row['spend']),
                        'Clicks': int(row['clicks']),
                        'Action': 'PAUSE'
                    })

                if display_data:
                    st.write("### Zero-Conversion Keywords")
                    for item in display_data[:10]:
                        st.write(f"- **{item['Keyword']}** ({item['Product']}, {item['Match']}) | "
                                f"Spend: {item['Spend']} | Clicks: {item['Clicks']} | "
                                f"**{item['Action']}**")

                total_waste = zero_conv['spend'].sum()
                st.error(f"Total wasted spend: {format_inr(total_waste)}")

            st.divider()

            # Negative ROI
            neg_roi = underperf[(underperf['roi'] < 1.0) & (underperf['conversions'] > 0)]
            if len(neg_roi) > 0:
                st.warning(f"⚠️  {len(neg_roi)} keywords losing money (ROI < 1.0)")
                display_data = []
                for _, row in neg_roi.head(20).iterrows():
                    loss = row['spend'] - row['gmv']
                    display_data.append({
                        'Keyword': row['keyword'],
                        'Product': row['product_name'],
                        'Spend': format_inr(row['spend']),
                        'GMV': format_inr(row['gmv']),
                        'ROI': f"{row['roi']:.2f}x",
                        'Loss': format_inr(loss)
                    })

                if display_data:
                    st.write("### Negative ROI Keywords")
                    for item in display_data[:10]:
                        st.write(f"- **{item['Keyword']}** ({item['Product']}) | "
                                f"Spend: {item['Spend']} | GMV: {item['GMV']} | "
                                f"ROI: {item['ROI']} | Loss: {item['Loss']}")

    # ========== TAB 2: KEYWORD HEALTH ==========
    with tab2:
        st.subheader("Keyword Health Matrix")

        kw_health = load_keyword_health_matrix(db_version, start_date, end_date, campaigns, cities)

        if kw_health is None or len(kw_health) == 0:
            st.info("No keyword data")
        else:
            display_data = []
            for _, row in kw_health.head(50).iterrows():
                display_data.append({
                    'Keyword': row['keyword'],
                    'Product': row['product_name'],
                    'Match': row['match_label'],
                    'Spend': format_inr(row['spend']),
                    'Conversions': int(row['conversions']) if row['conversions'] else 0,
                    'GMV': format_inr(row['gmv']),
                    'ROI': f"{row['roi']:.2f}x" if row['roi'] > 0 else "—",
                    'CTR': f"{row['ctr']:.2f}%" if row['ctr'] > 0 else "—"
                })

            st.write(f"**{len(kw_health)} keywords total**")

            for item in display_data[:30]:
                st.write(f"- {item['Keyword']} ({item['Product']}) | {item['Match']} | "
                        f"{item['Spend']} | Conv: {item['Conversions']} | "
                        f"GMV: {item['GMV']} | ROI: {item['ROI']}")

    # ========== TAB 3: SCALE WINNERS ==========
    with tab3:
        st.subheader("High-ROI Keywords to Scale")
        st.caption("Keywords with ROI >= 2.0 and consistent performance")

        winners = load_high_performers(db_version, start_date, end_date, campaigns, cities)

        if winners is None or len(winners) == 0:
            st.info("No high-ROI keywords found")
        else:
            display_data = []
            for _, row in winners.iterrows():
                monthly_potential = row['spend'] * (row['roi'] - 1) * 30
                display_data.append({
                    'Keyword': row['keyword'],
                    'Product': row['product_name'],
                    'Match': row['match_type'],
                    'Current Spend': format_inr(row['spend']),
                    'ROI': f"{row['roi']:.2f}x",
                    'Monthly Potential': format_inr(monthly_potential),
                    'Action': 'SCALE'
                })

            st.write(f"**{len(winners)} scaling opportunities**")

            for item in display_data[:20]:
                st.write(f"- **{item['Keyword']}** ({item['Product']}, {item['Match']}) | "
                        f"Current: {item['Current Spend']} | ROI: {item['ROI']} | "
                        f"Monthly Lift: {item['Monthly Potential']} | "
                        f"✅ {item['Action']}")

    # ========== TAB 4: QUERIES ==========
    with tab4:
        st.subheader("Search Query Analysis")

        queries = load_search_queries(db_version, start_date, end_date, campaigns)

        if queries is None or len(queries) == 0:
            st.info("No query data")
        else:
            # Converters
            converters = queries[queries['conversions'] > 0]
            wasters = queries[(queries['conversions'] == 0) & (queries['spend'] >= 50)]

            st.write(f"**Total queries:** {len(queries)} | "
                    f"**Converters:** {len(converters)} | "
                    f"**Wasters:** {len(wasters)}")

            st.markdown("#### 🎯 Top Converting Queries")
            display_data = []
            for _, row in converters.head(15).iterrows():
                display_data.append({
                    'Query': row['search_query'],
                    'Product': row['product_name'],
                    'Conversions': int(row['conversions']),
                    'GMV': format_inr(row['gmv']),
                    'ROI': f"{row['roi']:.2f}x" if row['roi'] > 0 else "—"
                })

            for item in display_data:
                st.write(f"- {item['Query']} ({item['Product']}) | "
                        f"{item['Conversions']} conv | {item['GMV']} | {item['ROI']}")

            st.divider()

            st.markdown("#### 💸 Waster Queries (Spend but No Conversions)")
            if len(wasters) > 0:
                total_waste = wasters['spend'].sum()
                st.error(f"Wasted: {format_inr(total_waste)} across {len(wasters)} queries")

                display_data = []
                for _, row in wasters.head(15).iterrows():
                    display_data.append({
                        'Query': row['search_query'],
                        'Product': row['product_name'],
                        'Spend': format_inr(row['spend']),
                        'Clicks': int(row['clicks'])
                    })

                for item in display_data:
                    st.write(f"- {item['Query']} ({item['Product']}) | "
                            f"Spend: {item['Spend']} | Clicks: {item['Clicks']} → No conversions")

    # ========== TAB 5: GEOGRAPHY ==========
    with tab5:
        st.subheader("Geographic Performance")

        geo = load_geographic_analysis(db_version, start_date, end_date, campaigns)

        if geo is None or len(geo) == 0:
            st.info("No geographic data")
        else:
            display_data = []
            for _, row in geo.iterrows():
                status = "🚀 EXPAND" if row['roi'] >= 3.0 else "📈 GROW" if row['roi'] >= 2.0 else "⚖️ STABLE" if row['roi'] >= 1.0 else "⚠️ TURNAROUND"

                display_data.append({
                    'City': row['city'],
                    'Spend': format_inr(row['spend']),
                    'GMV': format_inr(row['gmv']),
                    'ROI': f"{row['roi']:.2f}x",
                    'CTR': f"{row['ctr']:.2f}%",
                    'Status': status
                })

            for item in display_data[:25]:
                st.write(f"- **{item['City']}** | Spend: {item['Spend']} | "
                        f"GMV: {item['GMV']} | ROI: {item['ROI']} | "
                        f"CTR: {item['CTR']} | {item['Status']}")
