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
    con = config.connect_db(read_only=True)
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
    con = config.connect_db(read_only=True)
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
    con = config.connect_db(read_only=True)
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
def _search_query_intelligence(db_version, start_date, end_date, campaigns):
    """Top and bottom actual search queries (what shoppers typed).
    search_query has no per-row city column (it has a city_count aggregate
    instead), so city filtering doesn't apply here - flagged in the UI."""
    con = config.connect_db(read_only=True)
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
    con = config.connect_db(read_only=True)
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
    color_for = {"Broad": "blue", "Exact": "pink", "Other/None": "yellow"}
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
                                            range=[accents["blue"]["fg"], accents["pink"]["fg"]])),
            tooltip=["Match type", "Metric", alt.Tooltip("Share:Q", format=".1f")],
        ).properties(height=110)
        st.altair_chart(chart, use_container_width=True)
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
                     use_container_width=True, hide_index=True)

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
                     use_container_width=True, hide_index=True)

    st.divider()

    # === Zero-conversion, clicks > 0 (negative-keyword candidates) =========
    st.subheader("🕳️  Zero-conversion clicks (negative-keyword candidates)")
    st.caption(
        "Keywords that spent money and got clicks but zero conversions. "
        "Shoppers landed but didn't buy — likely an intent mismatch. "
        "Strong candidates to pause or add to a negative-keyword list."
    )
    zeroes = (kw[(kw["spend"] >= MEANINGFUL_SPEND) & (kw["conversions"] == 0) & (kw["clicks"] > 0)]
              .sort_values("spend", ascending=False)
              .head(25)
              .copy())
    if zeroes.empty:
        st.success("Every keyword with real spend converted at least once. Nice.")
    else:
        display = zeroes[["keyword", "match", "spend", "clicks", "ctr", "cpa"]].rename(columns={
            "keyword": "Keyword", "match": "Match", "spend": "Spend",
            "clicks": "Clicks", "ctr": "CTR %", "cpa": "CPA",
        })
        display["CTR %"] = display["CTR %"].round(2)
        display["CPA"] = "-"  # infinite by definition
        st.dataframe(format_df_inr(display, ["Spend"]),
                     use_container_width=True, hide_index=True)

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
            use_container_width=True, hide_index=True,
        )

    st.divider()

    # === Search-query-level intelligence (what shoppers actually typed) ====
    st.subheader("🎯 Search query intelligence — what shoppers actually typed")
    st.caption(
        "From the Search Query Report. City filter doesn't apply here "
        "(that report only has an aggregate city_count column)."
    )
    top_q, waste_q = _search_query_intelligence(db_version, start_date, end_date, campaigns)

    t_col, w_col = st.columns(2)
    with t_col:
        st.markdown("**Top converting queries (drive most GMV)**")
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
                         use_container_width=True, hide_index=True, height=420)
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
                         use_container_width=True, hide_index=True, height=420)
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
                             use_container_width=True, hide_index=True, height=420)
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
                             use_container_width=True, hide_index=True, height=420)

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
