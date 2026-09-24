"""
Typo and Waste Keyword Detector
Identifies keywords consuming budget with poor ROI
Compares BROAD vs EXACT match performance
"""
from __future__ import annotations

import streamlit as st
import config


@st.cache_data(show_spinner=False)
def load_all_keywords(db_version, start_date, end_date, campaigns, cities):
    """Get all unique keywords."""
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
        st.error(f"Error: {str(e)}")
        return []


@st.cache_data(show_spinner=False)
def load_red_flag_keywords(db_version, start_date, end_date, campaigns, cities):
    """Keywords where Budget > GMV (negative ROI)."""
    try:
        con = config.connect_db()
        df = con.execute(f"""
            SELECT
                keyword,
                CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                     WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                     ELSE 'Other' END AS match_type,
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
        st.error(f"Error loading red flags: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_keyword_comparison(db_version, keyword, start_date, end_date, campaigns, cities):
    """BROAD vs EXACT for a keyword."""
    try:
        con = config.connect_db()
        df = con.execute(f"""
            SELECT
                keyword,
                CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad'
                     WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN 'Exact'
                     ELSE 'Other' END AS match_type,
                SUM(total_impressions) AS impressions,
                SUM(total_clicks) AS clicks,
                SUM(total_budget_burnt) AS budget_burnt,
                SUM(total_gmv) AS gmv,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi
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
        st.error(f"Error: {str(e)}")
        return None


def format_inr(value):
    """Format as Indian currency."""
    if value is None or (isinstance(value, float) and value != value):
        return "-"

    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(round(value, 0))

    s = str(whole)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        s = ",".join(parts) + "," + last3

    return f"{sign}Rs. {s}"


def render(db_version, start_date, end_date, campaigns, cities):
    """Main render function."""
    st.title("TYPO AND WASTE KEYWORD DETECTOR")
    st.markdown("Find keywords bleeding money. Compare BROAD vs EXACT performance.")

    st.divider()

    # RED FLAG KEYWORDS
    st.subheader("RED FLAG KEYWORDS (Budget > GMV)")
    st.caption("Keywords losing money - where Budget Spent exceeds GMV Generated")

    red_flags = load_red_flag_keywords(db_version, start_date, end_date, campaigns, cities)

    if red_flags is None or len(red_flags) == 0:
        st.success("No money-losing keywords found")
    else:
        total_loss = (red_flags['budget_burnt'] - red_flags['gmv']).sum()
        st.error(f"Found {len(red_flags)} RED FLAG instances | Total loss: {format_inr(total_loss)}")

        for _, row in red_flags.head(30).iterrows():
            loss = row['budget_burnt'] - row['gmv']
            roi_str = f"{row['roi']:.2f}x" if row['roi'] and row['roi'] > 0 else "NEGATIVE"

            st.write(
                f"KEYWORD: {row['keyword']} ({row['match_type']}) | "
                f"Budget: {format_inr(row['budget_burnt'])} | "
                f"GMV: {format_inr(row['gmv'])} | "
                f"Loss: {format_inr(loss)} | "
                f"Conv: {int(row['conversions'])} | "
                f"ROI: {roi_str}"
            )

    st.divider()

    # BROAD VS EXACT COMPARISON
    st.subheader("BROAD vs EXACT COMPARISON")
    st.caption("Select a keyword to see performance split by match type")

    all_kw = load_all_keywords(db_version, start_date, end_date, campaigns, cities)

    if len(all_kw) == 0:
        st.warning("No keywords found")
    else:
        selected_kw = st.selectbox("Select Keyword:", sorted(all_kw))

        if selected_kw:
            kw_data = load_keyword_comparison(db_version, selected_kw, start_date, end_date, campaigns, cities)

            if kw_data is not None and len(kw_data) > 0:
                st.markdown(f"### {selected_kw}")

                for _, row in kw_data.iterrows():
                    match = row['match_type']
                    roi_str = f"{row['roi']:.2f}x" if row['roi'] and row['roi'] > 0 else "NEGATIVE"
                    loss = row['budget_burnt'] - row['gmv']

                    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
                    with col1:
                        st.write(f"**{match}**")
                    with col2:
                        st.write(f"Impr: {int(row['impressions']):,}")
                    with col3:
                        st.write(f"Clicks: {int(row['clicks']):,}")
                    with col4:
                        st.write(f"Budget: {format_inr(row['budget_burnt'])}")
                    with col5:
                        st.write(f"GMV: {format_inr(row['gmv'])}")
                    with col6:
                        st.write(f"Loss: {format_inr(loss)}")
                    with col7:
                        st.write(f"ROI: {roi_str}")

                st.divider()

                # RECOMMENDATION
                if len(kw_data) == 2:
                    broad_row = kw_data[kw_data['match_type'] == 'Broad'].iloc[0] if len(kw_data[kw_data['match_type'] == 'Broad']) > 0 else None
                    exact_row = kw_data[kw_data['match_type'] == 'Exact'].iloc[0] if len(kw_data[kw_data['match_type'] == 'Exact']) > 0 else None

                    if broad_row is not None and exact_row is not None:
                        st.markdown("#### RECOMMENDATION")

                        if broad_row['roi'] and exact_row['roi']:
                            if exact_row['roi'] > broad_row['roi']:
                                broad_loss = broad_row['budget_burnt'] - broad_row['gmv']
                                st.warning(
                                    f"EXACT is {(exact_row['roi']/broad_row['roi']):.1f}x better ROI than BROAD. "
                                    f"Shift budget from BROAD to EXACT. "
                                    f"BROAD is wasting: {format_inr(broad_loss)}"
                                )
                            else:
                                st.info(f"BROAD is performing better than EXACT for this keyword")
