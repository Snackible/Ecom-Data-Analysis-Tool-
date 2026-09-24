"""
TYPO & WASTE KEYWORD DETECTOR

Identifies keywords consuming disproportionate budget with poor ROI.
Compares BROAD vs EXACT match for the same keyword to spot where the waste is.

Flags: Budget Burnt > GMV (negative ROI = RED FLAG)
"""
from __future__ import annotations

import streamlit as st
import config


@st.cache_data(show_spinner=False)
def load_all_keywords(db_version, start_date, end_date, campaigns, cities):
    """Get every keyword + match type combination."""
    try:
        con = config.connect_db()

        df = con.execute(f"""
            SELECT DISTINCT keyword
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND keyword IS NOT NULL
            ORDER BY keyword
        """, [start_date, end_date, *campaigns, *cities]).fetchdf()

        con.close()
        return df['keyword'].tolist() if len(df) > 0 else []
    except Exception as e:
        st.error(f"Error loading keywords: {str(e)}")
        return []


@st.cache_data(show_spinner=False)
def load_all_campaigns(db_version, start_date, end_date):
    """Get all campaigns."""
    try:
        con = config.connect_db()

        df = con.execute(f"""
            SELECT DISTINCT campaign_name
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
            ORDER BY campaign_name
        """, [start_date, end_date]).fetchdf()

        con.close()
        return df['campaign_name'].tolist() if len(df) > 0 else []
    except Exception as e:
        st.error(f"Error loading campaigns: {str(e)}")
        return []


@st.cache_data(show_spinner=False)
def load_keyword_broad_exact_comparison(db_version, keyword, start_date, end_date, campaigns, cities):
    """Compare BROAD vs EXACT for a single keyword."""
    try:
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
                SUM(total_budget_burnt) AS budget_burnt,
                SUM(total_gmv) AS gmv,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
                100.0 * SUM(total_conversions) / NULLIF(SUM(total_clicks), 0) AS conv_rate,
                SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa,
                CASE
                    WHEN SUM(total_budget_burnt) > SUM(total_gmv) THEN '🚨 RED'
                    WHEN SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) >= 1.5 THEN '🟢 GREEN'
                    ELSE '🟡 YELLOW'
                END AS health_flag
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND keyword = ?
              AND match_type IN ('KEYWORD_MATCH_TYPE_BROAD', 'KEYWORD_MATCH_TYPE_EXACT')
            GROUP BY keyword, match_type
        """, [start_date, end_date, *campaigns, *cities, keyword]).fetchdf()

        con.close()
        return df
    except Exception as e:
        st.error(f"Error loading keyword data: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_keyword_by_campaign(db_version, keyword, campaign, start_date, end_date, cities):
    """Get keyword data for a specific campaign."""
    try:
        con = config.connect_db()

        df = con.execute(f"""
            SELECT
                keyword,
                campaign_name,
                CASE
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                    WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                    ELSE 'Other'
                END AS match_type,
                SUM(total_impressions) AS impressions,
                SUM(total_clicks) AS clicks,
                SUM(total_budget_burnt) AS budget_burnt,
                SUM(total_gmv) AS gmv,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                CASE
                    WHEN SUM(total_budget_burnt) > SUM(total_gmv) THEN '🚨 RED'
                    WHEN SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) >= 1.5 THEN '🟢 GREEN'
                    ELSE '🟡 YELLOW'
                END AS flag
            FROM granular
            WHERE keyword = ?
              AND campaign_name = ?
              AND metrics_date BETWEEN ? AND ?
              AND city IN ({','.join(['?'] * len(cities))})
              AND match_type IN ('KEYWORD_MATCH_TYPE_BROAD', 'KEYWORD_MATCH_TYPE_EXACT')
            GROUP BY keyword, campaign_name, match_type
        """, [keyword, campaign, start_date, end_date, *cities]).fetchdf()

        con.close()
        return df
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_all_red_flag_keywords(db_version, start_date, end_date, campaigns, cities):
    """Load ALL keywords where Budget > GMV (money losers)."""
    try:
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
                SUM(total_budget_burnt) AS budget_burnt,
                SUM(total_gmv) AS gmv,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND keyword IS NOT NULL
            GROUP BY keyword, match_type
            HAVING SUM(total_budget_burnt) > SUM(total_gmv)
            ORDER BY SUM(total_budget_burnt) DESC
        """, [start_date, end_date, *campaigns, *cities]).fetchdf()

        con.close()
        return df
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return None


def format_inr(value):
    """Indian digit grouping."""
    if value is None or (isinstance(value, float) and value != value):
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


def render(db_version, start_date, end_date, campaigns, cities):
    """Main render."""
    st.markdown("# 🔍 TYPO & WASTE KEYWORD DETECTOR")
    st.markdown(
        "Find keywords bleeding money. Compare BROAD vs EXACT match performance. "
        "Flag red-flag keywords where Budget Burnt > GMV."
    )

    st.divider()

    # ========== SECTION 1: RED FLAG KEYWORDS OVERVIEW ==========
    st.subheader("🚨 RED FLAG KEYWORDS (Budget > GMV)")
    st.caption("Keywords losing money - Budget spent exceeds GMV generated")

    red_flags = load_all_red_flag_keywords(db_version, start_date, end_date, campaigns, cities)

    if red_flags is None or len(red_flags) == 0:
        st.success("✅ No money-losing keywords found!")
    else:
        total_loss = (red_flags['budget_burnt'] - red_flags['gmv']).sum()
        st.error(f"⚠️ **{len(red_flags)} RED FLAG instances** | "
                f"Total loss: {format_inr(total_loss)}")

        # Display red flags
        for _, row in red_flags.head(30).iterrows():
            loss = row['budget_burnt'] - row['gmv']
            roi_display = f"{row['roi']:.2f}x" if row['roi'] > 0 else "NEGATIVE"
            st.write(
                f"🚨 **{row['keyword']}** ({row['match_type']}) | "
                f"Budget: {format_inr(row['budget_burnt'])} | "
                f"GMV: {format_inr(row['gmv'])} | "
                f"Loss: {format_inr(loss)} | "
                f"Conversions: {int(row['conversions'])} | "
                f"ROI: {roi_display}"
            )

    st.divider()

    # ========== SECTION 2: KEYWORD PICKER & COMPARISON ==========
    st.subheader("📊 BROAD vs EXACT Comparison")
    st.caption("Select a keyword to see performance split by match type")

    col1, col2 = st.columns([2, 1])

    with col1:
        all_keywords = load_all_keywords(db_version, start_date, end_date, campaigns, cities)
        if len(all_keywords) == 0:
            st.warning("No keywords found")
        else:
            selected_keyword = st.selectbox(
                "Select keyword:",
                sorted(all_keywords),
                key="keyword_picker"
            )

            if selected_keyword:
                kw_data = load_keyword_broad_exact_comparison(
                    db_version, selected_keyword, start_date, end_date, campaigns, cities
                )

                if kw_data is not None and len(kw_data) > 0:
                    st.markdown(f"### {selected_keyword}")

                    # Display as rows
                    for _, row in kw_data.iterrows():
                        flag = row['health_flag']
                        match = row['match_type']

                        col_a, col_b, col_c, col_d, col_e, col_f, col_g, col_h = st.columns(8)

                        with col_a:
                            st.write(f"**{flag}**")
                        with col_b:
                            st.write(f"**{match}**")
                        with col_c:
                            st.write(f"Impr: {int(row['impressions']):,}")
                        with col_d:
                            st.write(f"Clicks: {int(row['clicks']):,}")
                        with col_e:
                            st.write(f"Budget: {format_inr(row['budget_burnt'])}")
                        with col_f:
                            st.write(f"GMV: {format_inr(row['gmv'])}")
                        with col_g:
                            st.write(f"ROI: {row['roi']:.2f}x" if row['roi'] > 0 else "Negative")
                        with col_h:
                            st.write(f"Conv: {int(row['conversions'])}")

                    st.divider()

                    # Detailed comparison
                    if len(kw_data) == 2:
                        broad = kw_data[kw_data['match_type'] == 'Broad'].iloc[0] if len(kw_data[kw_data['match_type'] == 'Broad']) > 0 else None
                        exact = kw_data[kw_data['match_type'] == 'Exact'].iloc[0] if len(kw_data[kw_data['match_type'] == 'Exact']) > 0 else None

                        if broad is not None and exact is not None:
                            st.markdown("#### BROAD vs EXACT Analysis")

                            broad_loss = broad['budget_burnt'] - broad['gmv']
                            exact_loss = exact['budget_burnt'] - exact['gmv'] if exact['budget_burnt'] > 0 else 0

                            if broad['roi'] > exact['roi']:
                                st.success(
                                    f"✅ **BROAD is better** for '{selected_keyword}'\n\n"
                                    f"Broad ROI: {broad['roi']:.2f}x | Exact ROI: {exact['roi']:.2f}x\n\n"
                                    f"**Action:** Keep BROAD, reduce EXACT"
                                )
                            elif exact['roi'] > broad['roi']:
                                st.warning(
                                    f"⚠️ **EXACT is better** for '{selected_keyword}'\n\n"
                                    f"Exact ROI: {exact['roi']:.2f}x | Broad ROI: {broad['roi']:.2f}x\n\n"
                                    f"**Action:** Shift budget from BROAD to EXACT"
                                )
                                if broad['budget_burnt'] > 0:
                                    st.error(
                                        f"BROAD is wasting: {format_inr(broad_loss)} "
                                        f"(Budget {format_inr(broad['budget_burnt'])} > GMV {format_inr(broad['gmv'])})"
                                    )
                else:
                    st.info(f"No data for '{selected_keyword}'")

    with col2:
        st.markdown("#### Metrics Guide")
        st.write(
            "**🚨 RED** = Budget > GMV (losing money)\n\n"
            "**🟡 YELLOW** = 1.0x ≤ ROI < 1.5x (marginal)\n\n"
            "**🟢 GREEN** = ROI ≥ 1.5x (profitable)"
        )

    st.divider()

    # ========== SECTION 3: CAMPAIGN FILTER ==========
    st.subheader("🏢 Campaign-Level Keyword Analysis")
    st.caption("Drill into how a keyword performs in specific campaigns")

    col1, col2 = st.columns(2)

    with col1:
        campaign_list = load_all_campaigns(db_version, start_date, end_date)
        if len(campaign_list) > 0:
            selected_campaign = st.selectbox("Campaign:", sorted(campaign_list), key="campaign_picker")
        else:
            st.warning("No campaigns found")
            selected_campaign = None

    with col2:
        kw_list = load_all_keywords(db_version, start_date, end_date, campaigns, cities)
        if len(kw_list) > 0:
            selected_kw_campaign = st.selectbox("Keyword:", sorted(kw_list), key="kw_campaign_picker")
        else:
            st.warning("No keywords found")
            selected_kw_campaign = None

    if selected_campaign and selected_kw_campaign:
        campaign_kw_data = load_keyword_by_campaign(
            db_version, selected_kw_campaign, selected_campaign, start_date, end_date, cities
        )

        if campaign_kw_data is not None and len(campaign_kw_data) > 0:
            st.markdown(f"### {selected_kw_campaign} in {selected_campaign}")

            for _, row in campaign_kw_data.iterrows():
                flag = row['flag']
                match = row['match_type']
                loss = row['budget_burnt'] - row['gmv']

                st.write(
                    f"{flag} **{match}** | "
                    f"Impressions: {int(row['impressions']):,} | "
                    f"Budget: {format_inr(row['budget_burnt'])} | "
                    f"GMV: {format_inr(row['gmv'])} | "
                    f"Loss: {format_inr(loss)} | "
                    f"ROI: {row['roi']:.2f}x" if row['roi'] > 0 else "Negative"
                )
        else:
            st.info("No data for this campaign-keyword combination")
