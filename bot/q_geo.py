"""City and SKU intents (granular table)."""
import pandas as pd

from . import fmt, metrics
from .core import Context, Params, Result, data_not_loaded, empty_result, filters_from, needs_setting, num, range_text
from .helpers import add_share, lag_note, loss_below_break_even, sum_by, top
from .normalise import duplicate_city_candidates
from .params import Period, previous_period

# Display heuristic only (not a business benchmark): how far a share ratio or
# a ROAS must sit from the reference before it is flagged. Stated in answers.
SHARE_GAP = 0.20


def _fin(res, rng):
    res.date_range = range_text(rng)
    return res


def _md(ctx):
    return ["needs setting: min_data (every row is shown; nothing is labelled 'too little data')"] if not ctx.min_data_set() else []


def _be(ctx):
    return ["needs setting: contribution_before_ads_pct_of_mrp_gmv (break-even ROAS). Account ROAS is used as the reference instead"] \
        if ctx.break_even() is None else []


def _keep(ctx, df):
    if df.empty or not ctx.min_data_set():
        return df
    return df[[bool(ctx.enough_data(r.spend, r.clicks, r.days)) for r in df.itertuples()]]


def _ref_roas(ctx, df):
    """(reference ROAS, label): break-even if set, else the account ROAS."""
    be = ctx.break_even()
    if be:
        return be, "break-even"
    return metrics.roas(df["gmv"].sum(), df["spend"].sum()), "account ROAS"


def _latest_week(ctx, p, table="granular"):
    if p.period is not None:
        return p.period
    lo, hi = ctx.bounds(table)
    if lo is None:
        return None
    from datetime import timedelta
    return Period(max(lo, hi - timedelta(days=6)), hi, "latest 7 days")


def _dup_note(ctx):
    d = duplicate_city_candidates(ctx.catalog().cities)
    return [f"Possible duplicate city names (not merged): {', '.join(' / '.join(g) for g in d[:5])}"] if d else []


def _city_df(ctx, p, period=None):
    f = filters_from(p, "granular")
    f.pop("city", None)
    return ctx.fetch("granular", ("city",), period or ctx.effective_period(p), f)


def _shares(df):
    df = add_share(add_share(df.copy(), "spend", "spend_share"), "gmv", "gmv_share")
    df["share_index"] = [metrics.sdiv(g, s) for g, s in zip(df["gmv_share"], df["spend_share"])]
    return df


def city_view(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    df, rng = _city_df(ctx, p)
    if df.empty:
        return empty_result()
    df = _shares(df)
    res = Result()
    n = p.top_n
    cols = ["city", "spend", "spend_share", "gmv", "gmv_share", "roas", "cpm"]
    notes = _dup_note(ctx)

    if mode in ("spend", "merged"):
        t = top(df, n, "spend")
        r0 = t.iloc[0]
        res.headline = f"{r0['city']} takes the most budget: {fmt.inr(r0['spend'])}, {fmt.pct(r0['spend_share'])} of spend, at ROAS {fmt.roas(r0['roas'])}."
        res.tables.append(("Cities by spend", t[cols]))
        t2 = df[df["gmv"] > r0["gmv"]]
        if not t2.empty:
            b = top(t2, 1, "gmv").iloc[0]
            res.notes.append(f"Insight: {b['city']} earns more GMV ({fmt.inr(b['gmv'])}) than {r0['city']}.")
        res.notes.append("Action: check whether the best-ROAS city is held back by budget or stock before moving money (stock not in data).")
        if mode == "merged":
            res.notes.append("Fact: 'Noida' and 'Noida 1' are merged into 'Noida' in every city view.")
    elif mode == "gmv":
        t = top(df, n, "gmv")
        r0 = t.iloc[0]
        res.headline = f"{r0['city']} gives the highest ads revenue: {fmt.inr(r0['gmv'])} GMV (at MRP), {fmt.pct(r0['gmv_share'])} of total, ROAS {fmt.roas(r0['roas'])}."
        res.tables.append(("Cities by GMV", t[cols]))
        res.notes.append("Fact: ROAS is at MRP, so it overstates real revenue.")
    elif mode == "rank":
        ref, label = _ref_roas(ctx, df)
        t = top(_keep(ctx, df), n, "spend")
        below = df[num(df, "roas") < ref]
        res.headline = f"{len(below)} of {len(df)} cities are below {label} ({fmt.roas(ref)}); {len(df[num(df, 'roas') >= ref])} are above."
        t = t.assign(vs_ref=[("below" if (r or 0) < ref else "above") for r in t["roas"]])
        res.tables.append((f"Cities by spend (reference: {label})", t[cols + ["vs_ref"]]))
        notes += _be(ctx) + _md(ctx)
    elif mode in ("high_spend_weak", "strong_low_spend"):
        ref, label = _ref_roas(ctx, df)
        med = num(df, "spend").median()
        d = _keep(ctx, df)
        if mode == "high_spend_weak":
            sub = d[(num(d, "spend") >= med) & (num(d, "roas") < ref)]
            sub = top(sub, n, "spend")
            res.headline = f"{len(sub)} cities spend above the median with ROAS below {label} ({fmt.roas(ref)})." if not sub.empty else f"No high-spend city is below {label}."
            res.notes.append("Action: reduce or hold their budget; check availability first (not in data).")
        else:
            sub = d[(num(d, "spend") < med) & (num(d, "roas") >= ref)]
            sub = top(sub, n, "roas")
            res.headline = f"{len(sub)} cities earn ROAS above {label} ({fmt.roas(ref)}) on below-median spend." if not sub.empty else "No under-funded city stands out."
            res.notes.append("Action: test a step up in budget (see budget_increase_step_pct). Depends on stock, not in data.")
        res.tables.append(("Cities", sub[cols]))
        notes += _be(ctx) + _md(ctx)
    elif mode == "share_check":
        want = p.ent("cities") or ctx.set("priority_cities") or []
        if not want:
            want = list(top(df, 2, "spend")["city"])
            notes.append("No cities named and settings.priority_cities is empty, so the two biggest cities by spend are shown.")
        sub = df[df["city"].str.lower().isin([w.lower() for w in want])]
        flags = []
        for r in sub.itertuples():
            ix = r.share_index
            flags.append("overfunded (spend share above GMV share)" if ix is not None and ix < 1 - SHARE_GAP
                         else "underfunded (GMV share above spend share)" if ix is not None and ix > 1 + SHARE_GAP else "about right")
        sub = sub.assign(verdict=flags)
        res.headline = "; ".join(f"{r.city}: {fmt.pct(r.spend_share)} of spend, {fmt.pct(r.gmv_share)} of GMV ({v})" for r, v in zip(sub.itertuples(), flags)) or "Those cities are not in the data."
        res.tables.append(("Share of spend vs share of GMV", sub[cols + ["share_index", "verdict"]]))
        res.notes.append(f"Assumption: more than {int(SHARE_GAP * 100)}% gap between GMV share and spend share counts as over or under funded.")
    elif mode == "overunder":
        d = _keep(ctx, df)
        over = top(d[num(d, "share_index") < 1 - SHARE_GAP], n, "spend")
        under = top(d[num(d, "share_index") > 1 + SHARE_GAP], n, "gmv")
        res.headline = f"{len(over)} overfunded and {len(under)} underfunded cities (GMV share vs spend share gap above {int(SHARE_GAP * 100)}%)."
        res.tables.append(("Overfunded", over[cols + ["share_index"]]))
        res.tables.append(("Underfunded", under[cols + ["share_index"]]))
    elif mode == "cpm_vs_avg":
        acc = metrics.cpm(df["spend"].sum(), df["impressions"].sum())
        d = _keep(ctx, df).copy()
        d["cpm_vs_account"] = [metrics.sdiv(c, acc) for c in d["cpm"]]
        sub = top(d, n, "cpm_vs_account")
        res.headline = f"Account CPM is {fmt.inr(acc, 2)} per 1,000 impressions; highest city is {sub.iloc[0]['city']} at {fmt.inr(sub.iloc[0]['cpm'], 2)}." if not sub.empty else "No data."
        res.tables.append(("Cities by CPM vs account", sub[["city", "cpm", "cpm_vs_account", "spend", "roas"]]))
        res.notes.append("Fact: CPM is compared across cities within the same data, mixing placements. Use the placement view for like-for-like.")
    elif mode == "keyword_waste":
        cities = p.ent("cities")
        if not cities:
            return _fin(Result(headline="Which city? Name it and I will list its wasting keywords.", kind="unknown"), rng)
        k, rng = ctx.fetch("granular", ("keyword",), ctx.effective_period(p), {"city": cities},
                           extra="keyword IS NOT NULL")
        k = k.copy()
        be = ctx.break_even()
        k["lost"] = [loss_below_break_even(s, g, be) for s, g in zip(k["spend"], k["gmv"])]
        k = top(_keep(ctx, k), n, "lost")
        res.headline = f"In {cities[0]}, {fmt.inr(k['lost'].sum())} of keyword spend sits below {'break-even' if be else 'ROAS 1.00'}; worst is '{k.iloc[0]['keyword']}'." if not k.empty else f"No keyword wastes money in {cities[0]}."
        res.tables.append((f"Wasteful keywords in {cities[0]}", k[["keyword", "spend", "clicks", "roas", "lost"]]))
        res.notes.append("Fact: search query x city is not available in any report; this is keyword x city.")
        notes += _md(ctx)
    elif mode == "worse":
        cur = _latest_week(ctx, p)
        prev = previous_period(cur)
        a, rng = _city_df(ctx, p, cur)
        b, _ = _city_df(ctx, p, prev)
        if a.empty or b.empty:
            return _fin(Result(headline="No earlier period to compare."), rng)
        m = a.merge(b, on="city", suffixes=("", "_prev"))
        m["roas_change"] = [metrics.growth(x, y) for x, y in zip(num(m, "roas"), num(m, "roas_prev"))]
        m["cpm_change"] = [metrics.growth(x, y) for x, y in zip(num(m, "cpm"), num(m, "cpm_prev"))]
        m["ctr_change"] = [metrics.growth(x, y) for x, y in zip(num(m, "ctr"), num(m, "ctr_prev"))]
        m["conv_change"] = [metrics.growth(metrics.conv_rate(c, k), metrics.conv_rate(c2, k2)) for c, k, c2, k2 in zip(m["conversions"], m["clicks"], m["conversions_prev"], m["clicks_prev"])]
        m = _keep(ctx, m)
        w = top(m[num(m, "roas_change") < 0], n, "roas_change", asc=True)
        res.headline = (f"{len(m[num(m, 'roas_change') < 0])} cities got worse versus {prev.start} to {prev.end}; worst is {w.iloc[0]['city']} "
                        f"(ROAS {fmt.signed_pct(w.iloc[0]['roas_change'])}).") if not w.empty else "No city got worse on ROAS."
        res.tables.append(("Cities with falling ROAS (cost = CPM change, click-through, conversion)", w[["city", "spend", "roas", "roas_change", "cpm_change", "ctr_change", "conv_change"]]))
        res.notes.append("data not loaded: stock/fill rate, so availability cannot be ruled in or out as the cause.")
        res.notes += lag_note(ctx, rng)
        notes += _md(ctx)
    elif mode == "tier":
        tiers = ctx.set("city_tiers")
        if not tiers:
            r = needs_setting("city_tiers", "Showing ROAS by city instead.")
            r.tables.append(("Cities by ROAS", top(df, n, "roas")[cols]))
            return _fin(r, rng)
        d = df.assign(tier=df["city"].map(lambda c: tiers.get(c, "(no tier)")))
        out = sum_by(d, "tier")
        res.headline = "ROAS by tier: " + ", ".join(f"{r.tier} {fmt.roas(r.roas)}" for r in out.itertuples()) + "."
        res.tables.append(("ROAS by city tier", out[["tier", "spend", "gmv", "roas"]]))
    elif mode == "action":
        be = ctx.break_even()
        if be is None:
            return _fin(needs_setting("contribution_before_ads_pct_of_mrp_gmv", "Cut/hold/scale needs break-even ROAS."), rng)
        d = df.copy()
        sr, ss = ctx.set("scale_rule.roas_at_least"), ctx.set("scale_rule.spend_inr")
        acts = []
        for r in d.itertuples():
            ok = ctx.enough_data(r.spend, r.clicks, r.days)
            if ok is False:
                acts.append("hold (too little data)")
            elif (r.roas or 0) < be:
                acts.append("reduce")
            elif sr is not None and ss is not None and (r.roas or 0) >= sr and r.spend >= ss:
                acts.append("scale (depends on stock, not in data)")
            else:
                acts.append("hold")
        d["action"] = acts
        res.headline = "Cut / hold / scale: " + ", ".join(f"{k} {v}" for k, v in d["action"].str.split(" ").str[0].value_counts().items()) + "."
        res.tables.append(("City actions", top(d, n * 2, "spend")[["city", "spend", "roas", "action"]]))
        if sr is None or ss is None:
            notes.append("needs setting: scale_rule (no city is marked scale)")
        notes += _md(ctx)
        res.notes.append("Action: confirm stock in each city before any increase.")
    res.notes += notes
    return _fin(res, rng)


# --------------------------------------------------------------------------
def _sku_df(ctx, p, dims, period=None, extra=""):
    f = filters_from(p, "granular")
    return ctx.fetch("granular", dims, period or ctx.effective_period(p), f,
                     extra=("product_name IS NOT NULL" + (" AND " + extra if extra else "")))


def _no_sku_share(ctx, p):
    allt, rng = ctx.totals("granular", ctx.effective_period(p), {})
    nosku, _ = ctx.totals("granular", ctx.effective_period(p), {}, extra="product_name IS NULL")
    sh = metrics.sdiv(nosku.get("spend"), allt.get("spend")) if allt else None
    return sh, nosku.get("spend")


def sku_view(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    n = p.top_n
    notes = []
    sh, ns = _no_sku_share(ctx, p)
    if sh:
        notes.append(f"Fact: {fmt.pct(sh)} of spend ({fmt.inr(ns)}) is on placements with no SKU (Collection Ads, Auto Suggest, Search Inline Banner) and is not in this view.")
    if ctx.set("product_title_to_sku") is None:
        notes.append("needs setting: product_title_to_sku (raw product names are shown, truncated to 40 characters)")
    res = Result()
    if mode in ("rank", "heavy_weak", "strong_room", "ctr_conv", "more_spend"):
        df, rng = _sku_df(ctx, p, ("product",))
        if df.empty:
            return empty_result()
        df = sum_by(df, "product")  # merge products that share a short name
        df = _keep(ctx, df.assign(days=ctx.con.execute("select count(distinct metrics_date) from granular").fetchone()[0]))
        df = add_share(df, "spend", "spend_share")
        cols = ["product", "spend", "spend_share", "clicks", "gmv", "roas", "ctr"]
        ref, label = _ref_roas(ctx, df) if not df.empty else (None, "")
        if mode == "rank":
            t = top(df, n, "spend")
            res.headline = f"{t.iloc[0]['product']} gets the most spend: {fmt.inr(t.iloc[0]['spend'])} at ROAS {fmt.roas(t.iloc[0]['roas'])}." if not t.empty else "No SKU data."
            res.tables.append(("SKUs by spend", t[cols]))
        elif mode == "heavy_weak":
            med = num(df, "spend").median()
            t = top(df[(num(df, "spend") >= med) & (num(df, "roas") < ref)], n, "spend")
            res.headline = f"{len(t)} SKUs get above-median spend at ROAS below {label} ({fmt.roas(ref)})." if not t.empty else f"No heavily funded SKU is below {label}."
            res.tables.append(("Heavy spend, weak ROAS", t[cols]))
            notes += _be(ctx)
        elif mode in ("strong_room", "more_spend"):
            sr = ctx.set("scale_rule.roas_at_least")
            thr = sr or ref
            t = top(df[num(df, "roas") >= thr], n, "roas")
            res.headline = f"{len(t)} SKUs earn ROAS {fmt.roas(thr)} or better." if not t.empty else "No SKU clears the bar."
            res.tables.append(("Strong ROAS SKUs", t[cols]))
            res.notes.append("data not loaded: stock/fill rate. Any increase depends on stock, not in data; stock in transit never counts.")
            if sr is None:
                notes.append("needs setting: scale_rule.roas_at_least (break-even or account ROAS used instead)")
            notes += _be(ctx)
        else:  # ctr_conv
            d = df.assign(conv_rate=[metrics.conv_rate(c, k) for c, k in zip(df["conversions"], df["clicks"])])
            if len(d) < 4:
                t = d
            else:
                t = d[(num(d, "ctr") >= num(d, "ctr").median()) & (num(d, "conv_rate") <= num(d, "conv_rate").median())]
            t = top(t, n, "ctr")
            res.headline = f"{len(t)} SKUs have above-median CTR but below-median conversion (possible price, pack size or listing issue)."
            res.tables.append(("High CTR, low conversion", t[["product", "ctr", "conv_rate", "clicks", "spend", "roas"]]))
            res.notes.append("Hypothesis: check price, pack size and listing for these. Medians are within this account, not a benchmark.")
        notes += _md(ctx)
    elif mode in ("wasteful_city", "grid"):
        df, rng = _sku_df(ctx, p, ("product", "city") if mode == "wasteful_city" else ("product", "city", "match_type"),
                          extra="match_type NOT LIKE '%INVALID'" if mode == "grid" else "")
        if df.empty:
            return empty_result()
        if mode == "wasteful_city":
            be = ctx.break_even()
            df = df.copy()
            df["lost"] = [loss_below_break_even(s, g, be) for s, g in zip(df["spend"], df["gmv"])]
            t = top(_keep(ctx, df), n, "lost")
            res.headline = f"Most wasteful SKU x city: {t.iloc[0]['product']} in {t.iloc[0]['city']} ({fmt.inr(t.iloc[0]['lost'])} below {'break-even' if be else 'ROAS 1.00'})." if not t.empty and t.iloc[0]["lost"] > 0 else "No SKU x city combination spends below the floor."
            res.tables.append(("SKU x city, worst first", t[["product", "city", "spend", "roas", "lost"]]))
            notes += _be(ctx)[:1]
        else:
            tops = list(top(sum_by(df, "product"), 5, "spend")["product"])
            t = df[df["product"].isin(tops)].sort_values(["product", "spend"], ascending=[True, False])
            res.headline = f"SKU x city x match type grid for the top {len(tops)} SKUs by spend ({len(t)} rows, top 40 shown)."
            res.tables.append(("Grid", t.head(40)[["product", "city", "match_type", "spend", "clicks", "gmv", "roas"]]))
        notes += _md(ctx)
    elif mode == "drivers":
        prods = p.ent("products")
        if not prods:
            return Result(headline="Which SKU? Name it and I will show its searches and cities.", kind="unknown")
        c, rng = ctx.fetch("granular", ("city",), ctx.effective_period(p), {"product": prods})
        q, _ = ctx.fetch("search_query", ("query",), ctx.effective_period(p, "search_query"), {"product": prods}) if ctx.has_rows("search_query") else (pd.DataFrame(), None)
        res.headline = f"{prods[0]}: top city {c.iloc[0]['city']} ({fmt.inr(c.iloc[0]['spend'])}, ROAS {fmt.roas(c.iloc[0]['roas'])})" + (f"; top search '{q.iloc[0]['query']}' ({fmt.inr(q.iloc[0]['spend'])})." if not q.empty else ".") if not c.empty else "No rows for that SKU."
        res.tables.append(("Cities", top(c, n, "spend")[["city", "spend", "gmv", "roas"]]))
        if not q.empty:
            res.tables.append(("Search queries", top(q, n, "gmv")[["query", "spend", "gmv", "roas"]]))
        res.notes.append("Fact: query and city views are separate; search query x city is not available.")
    elif mode == "mismatch":
        kw = ctx.set("sku_keywords")
        if not kw:
            return needs_setting("sku_keywords", "Tell me the key words of each SKU's short name and I will flag searches that match none of them.")
        q, rng = ctx.fetch("search_query", ("product", "query"), ctx.effective_period(p, "search_query"), filters_from(p, "search_query"), extra="product_name IS NOT NULL")
        q = q.copy()
        words = {str(k).lower(): [w.lower() for w in v] for k, v in kw.items()}

        def hit(prod, qq):
            w = words.get(str(prod).lower())
            return None if w is None else any(x in qq for x in w)
        q["match"] = [hit(a, b) for a, b in zip(q["product"], q["query"])]
        bad = q[q["match"] == False]  # noqa: E712
        res.headline = f"{fmt.inr(bad['spend'].sum())} ({fmt.pct(metrics.sdiv(bad['spend'].sum(), q['spend'].sum()))}) of SKU search spend served on queries matching none of the SKU's words."
        res.tables.append(("SKU served on non-matching searches", top(bad, n, "spend")[["product", "query", "spend", "roas"]]))
    elif mode == "cannibal":
        q, rng = ctx.fetch("search_query", ("query", "product"), ctx.effective_period(p, "search_query"), None, extra="product_name IS NOT NULL")
        if q.empty:
            return empty_result()
        multi = q.groupby("query")["product"].nunique()
        both = q[q["query"].isin(multi[multi > 1].index)]
        t = top(both.groupby("query")[["spend", "gmv"]].sum().reset_index(), n, "spend")
        res.headline = f"{len(multi[multi > 1])} search queries are served by more than one SKU; possible overlap."
        res.tables.append(("Queries shared by several SKUs", t))
        res.notes.append("Hypothesis only: the data cannot prove one SKU takes sales from another. Test by pausing one SKU on the shared queries for a week.")
    res.notes += notes
    return _fin(res, rng)


def sku_city_keyword(ctx: Context, p: Params, mode: str = "best") -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    be = ctx.break_even()
    n = p.top_n if p.explicit_n else 10
    df, rng = ctx.fetch("granular", ("product", "city", "keyword"), ctx.effective_period(p), filters_from(p, "granular"),
                        extra="product_name IS NOT NULL AND ad_property = 'Keyword Based Ads' AND keyword IS NOT NULL")
    if df.empty:
        return empty_result()
    df = _keep(ctx, df)
    res = Result()
    ref, label = _ref_roas(ctx, df) if not df.empty else (be, "break-even")
    notes = _be(ctx) + _md(ctx)
    if ctx.set("product_title_to_sku") is None:
        notes.append("needs setting: product_title_to_sku (raw product names are shown, truncated to 40 characters)")
    if mode == "city_campaign":
        prods = p.ent("products")
        d = df if not prods else df[df["product"].isin([ctx.sku_map and ctx.sku_map.get(x, x) or x[:39] + ("~" if len(x) > 40 else "") for x in prods]) | df["product"].str.lower().isin([x.lower() for x in prods])]
        sc = sum_by(d, ["product", "city"])
        avg = sum_by(d, "product").set_index("product")["roas"]
        sc["sku_avg_roas"] = sc["product"].map(avg)
        sc["vs_avg"] = [metrics.growth(a, b) for a, b in zip(num(sc, "roas"), num(sc, "sku_avg_roas"))]
        pick = sc[(num(sc, "vs_avg").abs() >= SHARE_GAP)]
        pick = top(pick, n, "spend")
        if not ctx.min_data_set():
            return _fin(needs_setting("min_data", "A city-wise campaign needs enough volume per SKU x city."), rng)
        res.headline = (f"{len(pick)} SKU x city combinations have enough volume and ROAS at least {int(SHARE_GAP * 100)}% away from the SKU average; "
                        "a city-wise campaign is only worth it for these.") if not pick.empty else "No SKU x city combination is clearly different from its SKU average with enough volume. Not recommended."
        res.tables.append(("SKU x city vs SKU average", pick[["product", "city", "spend", "roas", "sku_avg_roas", "vs_avg"]]))
        res.notes.append("Setup if you proceed: one campaign per city and SKU, same keywords, starting budget from current spend. Success test: 2 weeks, ROAS vs the SKU average. Extra workload and it splits data.")
        res.notes.append(f"Assumption: 'clearly different' means {int(SHARE_GAP * 100)}% or more from the SKU average ROAS.")
        res.notes += notes
        return _fin(res, rng)
    win = top(df[num(df, "roas") >= ref], n, "gmv")
    fix = df[num(df, "roas") < ref]
    fix = top(fix, n, "spend")
    # cause: compare each fix row to the median of the whole set
    med = {c: num(df, c).median() for c in ("impressions", "ctr", "cpm")}
    med["conv"] = pd.Series([metrics.conv_rate(c, k) for c, k in zip(df["conversions"], df["clicks"])], dtype="float64").median()
    causes = []
    for r in fix.itertuples():
        dev = {
            "visibility (low impressions)": metrics.growth(r.impressions, med["impressions"]),
            "engagement (low CTR)": metrics.growth(r.ctr, med["ctr"]),
            "conversion (low conversion rate)": metrics.growth(metrics.conv_rate(r.conversions, r.clicks), med["conv"]),
            "cost (high CPM)": (-metrics.growth(r.cpm, med["cpm"])) if metrics.growth(r.cpm, med["cpm"]) is not None else None,
        }
        dev = {k: v for k, v in dev.items() if v is not None}
        causes.append(min(dev, key=dev.get) if dev else "unclear")
    fix = fix.assign(cause=causes)
    res.headline = (f"Best: {win.iloc[0]['product']} in {win.iloc[0]['city']} on '{win.iloc[0]['keyword']}' "
                    f"({fmt.inr(win.iloc[0]['gmv'])} GMV, ROAS {fmt.roas(win.iloc[0]['roas'])}). "
                    f"{len(fix)} combinations to fix, led by spend of {fmt.inr(fix['spend'].sum())}." if not win.empty and not fix.empty
                    else (f"Best: {win.iloc[0]['product']} in {win.iloc[0]['city']} on '{win.iloc[0]['keyword']}'." if not win.empty
                          else "No SKU x city x keyword clears the bar."))
    c = ["product", "city", "keyword", "spend", "impressions", "ctr", "cpm", "gmv", "roas"]
    res.tables.append((f"Winners: top {len(win)} by GMV above {label} ({fmt.roas(ref)})", win[c]))
    res.tables.append((f"Fix list: top {len(fix)} by spend below {label}", fix[c + ["cause"]]))
    res.notes.append("Fact: keyword ads only, and only rows with a SKU. Cause is the metric furthest below this account's own median.")
    res.notes += notes
    return _fin(res, rng)
