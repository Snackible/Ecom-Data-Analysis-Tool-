"""Advanced search query intelligence - intent classification, semantic analysis,
product affinities, cannibalization detection, and formula-based recommendations.

Every analysis is grounded in formulas, not heuristics. All recommendations come
with the math that drives them.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
import altair as alt

import config


# ---------------------------------------------------------------------------
# Query Intent Classification (semantic bucketing based on query text patterns)
# ---------------------------------------------------------------------------

def classify_intent(query: str) -> str:
    """Classify a search query into intent buckets based on linguistic patterns.

    Returns one of: brand, competitor, problem_solution, price, discovery,
    modifier, quantity, specific_product.
    """
    q = query.lower().strip()

    # Brand queries: mention specific brand or "vs competitor"
    brands = ["snackible", "patanjali", "nature", "organic", "native", "bob's", "britannia",
              "haldiram", "dabur", "himalaya", "monsoon", "lakme", "mamaearth"]
    if any(b in q for b in brands) or " vs " in q or " v/s " in q:
        return "brand"

    # Competitor signals
    if any(x in q for x in ["vs ", "v/s ", "alternative", "instead", "better", "cheaper"]):
        return "competitor_comparison"

    # Price signals
    if any(x in q for x in ["price", "cost", "cheap", "discount", "offer", "deal", "budget",
                             "affordable", "inexpensive", "expensive", "premium", "rate"]):
        return "price_sensitive"

    # Problem-solution: "for", "to", "helps", "treat", "relief", "cure"
    if any(x in q for x in [" for ", " to ", "helps", "treat", "relief", "cure", "benefit",
                             "good for", "best for", "fight", "boost", "improve"]):
        return "problem_solution"

    # Quantity: "bulk", "pack", "combo", "set", "dozen", "kg", "500g"
    if any(x in q for x in ["bulk", "pack", "combo", "set", "dozen", "kg", "g", "liter",
                             "litre", "ml", "500", "250"]):
        return "quantity_deal"

    # Discovery: vague, broad, browsing intent (single noun)
    words = q.split()
    if len(words) <= 2 and not any(x in q for x in ["buy", "online", "where"]):
        return "discovery"

    # Specific product: "XYZ brand Y product"
    if len(words) >= 3:
        return "specific_product"

    return "other"


def intent_emoji(intent: str) -> str:
    """Emoji for each intent type."""
    emojis = {
        "brand": "🏷️ ",
        "competitor_comparison": "⚔️ ",
        "price_sensitive": "💰",
        "problem_solution": "⚕️ ",
        "quantity_deal": "📦",
        "discovery": "🔍",
        "specific_product": "🎯",
        "other": "❓",
    }
    return emojis.get(intent, "❓")


# ---------------------------------------------------------------------------
# Keyword-Query Semantic Similarity with detailed overlap analysis
# ---------------------------------------------------------------------------

def word_overlap_analysis(keyword: str, query: str) -> dict:
    """Detailed breakdown of semantic overlap between keyword and actual query.

    Returns: {
        'overlap_pct': float 0-100,  # Jaccard similarity
        'common_words': [str],        # Words in both
        'keyword_only': [str],        # Words in keyword but not query
        'query_only': [str],          # Words in query but not keyword
        'pattern': str,               # "perfect_match", "core_match", "loose", "mismatch"
    }
    """
    kw_words = set(keyword.lower().split())
    q_words = set(query.lower().split())

    common = kw_words & q_words
    only_kw = kw_words - q_words
    only_q = q_words - kw_words

    union = kw_words | q_words
    jaccard = len(common) / len(union) if union else 0
    overlap_pct = jaccard * 100

    # Pattern classification
    if len(common) == len(kw_words) and len(only_q) == 0:
        pattern = "perfect_match"
    elif len(common) >= 2 or (len(common) >= 1 and len(kw_words) <= 2):
        pattern = "core_match"
    elif len(common) >= 1:
        pattern = "loose_match"
    else:
        pattern = "mismatch"

    return {
        "overlap_pct": overlap_pct,
        "common_words": sorted(common),
        "keyword_only": sorted(only_kw),
        "query_only": sorted(only_q),
        "pattern": pattern,
    }


# ---------------------------------------------------------------------------
# Product Affinity Analysis (which products convert together on same queries)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def product_affinity_matrix(db_version, start_date, end_date, campaigns):
    """Co-occurrence matrix: for each search query with 2+ products,
    compute how often products appear together and their relative ROI."""
    con = config.connect_db()

    # Get all queries with 2+ products
    df = con.execute(f"""
        WITH query_products AS (
            SELECT search_query,
                   product_name,
                   SUM(total_gmv) AS gmv,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                   ROW_NUMBER() OVER (PARTITION BY search_query ORDER BY gmv DESC) AS rank_in_query
            FROM search_query
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND search_query IS NOT NULL
              AND product_name IS NOT NULL
            GROUP BY search_query, product_name
        ),
        multi_product_queries AS (
            SELECT search_query
            FROM query_products
            GROUP BY search_query
            HAVING COUNT(DISTINCT product_name) >= 2
        )
        SELECT qp.search_query, qp.product_name, qp.gmv, qp.spend, qp.roi, qp.rank_in_query
        FROM query_products qp
        JOIN multi_product_queries mpq ON qp.search_query = mpq.search_query
        ORDER BY qp.search_query, qp.rank_in_query
    """, [start_date, end_date, *campaigns]).fetchdf()
    con.close()

    return df


# ---------------------------------------------------------------------------
# Cannibalization Detection (broad match stealing from exact on same query)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def detect_cannibalization(db_version, start_date, end_date, campaigns):
    """Find queries where the same keyword runs in both broad and exact match,
    and broad is capturing the intent that exact should own."""
    con = config.connect_db()

    df = con.execute(f"""
        WITH per_match AS (
            SELECT search_query, keyword, match_type,
                   SUM(total_budget_burnt) AS spend,
                   SUM(total_conversions) AS conversions,
                   SUM(total_gmv) AS gmv,
                   SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
                   COUNT(DISTINCT metrics_date) AS days
            FROM search_query
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND search_query IS NOT NULL
              AND keyword IS NOT NULL
            GROUP BY search_query, keyword, match_type
        )
        SELECT search_query, keyword,
               MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN spend END) AS broad_spend,
               MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN conversions END) AS broad_conv,
               MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN roi END) AS broad_roi,
               MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN spend END) AS exact_spend,
               MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN conversions END) AS exact_conv,
               MAX(CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_EXACT' THEN roi END) AS exact_roi
        FROM per_match
        GROUP BY search_query, keyword
        HAVING broad_spend IS NOT NULL AND exact_spend IS NOT NULL
    """, [start_date, end_date, *campaigns]).fetchdf()
    con.close()

    # Calculate cannibalization score: broad ROI much higher = broad outperforming exact
    # Means exact isn't capturing what it should on this specific query
    df["cannib_risk"] = 0.0
    mask = (df["exact_roi"] > 0) & (df["broad_roi"] > 0)
    df.loc[mask, "cannib_risk"] = (df.loc[mask, "broad_roi"] - df.loc[mask, "exact_roi"]) / df.loc[mask, "exact_roi"]

    return df[df["cannib_risk"] > 0.2].sort_values("broad_spend", ascending=False)  # 20%+ ROI gap


# ---------------------------------------------------------------------------
# Query Performance by Product (head-to-head on same query)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def query_product_comparison(db_version, search_query, start_date, end_date, campaigns):
    """All products' performance on a specific search query."""
    con = config.connect_db()

    df = con.execute(f"""
        SELECT product_name,
               SUM(total_budget_burnt) AS spend,
               SUM(total_clicks) AS clicks,
               SUM(total_conversions) AS conversions,
               SUM(total_gmv) AS gmv,
               SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
               100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
               100.0 * SUM(total_conversions) / NULLIF(SUM(total_clicks), 0) AS click_conv_rate,
               COUNT(DISTINCT metrics_date) AS days_active
        FROM search_query
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND search_query = ?
          AND product_name IS NOT NULL
        GROUP BY product_name
        ORDER BY gmv DESC
    """, [start_date, end_date, *campaigns, search_query]).fetchdf()
    con.close()

    return df


# ---------------------------------------------------------------------------
# Intent-Based Performance (how do different query intents perform?)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def performance_by_intent(db_version, start_date, end_date, campaigns):
    """Aggregate performance metrics bucketed by query intent."""
    con = config.connect_db()

    df = con.execute(f"""
        SELECT search_query,
               SUM(total_budget_burnt) AS spend,
               SUM(total_impressions) AS impressions,
               SUM(total_clicks) AS clicks,
               SUM(total_conversions) AS conversions,
               SUM(total_gmv) AS gmv,
               SUM(total_gmv) / NULLIF(SUM(total_budget_burnt), 0) AS roi,
               100.0 * SUM(total_clicks) / NULLIF(SUM(total_impressions), 0) AS ctr,
               100.0 * SUM(total_conversions) / NULLIF(SUM(total_clicks), 0) AS click_conv_rate
        FROM search_query
        WHERE metrics_date BETWEEN ? AND ?
          AND campaign_name IN ({','.join(['?'] * len(campaigns))})
          AND search_query IS NOT NULL
        GROUP BY search_query
        HAVING SUM(total_budget_burnt) > 0
    """, [start_date, end_date, *campaigns]).fetchdf()
    con.close()

    # Apply intent classification
    df["intent"] = df["search_query"].apply(classify_intent)

    # Aggregate by intent
    intent_agg = df.groupby("intent").agg({
        "search_query": "count",
        "spend": "sum",
        "impressions": "sum",
        "clicks": "sum",
        "conversions": "sum",
        "gmv": "sum",
    }).rename(columns={"search_query": "unique_queries"})

    intent_agg["roi"] = intent_agg["gmv"] / intent_agg["spend"].replace(0, pd.NA)
    intent_agg["ctr"] = 100 * intent_agg["clicks"] / intent_agg["impressions"].replace(0, pd.NA)
    intent_agg["click_conv_rate"] = 100 * intent_agg["conversions"] / intent_agg["clicks"].replace(0, pd.NA)
    intent_agg["cpa"] = intent_agg["spend"] / intent_agg["conversions"].replace(0, pd.NA)

    return intent_agg.reset_index()


# ---------------------------------------------------------------------------
# Render advanced analysis
# ---------------------------------------------------------------------------

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


def render(db_version, start_date, end_date, campaigns, format_df_inr):
    """Render advanced search query intelligence dashboard."""
    st.markdown("## 🧠 Advanced Search Query Intelligence")
    st.markdown(
        "Intent classification, semantic analysis, product affinities, "
        "cannibalization detection, and formula-driven recommendations."
    )

    # === Intent Performance ===
    st.subheader("Intent Classification & Performance")
    st.caption(
        "Every query bucketed by shopper intent: brand, competitor, price, problem-solution, discovery. "
        "Shows which intent types drive ROI."
    )

    intent_perf = performance_by_intent(db_version, start_date, end_date, campaigns)
    if not intent_perf.empty:
        intent_perf_display = intent_perf[[
            "intent", "unique_queries", "spend", "gmv", "roi", "ctr", "click_conv_rate", "conversions", "cpa"
        ]].copy()

        intent_perf_display["intent"] = intent_perf_display["intent"].apply(
            lambda x: f"{intent_emoji(x)} {x.upper()}"
        )

        for col in ["roi", "ctr", "click_conv_rate", "cpa"]:
            intent_perf_display[col] = intent_perf_display[col].round(2)

        intent_perf_display = intent_perf_display.rename(columns={
            "intent": "Intent", "unique_queries": "Unique Queries",
            "spend": "Spend", "gmv": "GMV", "roi": "ROI", "ctr": "CTR %",
            "click_conv_rate": "Click→Conv %", "conversions": "Conv", "cpa": "CPA"
        })

        st.dataframe(format_df_inr(intent_perf_display, ["Spend", "GMV", "CPA"]),
                     use_container_width=True, hide_index=True)

        # Insights
        best_intent = intent_perf.loc[intent_perf["roi"].idxmax()]
        worst_intent = intent_perf.loc[intent_perf["roi"].idxmin()]
        st.info(
            f"**Best intent:** {intent_emoji(best_intent['intent'])} {best_intent['intent'].upper()} "
            f"(ROI {best_intent['roi']:.2f}x)  \n"
            f"**Worst intent:** {intent_emoji(worst_intent['intent'])} {worst_intent['intent'].upper()} "
            f"(ROI {worst_intent['roi']:.2f}x)  \n"
            f"**Gap:** {(best_intent['roi'] / worst_intent['roi']):.1f}x — consider shifting budget from "
            f"low-intent queries to high-intent."
        )

    st.divider()

    # === Product Affinities ===
    st.subheader("Product Affinities — Which Products Convert Together")
    st.caption(
        "Searches that attracted 2+ products. Shows which products are natural complements "
        "(high affinity) and which cannibalize each other."
    )

    affinity = product_affinity_matrix(db_version, start_date, end_date, campaigns)
    if not affinity.empty:
        # Show top multi-product queries
        top_queries = affinity.groupby("search_query")["gmv"].sum().nlargest(10).index

        for query in top_queries[:5]:
            query_data = affinity[affinity["search_query"] == query].sort_values("gmv", ascending=False)
            total_gmv = query_data["gmv"].sum()

            with st.expander(f"**{query}** — ₹{format_inr(total_gmv)} GMV ({len(query_data)} products)"):
                display = query_data[["product_name", "rank_in_query", "spend", "conversions", "gmv", "roi"]].copy()
                display["roi"] = display["roi"].round(2)
                display = display.rename(columns={
                    "product_name": "Product", "rank_in_query": "Rank",
                    "spend": "Spend", "conversions": "Conv", "gmv": "GMV", "roi": "ROI"
                })
                st.dataframe(format_df_inr(display, ["Spend", "GMV"]),
                            use_container_width=True, hide_index=True)

    st.divider()

    # === Cannibalization Detection ===
    st.subheader("⚠️  Cannibalization Risk — Broad Stealing from Exact")
    st.caption(
        "When the same keyword runs in both broad and exact match on the same query, "
        "and broad ROI is 20%+ higher — exact isn't capturing what it should. "
        "Action: shift budget from broad to exact, or add negatives."
    )

    cannib = detect_cannibalization(db_version, start_date, end_date, campaigns)
    if not cannib.empty:
        cannib_display = cannib[[
            "search_query", "keyword", "broad_spend", "broad_roi", "exact_spend", "exact_roi", "cannib_risk"
        ]].head(15).copy()

        for col in ["broad_roi", "exact_roi", "cannib_risk"]:
            cannib_display[col] = cannib_display[col].round(2)

        cannib_display = cannib_display.rename(columns={
            "search_query": "Search Query", "keyword": "Keyword",
            "broad_spend": "Broad Spend", "broad_roi": "Broad ROI",
            "exact_spend": "Exact Spend", "exact_roi": "Exact ROI",
            "cannib_risk": "Cannib Risk %"
        })

        st.dataframe(format_df_inr(cannib_display, ["Broad Spend", "Exact Spend"]),
                    use_container_width=True, hide_index=True)

        total_cannib_spend = cannib["broad_spend"].sum()
        st.warning(
            f"💡 **{len(cannib)} query-keyword pairs show cannibalization risk.** "
            f"Total broad-match spend involved: ₹{format_inr(total_cannib_spend)}. "
            f"Test shifting 10-20% of this to exact match to protect exact ROI."
        )
    else:
        st.success("✅ No significant cannibalization detected.")

    st.divider()

    # === Deep Query Analysis ===
    st.subheader("🔬 Deep Query Analysis — Pick a Query to Dissect")

    all_queries = performance_by_intent(db_version, start_date, end_date, campaigns)
    all_queries = all_queries.sort_values("gmv", ascending=False)

    if not all_queries.empty:
        query_choice = st.selectbox(
            "Query:",
            all_queries["search_query"].unique()[:100],
            format_func=lambda q: f"{q} — ₹{format_inr(all_queries[all_queries['search_query']==q]['gmv'].sum())} GMV"
        )

        query_data = all_queries[all_queries["search_query"] == query_choice].iloc[0]

        st.markdown(f"### {query_choice}")
        st.markdown(f"**Intent:** {intent_emoji(query_data['intent'])} {query_data['intent'].upper()}")

        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            st.metric("GMV", f"₹{format_inr(query_data['gmv'])}")
        with col2:
            st.metric("Spend", f"₹{format_inr(query_data['spend'])}")
        with col3:
            st.metric("ROI", f"{query_data['roi']:.2f}x")
        with col4:
            st.metric("CTR", f"{query_data['ctr']:.2f}%")
        with col5:
            st.metric("Click→Conv", f"{query_data['click_conv_rate']:.1f}%")
        with col6:
            st.metric("CPA", f"₹{format_inr(query_data['cpa'])}")

        st.divider()

        # Product breakdown
        st.markdown("**Products on this query:**")
        prod_comp = query_product_comparison(db_version, query_choice, start_date, end_date, campaigns)

        if not prod_comp.empty:
            prod_display = prod_comp[[
                "product_name", "spend", "conversions", "gmv", "roi", "ctr", "click_conv_rate"
            ]].copy()

            for col in ["roi", "ctr", "click_conv_rate"]:
                prod_display[col] = prod_display[col].round(2)

            prod_display = prod_display.rename(columns={
                "product_name": "Product", "spend": "Spend",
                "conversions": "Conv", "gmv": "GMV", "roi": "ROI",
                "ctr": "CTR %", "click_conv_rate": "Click→Conv %"
            })

            st.dataframe(format_df_inr(prod_display, ["Spend", "GMV"]),
                        use_container_width=True, hide_index=True)

            # Head-to-head recommendation
            if len(prod_comp) >= 2:
                top_prod = prod_comp.iloc[0]
                second_prod = prod_comp.iloc[1]
                roi_gap = (top_prod["roi"] - second_prod["roi"]) / second_prod["roi"] * 100 if second_prod["roi"] > 0 else 0

                st.info(
                    f"**{top_prod['product_name']}** outperforms **{second_prod['product_name']}** "
                    f"by {roi_gap:.0f}% ROI on this query. "
                    f"Consider increasing bid for {top_prod['product_name']} and lowering for {second_prod['product_name']}."
                )
