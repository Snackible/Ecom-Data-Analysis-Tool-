"""Competitor, brand defence, expand-engine, keyword-served, risk and measure intents."""
import pandas as pd

from . import fmt, metrics
from .classify import contains_keyword
from .core import Context, Params, Result, empty_result, filters_from, needs_setting, num, range_text
from .helpers import loss_below_break_even, sum_by, top
from .params import previous_period
from . import q_cost


def _fin(res, rng):
    res.date_range = range_text(rng)
    return res


def _md(ctx):
    return ["needs setting: min_data (every row is shown; nothing is labelled 'too little data')"] if not ctx.min_data_set() else []


def _closeness(ctx, comp):
    close = {str(x).lower() for x in (ctx.set("competitor_closeness.close") or [])}
    far = {str(x).lower() for x in (ctx.set("competitor_closeness.far") or [])}
    c = (comp or "").lower()
    return "close" if c in close else "far" if c in far else "closeness not set"


def competitor_view(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    period = ctx.effective_period(p)
    df, rng = ctx.fetch("granular", ("keyword",), period, {k: v for k, v in filters_from(p, "granular").items() if k != "keyword"},
                        extra="keyword IS NOT NULL AND match_type NOT LIKE '%INVALID'")
    df = df[df["keyword"].map(lambda k: ctx.clf.bucket(k) == "attack")].copy()
    if df.empty:
        return _fin(Result(headline="No attack (competitor) keyword spend in this range."), rng)
    df["competitor"] = df["keyword"].map(ctx.clf.competitor)
    df["closeness"] = df["competitor"].map(lambda c: _closeness(ctx, c))
    df["conv_rate"] = [metrics.conv_rate(c, k) for c, k in zip(df["conversions"], df["clicks"])]
    g = sum_by(df, "competitor")
    g["closeness"] = g["competitor"].map(lambda c: _closeness(ctx, c))
    g["days"] = df.groupby("competitor")["days"].max().reindex(g["competitor"]).values
    res = Result()
    notes = _md(ctx)
    if ctx.set("competitor_closeness.close") is None and ctx.set("competitor_closeness.far") is None:
        notes.append("needs setting: competitor_closeness (closeness not set)")
    notes.append("data not loaded: new-to-brand (trial), so attack is judged on conversion and closeness only.")
    cols = ["competitor", "closeness", "spend", "clicks", "conv_rate", "roas"]
    g["conv_rate"] = [metrics.conv_rate(c, k) for c, k in zip(g["conversions"], g["clicks"])]
    if mode == "named":
        want = [w.lower() for w in (p.ent("keywords") + [x for x in ctx.clf.attack_terms if x in p.text.lower().replace("’", "'")])]
        sub = g[g["competitor"].str.lower().isin(want)] if want else g
        res.headline = "; ".join(f"{r.competitor}: {fmt.inr(r.spend)}, conversion {fmt.pct(r.conv_rate)}, ROAS {fmt.roas(r.roas)}" for r in sub.itertuples()) or "None of those competitors has spend."
        res.tables.append(("Attack keywords", sub[cols]))
    elif mode == "closest":
        t = top(g[num(g, "clicks") > 0], p.top_n, "conv_rate")
        res.headline = f"Best conversion among attack keywords: {t.iloc[0]['competitor']} at {fmt.pct(t.iloc[0]['conv_rate'])}; these are the closest by behaviour." if not t.empty else "No data."
        res.tables.append(("Competitors by conversion rate", t[cols]))
        notes.append("Hypothesis: high conversion suggests the competitor's shoppers want what Snackible sells. Confirm with closeness in settings.")
    else:  # keep_cut
        rows = []
        for r in g.itertuples():
            ok = ctx.enough_data(r.spend, r.clicks, r.days)
            poor = r.conv_rate is not None and g["conv_rate"].median() is not None and r.conv_rate < g["conv_rate"].median()
            if ok is False:
                a = "hold (too little data)"
            elif r.closeness == "far":
                a = "cut candidate (far from Snackible's product)"
            elif r.closeness == "close":
                a = "keep"
            elif poor:
                a = "review (below-median conversion; closeness not set)"
            else:
                a = "keep"
            rows.append(a)
        g["verdict"] = rows
        res.headline = f"{(g['verdict'].str.startswith('keep')).sum()} competitor keywords to keep, {(g['verdict'].str.startswith('cut')).sum()} cut candidates, {(g['verdict'].str.startswith('review')).sum()} to review."
        res.tables.append(("Attack keywords", top(g, p.top_n * 2, "spend")[cols + ["verdict"]]))
        res.notes.append("Fact: attack ROAS is lower by nature; cut only when far from the product or conversion is consistently poor after enough data.")
    res.notes += notes
    return _fin(res, rng)


def brand_defence(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    period = ctx.effective_period(p)
    f = {k: v for k, v in filters_from(p, "granular").items() if k != "keyword"}
    extra = "keyword IS NOT NULL AND ad_rank IS NOT NULL" + (" AND ad_property = 'Guaranteed Top Slot Ads'" if mode == "gts" else "")
    wh, args = ctx.where("granular", period, f, extra)
    d = ctx.sql("SELECT keyword, date_trunc('week', metrics_date)::DATE AS week, SUM(total_budget_burnt) spend, SUM(total_impressions) impressions, "
                "SUM(total_clicks) clicks, SUM(total_a2c) a2c, SUM(total_gmv) gmv, SUM(total_conversions) conversions, "
                "SUM(ad_rank*total_impressions)/NULLIF(SUM(total_impressions),0) AS avg_rank FROM granular" + wh + " GROUP BY 1,2", args)
    d = d[d["keyword"].map(ctx.clf.has_brand)] if not d.empty else d
    rng = ctx.con.execute("SELECT min(metrics_date), max(metrics_date) FROM granular" + wh, args).fetchone()
    if d.empty:
        return _fin(Result(headline="No brand-keyword rows with an ad rank in this range."), rng)
    w = d.groupby("week").apply(lambda x: pd.Series({
        "spend": x["spend"].sum(), "gmv": x["gmv"].sum(), "impressions": x["impressions"].sum(),
        "avg_rank": (x["avg_rank"] * x["impressions"]).sum() / x["impressions"].sum() if x["impressions"].sum() else None})).reset_index()
    w["roas"] = [metrics.roas(g, s) for g, s in zip(w["gmv"], w["spend"])]
    first, last = w["avg_rank"].iloc[0], w["avg_rank"].iloc[-1]
    holding = (last is not None and first is not None and last <= first + 1e-9)
    name = "Guaranteed Top Slot on brand searches" if mode == "gts" else "Brand defence"
    res = Result(headline=f"{name}: average ad rank {first:.2f} in the first week and {last:.2f} in the last ({'holding or improving' if holding else 'slipping'}); ROAS {fmt.roas(metrics.roas(w['gmv'].sum(), w['spend'].sum()))} on {fmt.inr(w['spend'].sum())}.")
    res.tables.append(("Brand keywords by week", w[["week", "spend", "avg_rank", "roas"]]))
    res.notes.append("Assumption: a lower ad_rank number is a better position. Rank is impression-weighted.")
    res.notes.append("Fact: exact-match brand keywords and Guaranteed Top Slot on brand searches are protected; never cut them on ROAS alone.")
    return _fin(res, rng)


def expand_engines(ctx: Context, p: Params) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    period = ctx.effective_period(p)
    df, rng = ctx.fetch("granular", ("keyword",), period, {k: v for k, v in filters_from(p, "granular").items() if k != "keyword"},
                        extra="keyword IS NOT NULL AND match_type NOT LIKE '%INVALID'")
    df = df[df["keyword"].map(lambda k: ctx.clf.bucket(k) == "expand")]
    if df.empty:
        return _fin(Result(headline="No expand keyword spend in this range."), rng)
    be = ctx.break_even()
    ref = be or metrics.roas(df["gmv"].sum(), df["spend"].sum())
    mr = ctx.set("min_roas.expand")
    ref = mr if mr is not None else ref
    keep = df[[bool(ctx.enough_data(r.spend, r.clicks, r.days)) if ctx.min_data_set() else True for r in df.itertuples()]]
    eng = top(keep[num(keep, "roas") >= ref], p.top_n, "gmv")
    burn = keep[num(keep, "roas") < ref].assign(lost=lambda x: [loss_below_break_even(s, g, be) for s, g in zip(x["spend"], x["gmv"])])
    burn = top(burn, p.top_n, "spend")
    res = Result(headline=f"{len(keep[num(keep, 'roas') >= ref])} expand keywords earn ROAS above {fmt.roas(ref)} and {len(keep[num(keep, 'roas') < ref])} fall below it.")
    res.tables.append(("Growth engines (by GMV)", eng[["keyword", "spend", "gmv", "roas"]]))
    res.tables.append(("Burning money (by spend)", burn[["keyword", "spend", "gmv", "roas"]]))
    res.notes.append("Fact: protect core category terms; prune exploratory terms that fail. Judged against " + ("min_roas.expand." if mr is not None else ("break-even." if be else "account ROAS.")))
    if be is None and mr is None:
        res.notes.append("needs setting: contribution_before_ads_pct_of_mrp_gmv or min_roas.expand")
    res.notes += _md(ctx)
    return _fin(res, rng)


def keyword_served(ctx: Context, p: Params) -> Result:
    """What does a named keyword actually serve on, and how much is off-keyword?"""
    if not ctx.has_rows("search_query"):
        return empty_result()
    kws = p.ent("keywords")
    if not kws:
        return Result(headline="Which keyword? Name it and I will show what it served on.", kind="unknown")
    df, rng = ctx.fetch("search_query", ("keyword", "match_type", "query"), ctx.effective_period(p, "search_query"),
                        {"keyword": kws})
    if df.empty:
        return _fin(Result(headline="No search query rows for that keyword."), rng)
    df = df.assign(contains=[contains_keyword(q, k) for q, k in zip(df["query"], df["keyword"])])
    tot = df["spend"].sum()
    off = df[~df["contains"]]
    res = Result(headline=f"{fmt.pct(metrics.sdiv(off['spend'].sum(), tot))} of '{kws[0]}' spend ({fmt.inr(off['spend'].sum())} of {fmt.inr(tot)}) landed on searches that do not contain it.")
    res.tables.append(("Other searches served", top(off, p.top_n, "spend")[["match_type", "query", "spend", "roas"]]))
    res.notes.append("Action: add the weakest as negatives or move the keyword to exact; check ROAS of those searches first.")
    return _fin(res, rng)


def risks_measure(ctx: Context, p: Params, mode: str) -> Result:
    from . import q_review
    cur, _ = q_cost.week_pair(ctx, p)
    if cur is None:
        return empty_result()
    acts, notes = q_review.build_actions(ctx, p, cur)
    res = Result()
    rng = (cur.start, cur.end)
    if mode == "risks":
        res.headline = "Risks if you follow the weekly actions:"
        res.notes += [
            "Cuts: a keyword or query cut as 'waste' may have been feeding later orders (attribution lag), so some GMV may be given up.",
            "Negatives and exact-match moves narrow reach; impressions on category terms can fall.",
            "Increases: marginal ROAS is usually below blended ROAS, and stock is not in the data (data not loaded: stock/fill rate).",
            "Brand defence is protected on purpose; cutting it would hand brand searches to competitors.",
            "ROAS is at MRP, so profitability is overstated.",
        ] + notes
    else:
        res.headline = "How to check next week that the changes worked:"
        res.notes += [
            "Negative or exact-match change: spend and ROAS of the keyword, and the spill share on its served searches.",
            "Reduce (lower bid): spend saved vs GMV lost on that keyword, and ad rank.",
            "Increase: marginal ROAS (extra GMV / extra spend) vs break-even after 7 days, and whether the budget is still fully spent.",
            "Test (new exact keyword): clicks, conversion rate and ROAS after enough data (min_data).",
            "Wait for the lag window before judging the latest days.",
        ] + notes[:2]
    return _fin(res, rng)
