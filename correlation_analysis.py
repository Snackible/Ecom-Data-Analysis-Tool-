"""
Correlation & Driver Analysis.

Every statistic here is computed inside DuckDB (corr / regr_slope / regr_r2),
so nothing heavy is pulled into Python memory - important on Render's 512MB
free tier. The unit of analysis is one (keyword x product x match_type)
rollup with meaningful spend, so correlations reflect real bidding units,
not noisy single-impression rows.

Interpretations are generated from the actual numbers returned - no copy is
hardcoded to a value it didn't measure.
"""
from __future__ import annotations

import streamlit as st
import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_nan(v):
    return v is None or (isinstance(v, float) and v != v)


def _fmt_corr(v):
    if _is_nan(v):
        return "n/a"
    return f"{v:+.2f}"


def _strength(v):
    """Human label for a correlation coefficient."""
    if _is_nan(v):
        return "no data"
    a = abs(v)
    direction = "positive" if v >= 0 else "negative"
    if a >= 0.7:
        mag = "strong"
    elif a >= 0.4:
        mag = "moderate"
    elif a >= 0.2:
        mag = "weak"
    else:
        return "negligible"
    return f"{mag} {direction}"


def format_inr(value):
    if _is_nan(value):
        return "-"
    value = float(value)
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
    return f"{sign}Rs. {s}"


# The shared CTE that every query below builds on: one row per bidding unit
# (keyword x product x match_type) with a spend floor, plus derived ratios.
def _units_cte(campaigns, cities, spend_floor):
    return f"""
        WITH units AS (
            SELECT
                keyword, product_name, match_type,
                SUM(total_impressions) AS impressions,
                SUM(total_clicks)      AS clicks,
                SUM(total_budget_burnt) AS spend,
                SUM(total_a2c)         AS a2c,
                SUM(total_conversions) AS conversions,
                SUM(total_gmv)         AS gmv
            FROM granular
            WHERE metrics_date BETWEEN ? AND ?
              AND campaign_name IN ({','.join(['?'] * len(campaigns))})
              AND city IN ({','.join(['?'] * len(cities))})
              AND keyword IS NOT NULL
              AND product_name IS NOT NULL
              AND match_type IN ('KEYWORD_MATCH_TYPE_BROAD','KEYWORD_MATCH_TYPE_EXACT')
            GROUP BY keyword, product_name, match_type
            HAVING SUM(total_budget_burnt) >= {spend_floor}
        ),
        derived AS (
            SELECT *,
                CASE WHEN impressions > 0 THEN 100.0 * clicks / impressions END AS ctr,
                CASE WHEN clicks > 0 THEN 100.0 * a2c / clicks END AS a2c_rate,
                CASE WHEN clicks > 0 THEN 100.0 * conversions / clicks END AS conv_rate,
                CASE WHEN spend > 0 THEN gmv / spend END AS roi,
                CASE WHEN conversions > 0 THEN spend / conversions END AS cpa
            FROM units
        )
    """


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_correlations(db_version, start_date, end_date, campaigns, cities, spend_floor):
    try:
        con = config.connect_db()
        row = con.execute(_units_cte(campaigns, cities, spend_floor) + """
            SELECT
                count(*)                     AS n,
                corr(gmv, spend)             AS gmv_spend,
                corr(gmv, impressions)       AS gmv_impr,
                corr(gmv, clicks)            AS gmv_clicks,
                corr(gmv, a2c)               AS gmv_a2c,
                corr(conversions, clicks)    AS conv_clicks,
                corr(roi, ctr)               AS roi_ctr,
                corr(roi, a2c_rate)          AS roi_a2c_rate,
                corr(roi, conv_rate)         AS roi_conv_rate,
                corr(roi, cpa)               AS roi_cpa,
                corr(roi, spend)             AS roi_spend
            FROM derived
        """, [start_date, end_date, *campaigns, *cities]).fetchone()
        con.close()
        if not row:
            return None
        keys = ["n", "gmv_spend", "gmv_impr", "gmv_clicks", "gmv_a2c", "conv_clicks",
                "roi_ctr", "roi_a2c_rate", "roi_conv_rate", "roi_cpa", "roi_spend"]
        return dict(zip(keys, row))
    except Exception as e:
        st.error(f"Error computing correlations: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_spend_gmv_regression(db_version, start_date, end_date, campaigns, cities, spend_floor):
    try:
        con = config.connect_db()
        row = con.execute(_units_cte(campaigns, cities, spend_floor) + """
            SELECT
                count(*)                        AS n,
                regr_slope(gmv, spend)          AS slope,
                regr_intercept(gmv, spend)      AS intercept,
                regr_r2(gmv, spend)             AS r2
            FROM derived
        """, [start_date, end_date, *campaigns, *cities]).fetchone()
        con.close()
        if not row:
            return None
        return dict(zip(["n", "slope", "intercept", "r2"], row))
    except Exception as e:
        st.error(f"Error computing regression: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_diminishing_returns(db_version, start_date, end_date, campaigns, cities, spend_floor):
    try:
        con = config.connect_db()
        df = con.execute(_units_cte(campaigns, cities, spend_floor) + """
            , ranked AS (
                SELECT *, NTILE(10) OVER (ORDER BY spend) AS decile FROM derived
            )
            SELECT decile,
                   count(*)                       AS n,
                   min(spend)                     AS min_spend,
                   max(spend)                     AS max_spend,
                   sum(spend)                     AS total_spend,
                   sum(gmv)                       AS total_gmv,
                   sum(gmv) / NULLIF(sum(spend),0) AS roi
            FROM ranked
            GROUP BY decile
            ORDER BY decile
        """, [start_date, end_date, *campaigns, *cities]).fetchdf()
        con.close()
        return df
    except Exception as e:
        st.error(f"Error computing diminishing returns: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_matchtype_efficiency(db_version, start_date, end_date, campaigns, cities, spend_floor):
    try:
        con = config.connect_db()
        df = con.execute(_units_cte(campaigns, cities, spend_floor) + """
            SELECT
                CASE WHEN match_type = 'KEYWORD_MATCH_TYPE_BROAD' THEN 'Broad' ELSE 'Exact' END AS match_label,
                count(*)                        AS n,
                sum(spend)                      AS spend,
                sum(gmv)                        AS gmv,
                sum(gmv) / NULLIF(sum(spend),0) AS roi,
                regr_slope(gmv, spend)          AS slope,
                regr_r2(gmv, spend)             AS r2
            FROM derived
            GROUP BY match_label
            ORDER BY spend DESC
        """, [start_date, end_date, *campaigns, *cities]).fetchdf()
        con.close()
        return df
    except Exception as e:
        st.error(f"Error computing match-type efficiency: {str(e)}")
        return None


@st.cache_data(show_spinner=False)
def load_product_scalability(db_version, start_date, end_date, campaigns, cities, spend_floor):
    try:
        con = config.connect_db()
        df = con.execute(_units_cte(campaigns, cities, spend_floor) + """
            SELECT
                product_name,
                count(*)                        AS n,
                sum(spend)                      AS spend,
                sum(gmv)                        AS gmv,
                sum(gmv) / NULLIF(sum(spend),0) AS roi,
                regr_slope(gmv, spend)          AS slope,
                regr_r2(gmv, spend)             AS r2
            FROM derived
            GROUP BY product_name
            HAVING count(*) >= 4
            ORDER BY spend DESC
            LIMIT 25
        """, [start_date, end_date, *campaigns, *cities]).fetchdf()
        con.close()
        return df
    except Exception as e:
        st.error(f"Error computing product scalability: {str(e)}")
        return None


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(db_version, start_date, end_date, campaigns, cities):
    st.title("Correlation & Driver Analysis")
    st.markdown(
        "What actually moves the needle. Each unit below is one "
        "(keyword x product x match-type) rollup; correlations and regressions "
        "are computed inside DuckDB across those units."
    )

    floors = {"Rs. 100 (default)": 100, "Rs. 250": 250, "Rs. 500": 500, "Rs. 1,000": 1000}
    c1, _ = st.columns([1, 3])
    with c1:
        floor_label = st.selectbox("Minimum spend per unit", list(floors.keys()), index=0)
    spend_floor = floors[floor_label]
    st.caption(
        "Units below this spend are excluded so a Rs. 5 spend with one lucky "
        "sale can't distort the statistics."
    )

    st.divider()

    # ===== 1. Correlation matrix =====
    st.subheader("1. Correlation matrix")
    st.caption(
        "Pearson r between metrics across bidding units. +1 = move together, "
        "-1 = move opposite, 0 = unrelated. Correlation is association, not proof "
        "of cause."
    )
    corr = load_correlations(db_version, start_date, end_date, campaigns, cities, spend_floor)
    if not corr or not corr.get("n"):
        st.info("Not enough data at this spend floor. Lower it or widen the date range.")
        return
    n = int(corr["n"])
    if n < 8:
        st.warning(f"Only {n} bidding units clear the spend floor - correlations on so "
                   "few points are unstable. Lower the floor or widen the window.")

    pairs = [
        ("GMV  vs  Spend", corr["gmv_spend"]),
        ("GMV  vs  Impressions", corr["gmv_impr"]),
        ("GMV  vs  Clicks", corr["gmv_clicks"]),
        ("GMV  vs  Add-to-carts", corr["gmv_a2c"]),
        ("Conversions  vs  Clicks", corr["conv_clicks"]),
        ("ROI  vs  CTR", corr["roi_ctr"]),
        ("ROI  vs  A2C rate", corr["roi_a2c_rate"]),
        ("ROI  vs  Conv rate", corr["roi_conv_rate"]),
        ("ROI  vs  CPA", corr["roi_cpa"]),
        ("ROI  vs  Spend", corr["roi_spend"]),
    ]
    st.write(f"**{n} bidding units** in scope.")
    for label, v in pairs:
        st.write(f"- **{label}**:  r = {_fmt_corr(v)}  ({_strength(v)})")

    st.divider()

    # ===== 2. ROI driver ranking =====
    st.subheader("2. What drives ROI?")
    st.caption("The funnel metrics ranked by how strongly they correlate with ROI "
               "across units. The top lever is where tuning most associates with ROI.")
    drivers = [
        ("CTR (click-through rate)", corr["roi_ctr"]),
        ("A2C rate (add-to-cart)", corr["roi_a2c_rate"]),
        ("Conversion rate (click->order)", corr["roi_conv_rate"]),
        ("CPA (cost per order)", corr["roi_cpa"]),
    ]
    ranked = sorted(drivers, key=lambda x: (abs(x[1]) if not _is_nan(x[1]) else -1), reverse=True)
    for i, (label, v) in enumerate(ranked, 1):
        st.write(f"{i}. **{label}** - r = {_fmt_corr(v)} ({_strength(v)})")
    top_label, top_v = ranked[0]
    if not _is_nan(top_v) and abs(top_v) >= 0.2:
        direction = ("higher" if top_v > 0 else "lower")
        st.info(f"**Read:** across your bidding units, ROI is most associated with "
                f"**{top_label}** - units with {direction} {top_label.split(' (')[0]} "
                f"tend to have higher ROI. Prioritise levers that improve it.")
    else:
        st.info("**Read:** no single funnel metric dominates ROI here - ROI is driven "
                "more by keyword/product selection than by any one rate.")

    st.divider()

    # ===== 3. Spend -> GMV elasticity =====
    st.subheader("3. Spend -> GMV elasticity (regression)")
    st.caption("Least-squares fit of GMV on spend across units. Slope = marginal GMV "
               "per extra rupee of spend; R2 = how much of GMV variation spend explains.")
    reg = load_spend_gmv_regression(db_version, start_date, end_date, campaigns, cities, spend_floor)
    if reg and not _is_nan(reg.get("slope")):
        slope = reg["slope"]
        r2 = reg["r2"]
        colA, colB, colC = st.columns(3)
        with colA:
            st.metric("Marginal GMV per Rs.1 spend", f"Rs. {slope:.2f}")
        with colB:
            st.metric("R-squared", f"{r2:.2f}" if not _is_nan(r2) else "n/a")
        with colC:
            st.metric("Units in fit", f"{int(reg['n'])}")
        bits = []
        if slope >= 1.0:
            bits.append(f"On average, each extra Rs.1 of spend is associated with "
                        f"**Rs. {slope:.2f} of GMV** - marginal spend is still paying back "
                        f"above break-even.")
        else:
            bits.append(f"Each extra Rs.1 of spend is associated with only "
                        f"**Rs. {slope:.2f} of GMV** - at the margin, added budget is "
                        f"below break-even; growth needs better targeting, not just more spend.")
        if not _is_nan(r2):
            if r2 >= 0.6:
                bits.append(f"Spend explains **{r2*100:.0f}%** of GMV variation - budget is "
                            "a strong, predictable lever here.")
            elif r2 >= 0.3:
                bits.append(f"Spend explains **{r2*100:.0f}%** of GMV variation - a real but "
                            "partial driver; who/what you bid on matters as much as how much.")
            else:
                bits.append(f"Spend explains only **{r2*100:.0f}%** of GMV variation - GMV is "
                            "mostly driven by selection and relevance, not budget size.")
        st.info("**Read:** " + "  \n\n".join(bits))
    else:
        st.info("Not enough variation to fit a regression at this spend floor.")

    st.divider()

    # ===== 4. Diminishing returns =====
    st.subheader("4. Diminishing returns (ROI by spend decile)")
    st.caption("Units split into 10 equal groups by spend (decile 1 = lowest spenders, "
               "10 = highest). If ROI falls in the top deciles, your biggest bets are "
               "converting less efficiently - a saturation signal.")
    dr = load_diminishing_returns(db_version, start_date, end_date, campaigns, cities, spend_floor)
    if dr is not None and len(dr) > 0:
        chart_data = {}
        for _, r in dr.iterrows():
            roi_v = r["roi"] if not _is_nan(r["roi"]) else 0
            chart_data[f"D{int(r['decile'])}"] = roi_v
        try:
            st.bar_chart(chart_data)
        except Exception:
            pass
        for _, r in dr.iterrows():
            roi_str = f"{r['roi']:.2f}x" if not _is_nan(r["roi"]) else "n/a"
            st.write(f"- **Decile {int(r['decile'])}** ({int(r['n'])} units, spend "
                     f"{format_inr(r['min_spend'])}-{format_inr(r['max_spend'])}): "
                     f"total spend {format_inr(r['total_spend'])}, GMV "
                     f"{format_inr(r['total_gmv'])}, ROI **{roi_str}**")
        # Saturation read: compare top-3 deciles vs bottom-3
        top = dr[dr["decile"] >= 8]
        bot = dr[dr["decile"] <= 3]
        if len(top) and len(bot):
            top_roi = top["total_gmv"].sum() / top["total_spend"].sum() if top["total_spend"].sum() else 0
            bot_roi = bot["total_gmv"].sum() / bot["total_spend"].sum() if bot["total_spend"].sum() else 0
            if bot_roi and top_roi < bot_roi * 0.8:
                pct_less = (1 - top_roi / bot_roi) * 100
                st.warning(f"**Saturation detected:** top-spend units return {top_roi:.2f}x vs "
                           f"{bot_roi:.2f}x for low-spend units. Your biggest bets are "
                           f"{pct_less:.0f}% less efficient - consider redistributing budget "
                           "toward smaller high-ROI units.")
            elif top_roi >= bot_roi:
                st.success(f"**No saturation:** top-spend units ({top_roi:.2f}x) hold up "
                           f"vs low-spend units ({bot_roi:.2f}x) - scaling the big winners "
                           "is still working.")

    st.divider()

    # ===== 5. Broad vs Exact efficiency =====
    st.subheader("5. Broad vs Exact - which converts spend to GMV better?")
    st.caption("Same regression, split by match type. Higher slope = more GMV per rupee; "
               "higher R2 = more predictable.")
    me = load_matchtype_efficiency(db_version, start_date, end_date, campaigns, cities, spend_floor)
    if me is not None and len(me) > 0:
        for _, r in me.iterrows():
            slope_str = f"Rs. {r['slope']:.2f}" if not _is_nan(r["slope"]) else "n/a"
            r2_str = f"{r['r2']:.2f}" if not _is_nan(r["r2"]) else "n/a"
            roi_str = f"{r['roi']:.2f}x" if not _is_nan(r["roi"]) else "n/a"
            st.write(f"- **{r['match_label']}** ({int(r['n'])} units): ROI {roi_str}, "
                     f"marginal GMV/Rs.1 = {slope_str}, R2 = {r2_str}, "
                     f"spend {format_inr(r['spend'])}")
        if len(me) == 2:
            broad = me[me["match_label"] == "Broad"]
            exact = me[me["match_label"] == "Exact"]
            if len(broad) and len(exact):
                bs = broad.iloc[0]["slope"]
                es = exact.iloc[0]["slope"]
                if not _is_nan(bs) and not _is_nan(es):
                    better = "Exact" if es > bs else "Broad"
                    st.info(f"**Read:** {better} converts marginal spend to GMV more "
                            f"efficiently (slope {max(bs, es):.2f} vs {min(bs, es):.2f}). "
                            f"Shift incremental budget toward {better} where intent allows.")

    st.divider()

    # ===== 6. Product scalability =====
    st.subheader("6. Product scalability")
    st.caption("Per product: slope = marginal GMV per rupee, R2 = predictability. "
               "High slope + high R2 = scalable (spend reliably makes GMV). "
               "Low slope or low R2 = saturated or erratic - fix before funding.")
    ps = load_product_scalability(db_version, start_date, end_date, campaigns, cities, spend_floor)
    if ps is not None and len(ps) > 0:
        scalable, saturated = [], []
        for _, r in ps.iterrows():
            slope = r["slope"]
            r2 = r["r2"]
            if not _is_nan(slope) and not _is_nan(r2) and slope >= 1.0 and r2 >= 0.4:
                scalable.append(r)
            elif not _is_nan(slope) and (slope < 1.0 or (not _is_nan(r2) and r2 < 0.2)):
                saturated.append(r)

        if scalable:
            st.markdown("**Scalable (fund these - spend reliably produces GMV):**")
            for r in scalable[:12]:
                st.write(f"- **{r['product_name']}**: slope Rs. {r['slope']:.2f}, "
                         f"R2 {r['r2']:.2f}, ROI {r['roi']:.2f}x, spend {format_inr(r['spend'])}")
        if saturated:
            st.markdown("**Saturated / erratic (fix targeting before adding budget):**")
            for r in saturated[:12]:
                r2_str = f"{r['r2']:.2f}" if not _is_nan(r["r2"]) else "n/a"
                slope_str = f"Rs. {r['slope']:.2f}" if not _is_nan(r["slope"]) else "n/a"
                st.write(f"- **{r['product_name']}**: slope {slope_str}, R2 {r2_str}, "
                         f"ROI {r['roi']:.2f}x, spend {format_inr(r['spend'])}")
        if not scalable and not saturated:
            st.caption("No product cleared the classification thresholds at this spend floor.")
