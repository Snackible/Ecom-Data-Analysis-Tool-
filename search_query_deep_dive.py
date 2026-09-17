"""
Search Query Deep Dive - a dedicated view for keyword/match-type analysis
and pause/scale recommendations. Rendered as an alternate page from
dashboard.py via a sidebar nav radio; shares the same sidebar filters
(date range, campaigns, cities).

Everything here is derived from the granular table (per campaign x city x
keyword x day rollup - has the KEYWORD + MATCH_TYPE + spend/GMV columns
needed to grade a keyword) and the search_query table (actual search
terms shoppers typed, needed for broad-match waste analysis).
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import config

# The raw MATCH_TYPE values in the granular/search_query tables are the
# API-level enum strings; the UI shows the friendly names instead. INVALID
# rows are non-keyword targeting (categories, products) that the export
# lumps into the keyword table with a placeholder match_type - not a real
# match type to grade, so most sections filter it out.
MATCH_TYPE_LABEL = {
    "KEYWORD_MATCH_TYPE_BROAD": "Broad",
    "KEYWORD_MATCH_TYPE_EXACT": "Exact",
    "KEYWORD_MATCH_TYPE_INVALID": "Other/None",
}

# Thresholds for "meaningful spend" - a keyword that spent Rs.10 total
# isn't a signal, positive or negative. Kept as module constants so the
# same numbers show up in the recommendation copy the user reads.
WINNER_ROI = 3.0        # ROI at/above this = "scale it"
LOSER_ROI = 1.0         # ROI below this with real spend = "pause it"
MEANINGFUL_SPEND = 100  # rupees - min spend before a keyword's ROI is worth judging


# ---------------------------------------------------------------------------
# Data loaders (cached per DB version + filter tuple so re-renders are cheap)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _match_type_overview(db_version, start_date, end_date, campaigns, cities):
    """One row per match type with all the top-line metrics."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT
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
            100.0 * SUM(total_conversions) / NULLIF(SUM(total_a2c), 0) AS conv_rate,
            SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
        GROUP BY match_type
        ORDER BY spend DESC
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()
    con.close()
    df["match_type_label"] = df["match_type"].map(MATCH_TYPE_LABEL).fillna(df["match_type"])
    return df


@st.cache_data(show_spinner=False)
def _keyword_grades(db_version, start_date, end_date, campaigns, cities):
    """One row per (keyword, match_type) with metrics + a grade bucket for
    the recommendation tables. Non-keyword rows (match_type INVALID or
    keyword IS NULL) are excluded - they can't be graded as a keyword."""
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
            SUM(total_budget_burnt) / NULLIF(SUM(total_conversions), 0) AS cpa
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
          AND keyword IS NOT NULL
          AND match_type IN ('KEYWORD_MATCH_TYPE_BROAD', 'KEYWORD_MATCH_TYPE_EXACT')
        GROUP BY keyword, match_type
        HAVING SUM(total_impressions) > 0
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()
    con.close()
    df["match"] = df["match_type"].map(MATCH_TYPE_LABEL)
    return df


@st.cache_data(show_spinner=False)
def _head_to_head(db_version, start_date, end_date, campaigns, cities):
    """Keywords that were run in BOTH broad and exact - one row per
    keyword with the two match types pivoted side by side. This is what
    powers the "switch match type" recommendations."""
    con = config.connect_db()
    df = con.execute(f"""
        WITH per_kw AS (
            SELECT keyword, match_type,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_gmv) AS gmv,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND keyword IS NOT NULL
              AND match_type IN ('KEYWORD_MATCH_TYPE_BROAD', 'KEYWORD_MATCH_TYPE_EXACT')
            GROUP BY keyword, match_type
            HAVING SUM(total_impressions) > 0
        )
        SELECT
            keyword,
            MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN spend END) AS broad_spend,
            MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN gmv END) AS broad_gmv,
            MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN roi END) AS broad_roi,
            MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN conversions END) AS broad_conv,
            MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN spend END) AS exact_spend,
            MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN gmv END) AS exact_gmv,
            MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN roi END) AS exact_roi,
            MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN conversions END) AS exact_conv
        FROM per_kw
        GROUP BY keyword
        HAVING broad_spend IS NOT NULL AND exact_spend IS NOT NULL
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _search_query_overview(db_version, start_date, end_date, campaigns):
    """Top-level search-query domain KPIs. Every value comes from a
    single SQL rollup over the real search_query rows in the window -
    no fabricated numbers, no filled-in placeholders."""
    con = config.connect_db()
    row = con.execute(f"""
        SELECT
            COUNT(DISTINCT search_query) AS unique_queries,
            COUNT(DISTINCT CASE WHEN total_impressions > 0 THEN search_query END) AS queries_with_impressions,
            COUNT(DISTINCT CASE WHEN total_clicks > 0 THEN search_query END) AS queries_with_clicks,
            COUNT(DISTINCT CASE WHEN total_conversions > 0 THEN search_query END) AS queries_with_conv,
            SUM(total_impressions) AS impressions,
            SUM(total_clicks) AS clicks,
            SUM(total_budget_burnt) AS spend,
            SUM(total_conversions) AS conversions,
            SUM(total_gmv) AS gmv,
            SUM(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN total_budget_burnt END) AS broad_spend,
            SUM(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN total_budget_burnt END) AS exact_spend,
            SUM(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN total_gmv END) AS broad_gmv,
            SUM(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN total_gmv END) AS exact_gmv
        FROM search_query
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND search_query IS NOT NULL
    """, [start_date, end_date, *campaigns]).fetchone()
    con.close()
    return dict(
        unique_queries=row[0] or 0,
        queries_with_impressions=row[1] or 0,
        queries_with_clicks=row[2] or 0,
        queries_with_conv=row[3] or 0,
        impressions=row[4] or 0, clicks=row[5] or 0,
        spend=row[6] or 0, conversions=row[7] or 0, gmv=row[8] or 0,
        broad_spend=row[9] or 0, exact_spend=row[10] or 0,
        broad_gmv=row[11] or 0, exact_gmv=row[12] or 0,
    )


@st.cache_data(show_spinner=False)
def _query_length_analysis(db_version, start_date, end_date, campaigns):
    """Bucket every search query by word count. Long-tail (4+ words)
    queries usually reflect stronger purchase intent than head terms,
    so comparing ROI/conv-rate across buckets exposes whether the
    broader-intent traffic is actually converting."""
    con = config.connect_db()
    df = con.execute(f"""
        WITH per_q AS (
            SELECT search_query,
                   -- word count: split on whitespace and count non-empty parts
                   len(str_split_regex(trim(search_query), '\\s+')) AS words,
                   SUM(total_impressions) AS impressions,
                   SUM(total_clicks) AS clicks,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) AS gmv
            FROM search_query
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND search_query IS NOT NULL AND trim(search_query) <> ''
            GROUP BY search_query
        )
        SELECT
            CASE
              WHEN words = 1 THEN '1 word (head term)'
              WHEN words = 2 THEN '2 words'
              WHEN words = 3 THEN '3 words'
              WHEN words BETWEEN 4 AND 5 THEN '4-5 words (long-tail)'
              ELSE '6+ words (deep long-tail)'
            END AS bucket,
            CASE
              WHEN words = 1 THEN 1 WHEN words = 2 THEN 2 WHEN words = 3 THEN 3
              WHEN words BETWEEN 4 AND 5 THEN 4 ELSE 5 END AS sort_key,
            COUNT(*) AS unique_queries,
            SUM(impressions) AS impressions,
            SUM(clicks) AS clicks,
            SUM(spend) AS spend,
            SUM(conversions) AS conversions,
            SUM(gmv) AS gmv,
            SUM(gmv) / NULLIF(SUM(spend), 0) AS roi,
            100.0 * SUM(clicks) / NULLIF(SUM(impressions), 0) AS ctr,
            100.0 * SUM(conversions) / NULLIF(SUM(clicks), 0) AS click_conv_rate
        FROM per_q
        GROUP BY bucket, sort_key
        ORDER BY sort_key
    """, [start_date, end_date, *campaigns]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _intent_gap_queries(db_version, start_date, end_date, campaigns, min_spend):
    """Broad-match queries where the actual search string shares few
    words with the bid keyword - a signal that broad match expanded to
    unrelated territory. Overlap uses simple word-set intersection over
    union (Jaccard), which stays comparable across query lengths.

    Only broad-match rows are considered - exact match by definition
    has query == keyword, so intent gap doesn't apply."""
    con = config.connect_db()
    df = con.execute(f"""
        WITH per_q AS (
            SELECT search_query, keyword,
                   SUM(total_impressions) AS impressions,
                   SUM(total_clicks) AS clicks,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) AS gmv
            FROM search_query
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND search_query IS NOT NULL AND keyword IS NOT NULL
              AND match_type = 'KEYWORD_MATCH_TYPE_BROAD'
              AND trim(search_query) <> '' AND trim(keyword) <> ''
            GROUP BY search_query, keyword
            HAVING SUM(total_budget_burnt) >= {min_spend}
        )
        SELECT search_query, keyword, impressions, clicks, spend, conversions, gmv,
               gmv / NULLIF(spend, 0) AS roi,
               -- Jaccard similarity on the two lowercase word sets
               (SELECT COUNT(*) FROM (
                    SELECT unnest(str_split_regex(lower(trim(search_query)), '\\s+'))
                    INTERSECT
                    SELECT unnest(str_split_regex(lower(trim(keyword)), '\\s+'))
               )) * 1.0 /
               NULLIF((SELECT COUNT(*) FROM (
                    SELECT unnest(str_split_regex(lower(trim(search_query)), '\\s+'))
                    UNION
                    SELECT unnest(str_split_regex(lower(trim(keyword)), '\\s+'))
               )), 0) AS word_overlap
        FROM per_q
        ORDER BY word_overlap ASC, spend DESC
        LIMIT 30
    """, [start_date, end_date, *campaigns]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _query_quadrants(db_version, start_date, end_date, campaigns, min_impressions):
    """Every query rolled up with impressions + ROI, so the render layer
    can plot the volume x value scatter and classify each into one of
    four quadrants against the median lines."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT search_query,
               SUM(total_impressions) AS impressions,
               SUM(total_clicks) AS clicks,
               SUM(total_budget_burnt) AS spend,
               SUM(total_conversions) AS conversions,
               SUM(total_gmv) AS gmv,
               SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi
        FROM search_query
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND search_query IS NOT NULL
        GROUP BY search_query
        HAVING SUM(total_impressions) >= {min_impressions}
           AND SUM(total_budget_burnt) > 0
    """, [start_date, end_date, *campaigns]).fetchdf()
    con.close()
    return df


@st.cache_data(show_spinner=False)
def _search_query_intelligence(db_version, start_date, end_date, campaigns):
    """Top and bottom actual search queries (what shoppers typed).
    search_query has no per-row city column (it has a city_count aggregate
    instead), so city filtering doesn't apply here - flagged in the UI."""
    con = config.connect_db()
    top = con.execute(f"""
        SELECT search_query, keyword, match_type,
               SUM(total_impressions) AS impressions,
               SUM(total_clicks) AS clicks,
               SUM(total_budget_burnt) AS spend,
               SUM(total_conversions) AS conversions,
               SUM(total_gmv) AS gmv,
               SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi
        FROM search_query
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND search_query IS NOT NULL
        GROUP BY search_query, keyword, match_type
        HAVING SUM(total_budget_burnt) > 0
        ORDER BY gmv DESC
        LIMIT 30
    """, [start_date, end_date, *campaigns]).fetchdf()
    waste = con.execute(f"""
        SELECT search_query, keyword, match_type,
               SUM(total_impressions) AS impressions,
               SUM(total_clicks) AS clicks,
               SUM(total_budget_burnt) AS spend,
               SUM(total_conversions) AS conversions,
               SUM(total_gmv) AS gmv
        FROM search_query
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND search_query IS NOT NULL
        GROUP BY search_query, keyword, match_type
        HAVING SUM(total_budget_burnt) >= {MEANINGFUL_SPEND}
           AND SUM(total_conversions) = 0
        ORDER BY spend DESC
        LIMIT 30
    """, [start_date, end_date, *campaigns]).fetchdf()
    con.close()
    for df in (top, waste):
        df["match"] = df["match_type"].map(MATCH_TYPE_LABEL).fillna(df["match_type"])
    return top, waste


@st.cache_data(show_spinner=False)
def _product_keyword_matrix(db_version, start_date, end_date, campaigns, cities):
    """Top product x keyword combinations - shows which pairings pay off
    (great creative alignment) and which don't (mismatch to hunt down)."""
    con = config.connect_db()
    df = con.execute(f"""
        SELECT product_name, keyword, match_type,
               SUM(total_budget_burnt) AS spend,
               SUM(total_conversions) AS conversions,
               SUM(total_gmv) AS gmv,
               SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi
        FROM granular
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND city IN ({','.join(['?'] * len(cities))})
          AND product_name IS NOT NULL
          AND keyword IS NOT NULL
        GROUP BY product_name, keyword, match_type
        HAVING SUM(total_budget_burnt) >= {MEANINGFUL_SPEND}
    """, [start_date, end_date, *campaigns, *cities]).fetchdf()
    con.close()
    df["match"] = df["match_type"].map(MATCH_TYPE_LABEL).fillna(df["match_type"])
    return df


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(db_version, start_date, end_date, selected_campaigns, selected_cities,
           format_inr, format_df_inr, accents):
    """Render the whole Search Query Deep Dive page. `accents` is passed
    in so KPI cards and callouts pick up the same brand colors as the
    main dashboard."""
    st.title("🔍 Search Query Deep Dive")
    st.caption(
        "Match type performance (Broad vs Exact), keyword winners to scale, "
        "losers to pause, and search query intelligence. Sidebar filters apply."
    )

    # === Classification methodology (always visible so nothing is a black box) ==
    with st.expander("📖 Classification methodology — thresholds, formulas & what each label means", expanded=False):
        st.markdown(f"""
**All numbers on this page come directly from the DuckDB `granular` and `search_query` tables — nothing is estimated, sampled, or padded.** Definitions used everywhere below:

**Core formulas (Instamart's own column names in parentheses):**
- **ROI** = GMV ÷ Spend, where **Spend = `total_budget_burnt`** (actual money burnt, not the campaign budget cap `total_budget`) and **GMV = `total_gmv`** (attributed sales value).
- **CTR** = `total_clicks` ÷ `total_impressions` × 100
- **A2C rate** = `total_a2c` ÷ `total_clicks` × 100 (add-to-cart rate on clicks)
- **Conversion rate (clicks→orders)** = `total_conversions` ÷ `total_clicks` × 100
- **CPA** = `total_budget_burnt` ÷ `total_conversions` (cost per acquired order)

**Meaningful-spend floor: ₹{MEANINGFUL_SPEND}.** Below this, ROI swings wildly on tiny sample sizes (a ₹5 spend and one ₹50 sale = 10x ROI but is not a real signal). Every recommendation table filters to ≥ this floor unless the user changes the selector for that section.

**Grade buckets applied per (keyword × match type) combination:**
- 🚀 **Winner:** ROI ≥ **{WINNER_ROI:.0f}x** with spend ≥ ₹{MEANINGFUL_SPEND}. Action: scale (raise bid or budget).
- 🛑 **Loser:** ROI < **{LOSER_ROI:.0f}x** with spend ≥ ₹{MEANINGFUL_SPEND}. Action: pause, reduce bid, or switch broad → exact.
- 🕳️ **Neg-keyword candidate:** spend ≥ threshold, clicks > 0, conversions = 0. Action: negative-keyword or pause.
- ⚔️ **Match-type head-to-head:** same keyword ran in both BROAD and EXACT; winner decided by ROI, tie if within 15% of each other.

**Match type values in the raw data:**
- `KEYWORD_MATCH_TYPE_BROAD` → displayed as **Broad** (query auto-expanded to related terms — higher volume, lower intent).
- `KEYWORD_MATCH_TYPE_EXACT` → displayed as **Exact** (query must match keyword — lower volume, higher intent).
- `KEYWORD_MATCH_TYPE_INVALID` → displayed as **Other/None** (non-keyword targeting like category or product ads; excluded from keyword grading because there's no keyword to grade).

**What "search query" actually means:** the literal text a shopper typed into the Instamart search bar, as recorded in the Search Query Report. Distinct from **keyword**, which is what *you* bid on. On broad match these two can differ significantly — that gap is the "intent-gap" analysis below.

**Filter scope:** every section respects the sidebar's date range and campaign filter. City filter applies to the `granular`-derived sections (keyword grades, product×keyword). It does NOT apply to `search_query`-derived sections because the search query report ships with an aggregate `city_count` per row instead of one row per city — filtering by a subset of cities would double-count.
""")

    campaigns = tuple(selected_campaigns)
    cities = tuple(selected_cities)

    # === Match type overview ==============================================
    st.subheader("Match type performance — Broad vs Exact")
    st.caption(
        f"Aggregated over the current filter window ({start_date} → {end_date}). "
        "'Other/None' covers non-keyword-targeted rows (category/product targeting)."
    )
    mt = _match_type_overview(db_version, start_date, end_date, campaigns, cities)
    if mt.empty:
        st.warning("No data in the current filters.")
        return

    # KPI card row - one card per match type, color-coded
    color_for = {"Broad": "blue", "Exact": "green", "Other/None": "amber"}
    cols = st.columns(len(mt))
    for col, (_, row) in zip(cols, mt.iterrows()):
        label = row["match_type_label"]
        color = accents[color_for.get(label, "green")]
        roi = row["roi"] if pd.notna(row["roi"]) else 0
        with col:
            st.markdown(
                f'<div style="background:{color["bg"]};border:1px solid {color["border"]};'
                f'border-left:4px solid {color["fg"]};border-radius:12px;padding:14px 16px">'
                f'<div style="color:{color["fg"]};font-weight:600;font-size:0.95rem;'
                f'letter-spacing:0.5px">{label.upper()}</div>'
                f'<div style="font-size:1.7rem;font-weight:700;margin:4px 0">'
                f'{roi:.2f}x ROI</div>'
                f'<div style="font-size:0.85rem;opacity:0.85;line-height:1.5">'
                f'GMV ₹{format_inr(row["gmv"] or 0)}<br>'
                f'Spend ₹{format_inr(row["spend"] or 0)}<br>'
                f'CTR {row["ctr"] or 0:.2f}% · A2C {row["a2c_rate"] or 0:.1f}%<br>'
                f'{int(row["conversions"] or 0):,} conv · '
                f'CPA ₹{format_inr(row["cpa"] or 0, 0)}</div></div>',
                unsafe_allow_html=True,
            )

    # Spend vs GMV share chart - shows efficiency skew at a glance
    real = mt[mt["match_type_label"].isin(["Broad", "Exact"])].copy()
    if len(real) == 2:
        total_spend = real["spend"].sum()
        total_gmv = real["gmv"].sum()
        share = pd.DataFrame({
            "Match type": list(real["match_type_label"]) * 2,
            "Metric": ["Share of spend"] * 2 + ["Share of GMV"] * 2,
            "Share": (list(real["spend"] / total_spend * 100) +
                      list(real["gmv"] / total_gmv * 100)),
        })
        chart = alt.Chart(share).mark_bar().encode(
            y=alt.Y("Metric:N", title=None),
            x=alt.X("Share:Q", stack="normalize", axis=alt.Axis(format="%", title=None)),
            color=alt.Color("Match type:N",
                            scale=alt.Scale(domain=["Broad", "Exact"],
                                            range=[accents["blue"]["fg"], accents["green"]["fg"]])),
            tooltip=["Match type", "Metric", alt.Tooltip("Share:Q", format=".1f")],
        ).properties(height=110)
        st.altair_chart(chart, width='stretch')
        broad_gmv_share = real[real["match_type_label"] == "Broad"]["gmv"].iloc[0] / total_gmv * 100
        broad_spend_share = real[real["match_type_label"] == "Broad"]["spend"].iloc[0] / total_spend * 100
        skew = broad_gmv_share - broad_spend_share
        if skew > 5:
            st.success(f"📈 Broad is pulling {skew:+.1f}pp more GMV share than spend share — "
                       "your broad match is over-performing its cost.")
        elif skew < -5:
            st.warning(f"📉 Broad's GMV share lags spend share by {-skew:.1f}pp — "
                       "money is going to broad but conversions are coming from exact. "
                       "Consider shifting budget to exact.")

    st.divider()

    # === Winners (scale these) ============================================
    st.subheader(f"🚀 Winners to scale (ROI ≥ {WINNER_ROI:.0f}x, meaningful spend)")
    st.caption(
        f"Keywords with ≥ ₹{MEANINGFUL_SPEND} spend and ROI at or above {WINNER_ROI:.0f}x. "
        "These are earning more than they cost — increase budget or bid to capture more of this demand."
    )
    kw = _keyword_grades(db_version, start_date, end_date, campaigns, cities)
    winners = (kw[(kw["spend"] >= MEANINGFUL_SPEND) & (kw["roi"] >= WINNER_ROI)]
               .sort_values("gmv", ascending=False)
               .head(30)
               .copy())
    if winners.empty:
        st.info("No keywords hit the winner threshold in the current filters. Widen the date range "
                "or lower thresholds by editing the constants at the top of search_query_deep_dive.py.")
    else:
        winners["Recommendation"] = "Scale — increase budget/bid"
        display = winners[["keyword", "match", "spend", "gmv", "roi", "conversions", "ctr", "cpa",
                           "Recommendation"]].rename(columns={
            "keyword": "Keyword", "match": "Match", "spend": "Spend",
            "gmv": "GMV", "roi": "ROI", "conversions": "Conv",
            "ctr": "CTR %", "cpa": "CPA",
        })
        display["ROI"] = display["ROI"].round(2)
        display["CTR %"] = display["CTR %"].round(2)
        st.dataframe(format_df_inr(display, ["Spend", "GMV", "CPA"]),
                     width='stretch', hide_index=True)

    st.divider()

    # === Losers (pause these) =============================================
    st.subheader(f"🛑 Losers to pause (ROI < {LOSER_ROI:.0f}x, meaningful spend)")
    st.caption(
        f"Keywords with ≥ ₹{MEANINGFUL_SPEND} spend and ROI below {LOSER_ROI:.0f}x. "
        "You're spending more than you're getting back — pause, reduce bid, or move to a tighter match."
    )
    losers = (kw[(kw["spend"] >= MEANINGFUL_SPEND) & (kw["roi"] < LOSER_ROI)]
              .sort_values("spend", ascending=False)
              .head(30)
              .copy())
    if losers.empty:
        st.success("🎉 No underperforming keywords with meaningful spend in this window.")
    else:
        losers["Recommendation"] = losers.apply(
            lambda r: ("Pause — no conversions at all" if r["conversions"] == 0
                       else ("Try switching to exact match" if r["match"] == "Broad"
                             else "Reduce bid or pause")),
            axis=1,
        )
        display = losers[["keyword", "match", "spend", "gmv", "roi", "conversions", "clicks",
                          "Recommendation"]].rename(columns={
            "keyword": "Keyword", "match": "Match", "spend": "Spend",
            "gmv": "GMV", "roi": "ROI", "conversions": "Conv", "clicks": "Clicks",
        })
        display["ROI"] = display["ROI"].round(2)
        total_wasted = losers["spend"].sum() - losers["gmv"].sum()
        st.error(f"💸 Total unrecovered spend from these keywords: ₹{format_inr(total_wasted)} "
                 f"(across {len(losers)} keyword-match combos)")
        st.dataframe(format_df_inr(display, ["Spend", "GMV"]),
                     width='stretch', hide_index=True)

    st.divider()

    # === Zero-conversion, clicks > 0 (negative-keyword candidates) =========
    st.subheader("🕳️  Zero-conversion clicks (negative-keyword candidates)")
    st.caption(
        "Keywords that spent money and got clicks but zero conversions. "
        "Shoppers landed but didn't buy — likely an intent mismatch. "
        "Strong candidates to pause or add to a negative-keyword list."
    )
    # Selectable spend floor - the default (Rs.100) is strict and often
    # empty on smaller windows, so let the user relax the threshold to
    # widen the net (or tighten it to prioritize only the loudest wastes).
    ZERO_CONV_THRESHOLDS = {
        "Any spend": 0,
        "≥ ₹10": 10,
        "≥ ₹50": 50,
        f"≥ ₹{MEANINGFUL_SPEND} (default)": MEANINGFUL_SPEND,
        "≥ ₹500": 500,
        "≥ ₹1,000": 1000,
    }
    zero_conv_col1, zero_conv_col2 = st.columns([1, 3])
    with zero_conv_col1:
        zc_choice = st.selectbox(
            "Min spend threshold", list(ZERO_CONV_THRESHOLDS.keys()),
            index=3, key="zero_conv_threshold",
            help="Lower this to surface smaller-spend keywords with zero conversions",
        )
    zc_min = ZERO_CONV_THRESHOLDS[zc_choice]
    zeroes = (kw[(kw["spend"] >= zc_min) & (kw["conversions"] == 0) & (kw["clicks"] > 0)]
              .sort_values("spend", ascending=False)
              .head(50)
              .copy())
    if zeroes.empty:
        st.success(f"Every keyword with ≥ ₹{zc_min:,} spend converted at least once. "
                   "Try lowering the threshold to widen the search.")
    else:
        st.caption(f"Showing {len(zeroes)} keyword-match combo(s) at this threshold "
                   f"(total unrecovered spend ₹{format_inr(zeroes['spend'].sum())}).")
        display = zeroes[["keyword", "match", "spend", "clicks", "ctr", "cpa"]].rename(columns={
            "keyword": "Keyword", "match": "Match", "spend": "Spend",
            "clicks": "Clicks", "ctr": "CTR %", "cpa": "CPA",
        })
        display["CTR %"] = display["CTR %"].round(2)
        display["CPA"] = "-"  # infinite by definition
        st.dataframe(format_df_inr(display, ["Spend"]),
                     width='stretch', hide_index=True)

    st.divider()

    # === Head-to-head: Broad vs Exact for the same keyword =================
    st.subheader("⚔️  Broad vs Exact head-to-head (same keyword, both match types)")
    st.caption(
        "Keywords that ran in both match types in this window. "
        "The winner column is decided by ROI, tie-broken by GMV. "
        "Use this to consolidate — if one match type is clearly winning, "
        "shift budget to it and pause the other."
    )
    h2h = _head_to_head(db_version, start_date, end_date, campaigns, cities)
    if h2h.empty:
        st.info("No keywords were run in both broad and exact match in this window.")
    else:
        def _winner(row):
            b, e = row["broad_roi"] or 0, row["exact_roi"] or 0
            if abs(b - e) < 0.15 * max(b, e, 1):
                return "Too close — keep both"
            if b > e:
                lift = (b - e) / e * 100 if e > 0 else float("inf")
                return f"Broad wins ({lift:.0f}% better ROI)" if lift != float("inf") else "Broad wins (exact = 0)"
            lift = (e - b) / b * 100 if b > 0 else float("inf")
            return f"Exact wins ({lift:.0f}% better ROI)" if lift != float("inf") else "Exact wins (broad = 0)"

        h2h["Winner"] = h2h.apply(_winner, axis=1)
        h2h["_total_spend"] = h2h["broad_spend"].fillna(0) + h2h["exact_spend"].fillna(0)
        h2h = h2h.sort_values("_total_spend", ascending=False).head(25).drop(columns=["_total_spend"])
        for c in ["broad_roi", "exact_roi"]:
            h2h[c] = h2h[c].round(2)
        display = h2h.rename(columns={
            "keyword": "Keyword",
            "broad_spend": "Broad Spend", "broad_gmv": "Broad GMV",
            "broad_roi": "Broad ROI", "broad_conv": "Broad Conv",
            "exact_spend": "Exact Spend", "exact_gmv": "Exact GMV",
            "exact_roi": "Exact ROI", "exact_conv": "Exact Conv",
        })
        st.dataframe(
            format_df_inr(display, ["Broad Spend", "Broad GMV", "Exact Spend", "Exact GMV"]),
            width='stretch', hide_index=True,
        )

    st.divider()

    # === Search-query-level intelligence (what shoppers actually typed) ====
    st.subheader("🎯 Search query intelligence — what shoppers actually typed")
    st.caption(
        "Everything below is computed directly from the **Search Query Report** "
        "(`search_query` table) — the literal text shoppers typed into Instamart. "
        "City filter doesn't apply here (report ships with `city_count` aggregate)."
    )

    # ---- 7A. Overview KPIs -------------------------------------------------
    st.markdown("##### Overview")
    ov = _search_query_overview(db_version, start_date, end_date, campaigns)
    if ov["unique_queries"] == 0:
        st.warning("No search query data in the current filters.")
    else:
        blended_roi = ov["gmv"] / ov["spend"] if ov["spend"] else 0
        conv_pct = ov["queries_with_conv"] / ov["queries_with_clicks"] * 100 if ov["queries_with_clicks"] else 0
        broad_pct = ov["broad_spend"] / ov["spend"] * 100 if ov["spend"] else 0
        broad_roi = ov["broad_gmv"] / ov["broad_spend"] if ov["broad_spend"] else 0
        exact_roi = ov["exact_gmv"] / ov["exact_spend"] if ov["exact_spend"] else 0

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        for col, (label, value, sub) in zip(
            [c1, c2, c3, c4, c5, c6],
            [
                ("Unique queries", f"{ov['unique_queries']:,}",
                 f"{ov['queries_with_clicks']:,} got clicks"),
                ("Spend", f"₹{format_inr(ov['spend'])}",
                 f"{broad_pct:.0f}% broad · {100-broad_pct:.0f}% exact"),
                ("GMV", f"₹{format_inr(ov['gmv'])}",
                 f"{ov['conversions']:,} orders"),
                ("Blended ROI", f"{blended_roi:.2f}x",
                 f"Broad {broad_roi:.2f}x · Exact {exact_roi:.2f}x"),
                ("% queries that converted",
                 f"{conv_pct:.1f}%",
                 f"{ov['queries_with_conv']:,} of {ov['queries_with_clicks']:,} clicked queries"),
                ("Impr. → Click",
                 f"{ov['clicks']/ov['impressions']*100:.2f}%" if ov['impressions'] else "—",
                 f"{ov['impressions']:,} impressions total"),
            ],
        ):
            with col:
                st.markdown(
                    f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
                    f'<div class="kpi-value">{value}</div>'
                    f'<div class="kpi-sub">{sub}</div></div>',
                    unsafe_allow_html=True,
                )

        # Narrative interpretation of the KPIs (all numbers pulled from ov)
        interpretation_bits = []
        if broad_pct > 55:
            interpretation_bits.append(
                f"**{broad_pct:.0f}% of search-query spend is going to broad match.** "
                "Broad expands your bid keyword to related terms — good for discovery, "
                "but you pay for the intent mismatch too. See the intent-gap section below.")
        elif broad_pct < 30:
            interpretation_bits.append(
                f"Only **{broad_pct:.0f}% of spend is on broad match** — you're already "
                "leaning heavily on exact. If growth is stalling, controlled broad on "
                "your top keywords can surface new query patterns to graduate to exact.")
        if exact_roi > broad_roi * 1.2 and ov["broad_spend"] > 0:
            interpretation_bits.append(
                f"**Exact ROI ({exact_roi:.2f}x) is {(exact_roi/broad_roi - 1)*100:.0f}% "
                f"better than broad ({broad_roi:.2f}x).** Every rupee shifted from broad "
                "to exact should return more, up to the point exact stops scaling.")
        elif broad_roi > exact_roi * 1.2:
            interpretation_bits.append(
                f"**Broad ROI ({broad_roi:.2f}x) is beating exact ({exact_roi:.2f}x).** "
                "Unusual — usually means your exact keyword coverage is too narrow. "
                "Look at the winning broad-match queries below and add them as new exact keywords.")
        if conv_pct < 20:
            interpretation_bits.append(
                f"Only **{conv_pct:.0f}% of clicked queries converted** — the other "
                f"{100-conv_pct:.0f}% saw your ad, clicked, and left without buying. "
                "That's the negative-keyword and product-mismatch opportunity.")
        if interpretation_bits:
            st.info("**What this means:** " + "  \n\n".join(interpretation_bits))

    st.markdown("---")

    # ---- 7B. Query length (long-tail) -------------------------------------
    st.markdown("##### Query length — head terms vs long-tail")
    st.caption(
        "Buckets every unique query by word count. Rule of thumb: longer queries "
        "usually reflect stronger purchase intent (shopper knows exactly what they want), "
        "while 1-2 word head terms are cheaper per impression but noisier."
    )
    ql = _query_length_analysis(db_version, start_date, end_date, campaigns)
    if not ql.empty:
        ql_display = ql[["bucket", "unique_queries", "impressions", "clicks", "spend",
                         "conversions", "gmv", "roi", "ctr", "click_conv_rate"]].copy()
        for c in ["roi"]:
            ql_display[c] = ql_display[c].round(2)
        for c in ["ctr", "click_conv_rate"]:
            ql_display[c] = ql_display[c].round(1)
        ql_display = ql_display.rename(columns={
            "bucket": "Bucket", "unique_queries": "Unique queries",
            "impressions": "Impressions", "clicks": "Clicks",
            "spend": "Spend", "conversions": "Conv", "gmv": "GMV",
            "roi": "ROI", "ctr": "CTR %", "click_conv_rate": "Click→Conv %",
        })
        st.dataframe(format_df_inr(ql_display, ["Spend", "GMV"]),
                     width='stretch', hide_index=True)

        # Compare short vs long-tail
        head = ql[ql["sort_key"].isin([1, 2])]
        tail = ql[ql["sort_key"].isin([4, 5])]
        head_roi = head["gmv"].sum() / head["spend"].sum() if head["spend"].sum() else 0
        tail_roi = tail["gmv"].sum() / tail["spend"].sum() if tail["spend"].sum() else 0
        head_spend = head["spend"].sum()
        tail_spend = tail["spend"].sum()
        insights = []
        if head_roi and tail_roi:
            if tail_roi > head_roi * 1.3:
                insights.append(
                    f"**Long-tail (4+ words) ROI is {tail_roi:.2f}x vs head-term (1-2 words) {head_roi:.2f}x** "
                    f"— long-tail is {(tail_roi/head_roi-1)*100:.0f}% more efficient. "
                    "You're currently spending ₹{spend_ht:,.0f} on head terms vs ₹{spend_lt:,.0f} on long-tail. "
                    "Consider shifting some head-term budget to long-tail expansion.".format(
                        spend_ht=head_spend, spend_lt=tail_spend))
            elif head_roi > tail_roi * 1.3:
                insights.append(
                    f"**Head terms ({head_roi:.2f}x ROI) are actually outperforming long-tail ({tail_roi:.2f}x).** "
                    "Unusual pattern — suggests your long-tail bids are landing on niche queries "
                    "with low conversion volume. Investigate the specific 4+ word queries below.")
            else:
                insights.append(
                    f"Head vs long-tail ROI are within 30% of each other "
                    f"({head_roi:.2f}x vs {tail_roi:.2f}x). Neither bucket is being starved.")
        # Highlight the sweet-spot bucket
        best = ql.loc[ql["roi"].idxmax()] if not ql["roi"].isna().all() else None
        if best is not None:
            insights.append(
                f"Best-performing bucket by ROI: **{best['bucket']}** at {best['roi']:.2f}x "
                f"across {int(best['unique_queries']):,} queries.")
        if insights:
            st.info("**What this means:** " + "  \n\n".join(insights))

    st.markdown("---")

    # ---- 7C. Query-keyword intent gap (broad match waste detector) ---------
    st.markdown("##### Broad-match intent gap — where broad expanded too far")
    st.caption(
        "For every broad-match row, we compute the word overlap between the **actual "
        "query the shopper typed** and the **keyword you bid on**, using Jaccard similarity "
        "(shared words ÷ total unique words). Overlap **0.0** = the two share no common "
        "words at all — broad match matched them on the *underlying category* or "
        "*misspelling correction*, not shared vocabulary. Overlap **1.0** = identical."
    )
    with st.container():
        gap_col1, gap_col2 = st.columns([1, 3])
        with gap_col1:
            gap_min_spend = st.selectbox(
                "Min spend on broad-match query", [10, 50, 100, 500, 1000],
                index=2, key="intent_gap_min",
                help="Only rank queries that have spent at least this much on broad match",
            )
    ig = _intent_gap_queries(db_version, start_date, end_date, campaigns, gap_min_spend)
    if ig.empty:
        st.info(f"No broad-match queries with ≥ ₹{gap_min_spend} spend in the current filters.")
    else:
        ig_display = ig.copy()
        ig_display["roi"] = ig_display["roi"].round(2)
        ig_display["word_overlap"] = (ig_display["word_overlap"] * 100).round(0).astype(int).astype(str) + "%"
        ig_display = ig_display.rename(columns={
            "search_query": "Actual query", "keyword": "Your bid keyword",
            "impressions": "Impr.", "clicks": "Clicks", "spend": "Spend",
            "conversions": "Conv", "gmv": "GMV", "roi": "ROI",
            "word_overlap": "Word overlap",
        })
        st.dataframe(format_df_inr(ig_display, ["Spend", "GMV"]),
                     width='stretch', hide_index=True, height=420)

        # Concrete recommendations from the top offender + aggregate
        n_zero_overlap = (ig["word_overlap"] == 0).sum()
        zero_overlap_spend = ig[ig["word_overlap"] == 0]["spend"].sum()
        zero_overlap_gmv = ig[ig["word_overlap"] == 0]["gmv"].sum()
        zero_overlap_roi = zero_overlap_gmv / zero_overlap_spend if zero_overlap_spend else 0
        gap_insights = []
        if n_zero_overlap > 0:
            gap_insights.append(
                f"**{n_zero_overlap} broad-match query/keyword pairs share ZERO words** "
                f"(total spend ₹{format_inr(zero_overlap_spend)}, ROI {zero_overlap_roi:.2f}x). "
                "These are the clearest broad-match expansion cases. If ROI is above your "
                "target, keep the broad match — the algorithm found paying customers you'd have "
                "missed. If ROI is below target, either add those queries as **new exact-match "
                "keywords** (to control bids), or as **negative keywords** (to stop the spend)."
            )
        top = ig.iloc[0]
        gap_insights.append(
            f"**Top offender:** shopper typed *\"{top['search_query']}\"* while you were bidding "
            f"on *\"{top['keyword']}\"* — {int(top['word_overlap']*100)}% word overlap, "
            f"₹{format_inr(top['spend'])} spent, {int(top['conversions'])} order(s), "
            f"ROI {top['roi']:.2f}x. "
            + ("Since it's converting, treat this as a **new exact keyword to graduate**."
               if top["roi"] >= WINNER_ROI else
               ("It's losing money — add as **negative keyword** on this campaign."
                if top["roi"] < LOSER_ROI else
                "Marginal — worth tightening the match type or lowering the bid."))
        )
        st.info("**What this means:** " + "  \n\n".join(gap_insights))

    st.markdown("---")

    # ---- 7D. Volume × ROI quadrants ---------------------------------------
    st.markdown("##### Volume × ROI quadrants — which queries deserve which action")
    st.caption(
        "Each dot is one unique search query with ≥ 10 impressions. "
        "Median impressions and median ROI (across the filtered queries) form the axes. "
        "Each of the four quadrants maps to a specific playbook."
    )
    qd = _query_quadrants(db_version, start_date, end_date, campaigns, min_impressions=10)
    if qd.empty:
        st.info("Not enough query volume in the current filters to plot quadrants.")
    else:
        med_impr = float(qd["impressions"].median())
        med_roi = float(qd["roi"].median())
        qd = qd.copy()
        qd["quadrant"] = qd.apply(
            lambda r: (
                "🚀 Scale (high vol · high ROI)" if r["impressions"] >= med_impr and r["roi"] >= med_roi
                else "🌱 Hidden gem (low vol · high ROI)" if r["roi"] >= med_roi
                else "💸 Money drain (high vol · low ROI)" if r["impressions"] >= med_impr
                else "🪦 Prune (low vol · low ROI)"
            ), axis=1,
        )
        quadrant_colors = {
            "🚀 Scale (high vol · high ROI)":       accents["green"]["fg"],
            "🌱 Hidden gem (low vol · high ROI)":   accents["blue"]["fg"],
            "💸 Money drain (high vol · low ROI)":  accents["red"]["fg"],
            "🪦 Prune (low vol · low ROI)":         accents["amber"]["fg"],
        }
        scatter = alt.Chart(qd).mark_circle(size=40, opacity=0.55).encode(
            x=alt.X("impressions:Q", scale=alt.Scale(type="log"),
                    title="Impressions (log scale)"),
            y=alt.Y("roi:Q", scale=alt.Scale(clamp=True, domain=[0, max(10, qd["roi"].quantile(0.98))]),
                    title="ROI (GMV / Spend)"),
            color=alt.Color("quadrant:N",
                            scale=alt.Scale(domain=list(quadrant_colors.keys()),
                                            range=list(quadrant_colors.values())),
                            legend=alt.Legend(title=None, orient="top", columns=2)),
            tooltip=[
                alt.Tooltip("search_query:N", title="Query"),
                alt.Tooltip("impressions:Q", format=",.0f"),
                alt.Tooltip("clicks:Q", format=",.0f"),
                alt.Tooltip("spend:Q", format=",.0f", title="Spend ₹"),
                alt.Tooltip("gmv:Q", format=",.0f", title="GMV ₹"),
                alt.Tooltip("roi:Q", format=".2f", title="ROI"),
                alt.Tooltip("conversions:Q", format=",.0f", title="Conv"),
                alt.Tooltip("quadrant:N"),
            ],
        )
        med_lines = (alt.Chart(pd.DataFrame({"impr": [med_impr], "roi": [med_roi]}))
                     .mark_rule(strokeDash=[4, 4], color=accents["amber"]["fg"])
                     .encode(x="impr:Q") +
                     alt.Chart(pd.DataFrame({"impr": [med_impr], "roi": [med_roi]}))
                     .mark_rule(strokeDash=[4, 4], color=accents["amber"]["fg"])
                     .encode(y="roi:Q"))
        st.altair_chart((scatter + med_lines).properties(height=340), width='stretch')

        # Counts + total-spend per quadrant + prescription
        q_summary = (qd.groupby("quadrant")
                     .agg(queries=("search_query", "count"),
                          spend=("spend", "sum"),
                          gmv=("gmv", "sum"))
                     .reset_index())
        q_summary["ROI"] = (q_summary["gmv"] / q_summary["spend"].replace(0, pd.NA)).round(2)
        q_display = q_summary[["quadrant", "queries", "spend", "gmv", "ROI"]].rename(columns={
            "quadrant": "Quadrant", "queries": "# queries",
            "spend": "Spend", "gmv": "GMV",
        })
        st.dataframe(format_df_inr(q_display, ["Spend", "GMV"]),
                     width='stretch', hide_index=True)
        st.markdown(f"""
**Playbook (what to do with each quadrant):**
- 🚀 **Scale** — high volume, ROI above the median. These are your workhorses. Raise bids or budget to capture more impression share.
- 🌱 **Hidden gems** — low volume but great ROI. Add them as exact-match keywords, raise bids to grow their share, or write dedicated ad creative for them.
- 💸 **Money drains** — high volume, ROI below the median. Highest-priority intervention. Options: negative keyword, tighter match type, lower bid, or fix landing page.
- 🪦 **Prune** — low volume, low ROI. Low priority individually but they add up. Bulk-pause anything with < ₹50 GMV.

_(Median impressions cutoff for this window: **{med_impr:,.0f}** · median ROI: **{med_roi:.2f}x**.)_
""")

    st.markdown("---")

    # ---- 7E. Top converters + wasted spend (kept as before, side by side) ---
    st.markdown("##### Top converters & wasted spend")
    st.caption("Two tables side-by-side — the queries that are paying off, and the queries "
               "with meaningful spend but zero orders (the negative-keyword shortlist).")
    top_q, waste_q = _search_query_intelligence(db_version, start_date, end_date, campaigns)

    t_col, w_col = st.columns(2)
    with t_col:
        st.markdown("**Top converting queries (top 30 by GMV)**")
        if top_q.empty:
            st.caption("No search query data in the current filters.")
        else:
            t = top_q[["search_query", "keyword", "match", "spend", "gmv", "roi", "conversions"]].copy()
            t["roi"] = t["roi"].round(2)
            t = t.rename(columns={
                "search_query": "Query", "keyword": "Bid keyword",
                "match": "Match", "spend": "Spend", "gmv": "GMV",
                "roi": "ROI", "conversions": "Conv",
            })
            st.dataframe(format_df_inr(t, ["Spend", "GMV"]),
                         width='stretch', hide_index=True, height=420)
    with w_col:
        st.markdown("**Wasted spend queries (spent ≥ ₹100, zero conversions)**")
        st.caption("These are top candidates to add as **negative keywords** — "
                   "you're paying for the click but nobody buys.")
        if waste_q.empty:
            st.caption("No wasted-spend queries in the current filters.")
        else:
            w = waste_q[["search_query", "keyword", "match", "spend", "clicks", "impressions"]].copy()
            w = w.rename(columns={
                "search_query": "Query", "keyword": "Bid keyword",
                "match": "Match", "spend": "Spend", "clicks": "Clicks",
                "impressions": "Impr.",
            })
            st.dataframe(format_df_inr(w, ["Spend"]),
                         width='stretch', hide_index=True, height=420)
            total_waste = waste_q["spend"].sum()
            st.error(f"💸 Total wasted spend from these queries: ₹{format_inr(total_waste)}")

    st.divider()

    # === Product x keyword goldmine ========================================
    st.subheader("💎 Product × keyword goldmines and mismatches")
    st.caption(
        "Pairs the exports link explicitly (a keyword shown against a specific "
        "product ad). Great pairs = the creative and the query align; bad pairs "
        "= the wrong product is showing up for a query and eating budget."
    )
    pk = _product_keyword_matrix(db_version, start_date, end_date, campaigns, cities)
    if pk.empty:
        st.info("No product-keyword pairs with meaningful spend in the current filters.")
    else:
        gold_col, mud_col = st.columns(2)
        with gold_col:
            st.markdown("**Gold pairings (top 20 by GMV)**")
            gold = (pk[pk["roi"] >= WINNER_ROI]
                    .sort_values("gmv", ascending=False)
                    .head(20)
                    .copy())
            if gold.empty:
                st.caption("No pairings hit the winner ROI threshold in this window.")
            else:
                g = gold[["product_name", "keyword", "match", "spend", "gmv", "roi", "conversions"]].copy()
                g["roi"] = g["roi"].round(2)
                g = g.rename(columns={
                    "product_name": "Product", "keyword": "Keyword",
                    "match": "Match", "spend": "Spend", "gmv": "GMV",
                    "roi": "ROI", "conversions": "Conv",
                })
                st.dataframe(format_df_inr(g, ["Spend", "GMV"]),
                             width='stretch', hide_index=True, height=420)
        with mud_col:
            st.markdown("**Money pit pairings (spend but ROI < 1x)**")
            mud = (pk[(pk["roi"] < LOSER_ROI)]
                   .sort_values("spend", ascending=False)
                   .head(20)
                   .copy())
            if mud.empty:
                st.caption("No underperforming product-keyword pairs. Nice.")
            else:
                m = mud[["product_name", "keyword", "match", "spend", "gmv", "roi", "conversions"]].copy()
                m["roi"] = m["roi"].round(2)
                m = m.rename(columns={
                    "product_name": "Product", "keyword": "Keyword",
                    "match": "Match", "spend": "Spend", "gmv": "GMV",
                    "roi": "ROI", "conversions": "Conv",
                })
                st.dataframe(format_df_inr(m, ["Spend", "GMV"]),
                             width='stretch', hide_index=True, height=420)

    st.divider()

    # === Summary action list ==============================================
    st.subheader("📋 Quick action summary")
    n_win = len(kw[(kw["spend"] >= MEANINGFUL_SPEND) & (kw["roi"] >= WINNER_ROI)])
    n_lose = len(kw[(kw["spend"] >= MEANINGFUL_SPEND) & (kw["roi"] < LOSER_ROI)])
    n_zero = len(kw[(kw["spend"] >= MEANINGFUL_SPEND) & (kw["conversions"] == 0) & (kw["clicks"] > 0)])
    n_waste = len(waste_q) if not waste_q.empty else 0

    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
    summary_col1.metric("🚀 Scale", n_win, help="Keywords with high ROI worth more budget")
    summary_col2.metric("🛑 Pause", n_lose, help="Keywords losing money")
    summary_col3.metric("🕳️  Neg. kw candidates", n_zero, help="Clicks but no conversions")
    summary_col4.metric("💸 Wasted queries", n_waste, help="Search queries with spend but no conversions")

    st.caption(
        f"Thresholds used: winner ROI ≥ {WINNER_ROI:.0f}x, loser ROI < {LOSER_ROI:.0f}x, "
        f"meaningful spend ≥ ₹{MEANINGFUL_SPEND}. "
        "Edit the constants at the top of `search_query_deep_dive.py` to tune."
    )
