"""Search-query, match-type and attack/defend/expand intents."""
import pandas as pd

from . import fmt, metrics
from .classify import contains_keyword
from .core import (Context, Params, Result, data_not_loaded, empty_result, filters_from, needs_setting,
                   num, range_text)
from .helpers import add_share, date_line, lag_note, loss_below_break_even, sum_by, top
from .params import previous_period

SQ_COLS = ["spend", "impressions", "clicks", "ctr", "cpm", "gmv", "roas"]


def _finish(res: Result, rng) -> Result:
    res.date_range = range_text(rng)
    return res


def _sq(ctx, p, dims, extra="", order="spend DESC", period=None):
    return ctx.fetch("search_query", dims, period or ctx.effective_period(p, "search_query"),
                     filters_from(p, "search_query"), extra=extra, order=order)


def _md_note(ctx, df_in) -> list:
    if ctx.enough_data() is None:
        return ["needs setting: min_data (every row is shown; nothing is labelled 'too little data')"]
    return []


def _apply_min_data(ctx, df):
    if df.empty or not ctx.min_data_set():
        return df
    keep = [bool(ctx.enough_data(r.spend, r.clicks, r.days)) for r in df.itertuples()]
    return df[keep]


def _be_note(ctx) -> list:
    if ctx.break_even() is None:
        return ["needs setting: contribution_before_ads_pct_of_mrp_gmv (break-even ROAS). Using ROAS 1.00 as the floor instead"]
    return []


def _waste_mask(ctx, df):
    """Boolean Series per settings.waste_rule, or None when the rule is unset."""
    s, r, d, z = (ctx.set(f"waste_rule.{k}") for k in ("spend_inr", "roas_below", "over_days", "zero_gmv_spend_inr"))
    a = None
    if s is not None and r is not None:
        a = (num(df, "spend") >= s) & (num(df, "roas") < r)
        if d is not None:
            a &= num(df, "days") >= d
    b = None
    if z is not None:
        b = (num(df, "spend") >= z) & (num(df, "gmv").fillna(0) == 0)
    if a is None and b is None:
        return None
    m = pd.Series(False, index=df.index)
    for x in (a, b):
        if x is not None:
            m |= x.fillna(False)
    return m


def _fix_for(row_kw_match, spill, bucket):
    if row_kw_match == "Broad" and spill:
        return "add as negative, or lower the bid"
    if row_kw_match == "Broad":
        return "move to exact"
    return "lower the bid"


# --------------------------------------------------------------------------
def query_waste(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("search_query"):
        return empty_result()
    be = ctx.break_even()
    floor = be or 1.0
    notes = []
    n = p.top_n if p.explicit_n else (20 if mode == "top_wasteful" else 10)

    if mode == "drag":
        return _drag(ctx, p, n)

    dims = ("campaign", "query") if mode == "top_wasteful" else ("query",)
    df, rng = _sq(ctx, p, dims)
    if df.empty:
        return empty_result()
    df = _apply_min_data(ctx, df)
    notes += _md_note(ctx, df)
    res = Result()

    if mode == "below1":
        sub = df[(num(df, "roas") < 1) & (num(df, "spend") > 0)]
        sub = top(sub, n, "spend")
        res.headline = (f"{len(df[(num(df, 'roas') < 1) & (num(df, 'spend') > 0)])} search queries have ROAS below 1.00; "
                        f"the biggest is '{sub.iloc[0]['query']}' at {fmt.inr(sub.iloc[0]['spend'])} (ROAS {fmt.roas(sub.iloc[0]['roas'])})."
                        if not sub.empty else "No search query has ROAS below 1.00 in this range.")
        res.tables.append(("Queries below ROAS 1.00, by spend", sub[["query"] + SQ_COLS]))
        if not sub.empty:
            res.notes.append(f"Together these spent {fmt.inr(sub['spend'].sum())} for {fmt.inr(sub['gmv'].sum())} GMV (at MRP).")
            res.notes.append("Action: add the high-spend ones as negatives or lower the bid on the keyword serving them.")
    elif mode == "zero_gmv":
        z = ctx.set("waste_rule.zero_gmv_spend_inr")
        sub = df[(num(df, "gmv").fillna(0) == 0) & (num(df, "spend") > (z or 0))]
        sub = top(sub, n, "spend")
        res.headline = (f"{len(df[(num(df, 'gmv').fillna(0) == 0) & (num(df, 'spend') > (z or 0))])} queries spent money with zero GMV, "
                        f"{fmt.inr(sub['spend'].sum())} in the top {len(sub)}.") if not sub.empty \
            else "No search query spent money with zero GMV."
        res.tables.append(("Zero-GMV queries, by spend", sub[["query", "spend", "impressions", "clicks", "ctr", "cpm"]]))
        if z is None:
            notes.append("needs setting: waste_rule.zero_gmv_spend_inr (any spend above 0 is shown)")
    elif mode == "top_wasteful":
        df = df.copy()
        df["lost"] = [loss_below_break_even(s, g, be) for s, g in zip(df["spend"], df["gmv"])]
        df = df[num(df, "lost") > 0]
        sub = top(df, n, "lost")
        if sub.empty:
            return _finish(Result(headline=f"No search query spends below {'break-even' if be else 'ROAS 1.00'} in this range.",
                                  notes=_be_note(ctx)), rng)
        res.headline = (f"Top {len(sub)} wasteful search queries lose {fmt.inr(sub['lost'].sum())} against "
                        f"{'break-even' if be else 'ROAS 1.00'}; worst is '{sub.iloc[0]['query']}' "
                        f"in {sub.iloc[0]['campaign']} ({fmt.inr(sub.iloc[0]['lost'])}).") if not sub.empty else "Nothing wasteful found."
        res.tables.append(("Wasteful queries", sub[["query", "campaign", "spend", "clicks", "roas", "lost"]]))
        notes += _be_note(ctx)
        res.notes.append("Action: negative or exact-match move per query; check the match type of the serving keyword first.")
    elif mode == "irrelevant":
        d2, rng = _sq(ctx, p, ("keyword", "match_type", "query"))
        d2 = d2[d2["match_type"].isin(["Broad"])].copy()
        d2["contains_keyword"] = [contains_keyword(q, k) for q, k in zip(d2["query"], d2["keyword"])]
        sub = d2[~d2["contains_keyword"]].copy()
        sub["lost"] = [loss_below_break_even(s, g, be) for s, g in zip(sub["spend"], sub["gmv"])]
        sub = top(sub, n, "lost")
        res.headline = (f"{fmt.inr(d2[~d2['contains_keyword']]['spend'].sum())} of broad-match spend "
                        f"({fmt.pct(metrics.sdiv(d2[~d2['contains_keyword']]['spend'].sum(), d2['spend'].sum()))}) landed on searches "
                        "that do not contain the bid keyword.")
        res.tables.append(("Searches that do not contain the keyword (worst first)", sub[["keyword", "query", "spend", "roas", "lost"]]))
        if ctx.set("sku_keywords") is None:
            notes.append("needs setting: sku_keywords (without it, 'irrelevant' means 'does not contain the bid keyword', a weaker test)")
        notes += _be_note(ctx)
    elif mode == "high_ctr_poor_conv":
        d = df[(num(df, "clicks") > 0)].copy()
        d = d[num(d, "impressions") > 0]
        if len(d) < 8:
            return _finish(Result(headline="Too little data to compare queries.", kind="ok"), rng)
        hi = num(d, "ctr").quantile(0.75)
        lo = num(d.assign(cr=[metrics.conv_rate(c, k) for c, k in zip(d["conversions"], d["clicks"])]), "cr")
        d["conv_rate"] = [metrics.conv_rate(c, k) for c, k in zip(d["conversions"], d["clicks"])]
        lo_q = num(d, "conv_rate").quantile(0.25)
        sub = d[(num(d, "ctr") >= hi) & (num(d, "conv_rate") <= lo_q)]
        sub = top(sub, n, "spend")
        res.headline = f"{len(sub)} queries have top-quartile CTR but bottom-quartile conversion."
        res.tables.append(("High CTR, poor conversion", sub[["query", "spend", "clicks", "ctr", "conv_rate", "roas"]]))
        notes.append("Fact: 'high' and 'poor' are relative to this account's own distribution (top and bottom quartile), not an external benchmark.")
    elif mode == "good_low_spend":
        thresh = ctx.set("scale_rule.roas_at_least") or floor
        good = df[num(df, "roas") >= thresh]
        med = num(df, "spend").median()
        sub = top(good[num(good, "spend") <= med], n, "roas")
        res.headline = (f"{len(sub)} queries convert at ROAS {fmt.roas(thresh)} or better on below-median spend.")
        res.tables.append(("Candidates for more budget", sub[["query", "spend", "clicks", "conv_rate" if "conv_rate" in sub else "ctr", "gmv", "roas"]]))
        res.notes.append("Action: give the best ones their own exact-match keyword. Any scale-up depends on stock, not in data.")
        notes += _be_note(ctx)
    elif mode in ("negatives", "saving"):
        mask = _waste_mask(ctx, df)
        if mask is None:
            cand = df.copy()
            cand["lost"] = [loss_below_break_even(s, g, be) for s, g in zip(cand["spend"], cand["gmv"])]
            sub = top(cand, n, "lost")
            res.headline = "needs setting: waste_rule. Showing the biggest losses against break-even instead."
            res.kind = "needs_setting"
            res.tables.append(("Largest losses (no waste rule applied)", sub[["query", "spend", "gmv", "roas", "lost"]]))
            return _finish(res, rng)
        sub = df[mask].copy()
        sub["lost"] = [loss_below_break_even(s, g, be) for s, g in zip(sub["spend"], sub["gmv"])]
        sub = top(sub, 1000, "spend")
        if mode == "saving":
            res.headline = (f"Cutting all {len(sub)} queries that hit the waste rule saves {fmt.inr(sub['spend'].sum())} of spend "
                            f"and gives up {fmt.inr(sub['gmv'].sum())} GMV (at MRP).")
            res.tables.append(("Largest waste-rule queries", sub.head(n)[["query", "spend", "gmv", "roas"]]))
        else:
            res.headline = f"{len(sub)} queries hit the waste rule; add these as negatives first."
            res.tables.append(("Negative keyword candidates", sub.head(n)[["query", "spend", "clicks", "roas"]]))
            res.notes.append("Action: add as negatives at campaign level. Confirm Instamart allows negatives at that level.")
    elif mode == "high_cpm":
        d = df[num(df, "impressions") > 0]
        sub = top(d, n, "cpm")
        res.headline = f"Highest CPM query is '{sub.iloc[0]['query']}' at {fmt.inr(sub.iloc[0]['cpm'], 2)} per 1,000 impressions (ROAS {fmt.roas(sub.iloc[0]['roas'])})." if not sub.empty else "No data."
        res.tables.append(("Highest CPM queries", sub[["query", "cpm", "spend", "clicks", "gmv", "roas"]]))
        res.notes.append("Fact: CPM is compared across queries inside search placements only.")
    res.notes += notes
    res.notes += lag_note(ctx, rng, "search_query")[:1]
    return _finish(res, rng)


def _drag(ctx, p, n):
    df, rng = _sq(ctx, p, ("keyword", "match_type", "query"))
    if df.empty:
        return empty_result()
    be = ctx.break_even()
    rows = []
    kw = df.groupby(["keyword", "match_type"], dropna=False)[["spend", "gmv"]].sum().to_dict("index")
    for r in df.itertuples():
        ks = kw[(r.keyword, r.match_type)]
        spend_wo, gmv_wo = ks["spend"] - r.spend, ks["gmv"] - (r.gmv or 0)
        kroas = metrics.roas(ks["gmv"], ks["spend"])
        wo = metrics.roas(gmv_wo, spend_wo)
        lost = loss_below_break_even(r.spend, r.gmv, be)
        rows.append(dict(keyword=r.keyword, match_type=r.match_type, query=r.query, impressions=r.impressions,
                         spend=r.spend, clicks=r.clicks, ctr=r.ctr, cpm=r.cpm, gmv=r.gmv, roas=r.roas,
                         kw_roas=kroas, kw_roas_without=wo,
                         contains_keyword=contains_keyword(r.query, r.keyword), lost=lost))
    d = pd.DataFrame(rows)
    sub = d[(num(d, "roas").fillna(0) < num(d, "kw_roas").fillna(0)) & (num(d, "kw_roas_without") > num(d, "kw_roas"))]
    sub = top(sub, n, "lost")
    if sub.empty:
        return _finish(Result(headline="No query is dragging a keyword's ROAS down in this range."), rng)
    t = sub.iloc[0]
    res = Result(headline=(f"'{t['query']}' on {t['match_type']} '{t['keyword']}' dragged ROAS down most: {fmt.inr(t['spend'])} spend, "
                           f"ROAS {fmt.roas(t['roas'])}; the keyword's ROAS is {fmt.roas(t['kw_roas'])} with it and "
                           f"{fmt.roas(t['kw_roas_without'])} without."))
    res.tables.append(("Queries dragging keyword ROAS (ranked by spend lost)", sub[
        ["keyword", "match_type", "query", "impressions", "spend", "ctr", "cpm", "gmv", "roas", "kw_roas", "kw_roas_without", "lost"]]))
    spill = sub[~sub["contains_keyword"].astype(bool)]
    if len(spill):
        res.notes.append(f"{len(spill)} of these do not contain the keyword (broad-match spill).")
    if not (num(sub, "lost") > 0).any():
        res.notes.append(f"None of these is below {'break-even' if be else 'ROAS 1.00'}; they only pull their keyword's ROAS down relative to its other queries.")
    res.notes.append("Action: add the worst as negatives, move the keyword to exact, or lower the bid. Broad on 'snackible' is deliberate: treat it as expand spend and use negatives or a lower bid, not exact.")
    res.notes += _be_note(ctx) + _md_note(ctx, None)
    return _finish(res, rng)


# --------------------------------------------------------------------------
def new_queries(ctx, p):
    if not ctx.has_rows("search_query"):
        return empty_result()
    cur = ctx.effective_period(p, "search_query")
    lo, hi = ctx.bounds("search_query")
    if p.period is None:  # default: latest 7 days of data
        from datetime import timedelta
        cur = type(cur)(max(lo, hi - timedelta(days=6)), hi, "latest 7 days")
    prev = previous_period(cur)
    df, rng = ctx.fetch("search_query", ("query",), cur, filters_from(p, "search_query"))
    seen = ctx.sql("SELECT DISTINCT lower(trim(search_query)) q FROM search_query WHERE metrics_date < ?", [cur.start])
    seen = set(seen["q"])
    new = df[~df["query"].isin(seen)]
    if not seen:
        return _finish(Result(headline="No earlier data to compare, so every query would count as new.", kind="ok"), rng)
    new = top(new, p.top_n, "spend")
    res = Result(headline=f"{len(df[~df['query'].isin(seen)])} queries appeared for the first time in {cur.start} to {cur.end}.")
    res.tables.append(("New queries by spend", new[["query"] + SQ_COLS]))
    res.notes.append("Action: for any that convert, add as an exact keyword; for any with spend and no GMV, add as a negative once min data is met.")
    res.notes += _md_note(ctx, None)
    return _finish(res, rng)


def themes(ctx, p, variants_only=False):
    from .normalise import theme_key
    if not ctx.has_rows("search_query"):
        return empty_result()
    df, rng = _sq(ctx, p, ("query",))
    if df.empty:
        return empty_result()
    df = df.copy()
    df["variant_group"] = df["query"].map(theme_key)
    if variants_only:
        g = df.groupby("variant_group").filter(lambda x: x["query"].nunique() > 1)
        if g.empty:
            return _finish(Result(headline="No spelling variants found to combine."), rng)
        out = sum_by(g, "variant_group")
        out["variants"] = out["variant_group"].map(lambda k: ", ".join(sorted(g[g["variant_group"] == k]["query"].unique())[:6]))
        out = top(out, p.top_n, "spend")
        res = Result(headline=f"{len(out)} query groups have spelling variants worth combining; the largest is '{out.iloc[0]['variant_group']}'.")
        res.tables.append(("Spelling variants combined (theme view only)", out[["variant_group", "variants", "spend", "gmv", "roas"]]))
        res.notes.append("Query-level views still show each spelling separately.")
        return _finish(res, rng)
    themes_ = {"chips": ["chips"], "khakhra": ["khakhra"], "biscuits and dips": ["biscuit", "dip"], "puffs": ["puff"],
               "protein": ["protein"], "healthy": ["healthy", "millet", "ragi", "jowar"]}
    rows = []
    for name, keys in themes_.items():
        m = df[df["variant_group"].map(lambda q: any(k in q for k in keys))]
        if not m.empty:
            t = m[["spend", "impressions", "clicks", "a2c", "gmv", "conversions"]].sum(min_count=1)
            rows.append(dict(theme=name, queries=len(m), **t.to_dict()))
    out = metrics.add_ratios(pd.DataFrame(rows))
    if out.empty:
        return _finish(Result(headline="No queries matched the themes."), rng)
    out = top(out, 20, "roas")
    res = Result(headline=f"Best theme by ROAS is '{out.iloc[0]['theme']}' at {fmt.roas(out.iloc[0]['roas'])} on {fmt.inr(out.iloc[0]['spend'])}.")
    res.tables.append(("Themes ranked by ROAS (a query can sit in more than one theme)", out[["theme", "queries", "spend", "clicks", "gmv", "roas"]]))
    res.notes.append("Fact: spelling variants (khakra/khakhra, protien/protein) are grouped in this theme view.")
    return _finish(res, rng)


def more_keywords(ctx, p, exact_only=False):
    """Queries that convert but are not bid as exact keywords."""
    if not ctx.has_rows("search_query"):
        return empty_result()
    df, rng = _sq(ctx, p, ("query",))
    exact = ctx.sql("SELECT DISTINCT lower(trim(keyword)) k FROM granular WHERE match_type LIKE '%EXACT'") if ctx.has_rows("granular") else pd.DataFrame({"k": []})
    exact = set(exact["k"])
    thresh = ctx.set("scale_rule.roas_at_least") or ctx.break_even() or 1.0
    df = _apply_min_data(ctx, df)
    sub = df[(num(df, "roas") >= thresh) & (~df["query"].isin(exact))]
    sub = top(sub, p.top_n, "gmv")
    res = Result(headline=f"{len(sub)} proven queries convert at ROAS {fmt.roas(thresh)} or better and are not bid as exact keywords."
                 if not sub.empty else "No converting query is missing an exact keyword.")
    if not sub.empty:
        sub = sub.copy()
        sub["label"] = "proven"
        sub["suggested"] = "exact match, small test budget"
        res.tables.append(("Exact-keyword candidates", sub[["query", "label", "spend", "gmv", "roas", "suggested"]]))
    res.notes.append("Hypothesis: 'new idea (untested)' keywords related to these (same SKU and bucket) could be tried; none are listed because they are not in the data.")
    res.notes += _md_note(ctx, None) + _be_note(ctx)
    return _finish(res, rng)


# --------------------------------------------------------------------------
def _bucket_tables(ctx, p, period):
    """(by bid DataFrame, by served DataFrame, rng) grouped by bucket."""
    clf = ctx.clf
    g, rng1 = ctx.fetch("granular", ("keyword",), period, filters_from(p, "granular"),
                        extra="match_type NOT LIKE '%INVALID' AND keyword IS NOT NULL")
    s, rng2 = ctx.fetch("search_query", ("query",), period, filters_from(p, "search_query"))
    if not g.empty:
        g["bucket"] = g["keyword"].map(clf.bucket)
    if not s.empty:
        s["bucket"] = s["query"].map(clf.bucket)
    return (sum_by(g, "bucket") if not g.empty else g), (sum_by(s, "bucket") if not s.empty else s), g, s, (rng1 if not g.empty else rng2)


def _with_target(ctx, tab):
    t = {k: ctx.set(f"split_target.{k}") for k in ("attack", "expand", "defend")}
    tab = add_share(tab.copy(), "spend", "share")
    if all(v is not None for v in t.values()):
        tot = sum(t.values())
        tab["target"] = tab["bucket"].map(lambda b: t[b] / tot)
        tab["gap_pts"] = [(a - b) * 100 if a is not None and b is not None else None for a, b in zip(tab["share"], tab["target"])]
        return tab, True
    return tab, False


def bucket_split(ctx, p, mode="split"):
    if not (ctx.has_rows("granular") or ctx.has_rows("search_query")):
        return empty_result()
    period = ctx.effective_period(p)
    bid, srv, g, s, rng = _bucket_tables(ctx, p, period)
    cols = ["bucket", "spend", "share", "impressions", "clicks", "ctr", "cpc", "cpm", "gmv", "roas"]
    res = Result()
    notes = []
    if mode == "defend_on_brand":
        sq, _ = ctx.fetch("search_query", ("keyword", "query"), period, filters_from(p, "search_query"))
        sq = sq[sq["keyword"].map(lambda k: ctx.clf.bucket(k) == "defend")]
        if sq.empty:
            return _finish(Result(headline="No defend-keyword spend in this range."), rng)
        sq = sq.assign(brand=sq["query"].map(ctx.clf.has_brand))
        on = sq[sq["brand"]]["spend"].sum()
        res.headline = (f"{fmt.pct(metrics.sdiv(on, sq['spend'].sum()))} of defend-keyword spend ({fmt.inr(on)} of {fmt.inr(sq['spend'].sum())}) "
                        "landed on brand searches; the rest served on other searches.")
        off = top(sq[~sq["brand"]], p.top_n, "spend")
        res.tables.append(("Defend keyword spend served on non-brand searches", off[["keyword", "query", "spend", "roas"]]))
        res.notes.append("Fact: broad match on 'snackible' is deliberate; spend on non-brand searches counts as expand spend.")
        return _finish(res, rng)
    if mode == "attack_leak":
        sq, _ = ctx.fetch("search_query", ("keyword", "query"), period, filters_from(p, "search_query"))
        sq = sq[sq["keyword"].map(lambda k: ctx.clf.bucket(k) == "attack")]
        if sq.empty:
            return _finish(Result(headline="No attack-keyword spend in this range."), rng)
        comp = sq["query"].map(lambda q: ctx.clf.bucket(q) == "attack")
        leak = sq[~comp]
        res.headline = (f"{fmt.inr(leak['spend'].sum())} ({fmt.pct(metrics.sdiv(leak['spend'].sum(), sq['spend'].sum()))}) of attack-keyword spend "
                        "leaked into non-competitor searches.")
        res.tables.append(("Attack spend on non-competitor searches", top(leak, p.top_n, "spend")[["keyword", "query", "spend", "roas"]]))
        res.notes.append("Action: add the worst as negatives or move the keyword to exact.")
        return _finish(res, rng)
    if mode == "by_match":
        d, rng = ctx.fetch("granular", ("keyword", "match_type"), period, filters_from(p, "granular"),
                           extra="match_type NOT LIKE '%INVALID' AND keyword IS NOT NULL")
        if d.empty:
            return empty_result()
        d["bucket"] = d["keyword"].map(ctx.clf.bucket)
        out = sum_by(d, ["bucket", "match_type"])
        best = top(out[num(out, 'spend') > 0], 1, "roas").iloc[0]
        res.headline = f"Best bucket x match type cell: {best['bucket']} {best['match_type']} at ROAS {fmt.roas(best['roas'])}."
        res.tables.append(("Bucket x match type (by keyword bid)", out[["bucket", "match_type", "spend", "clicks", "gmv", "roas"]]))
        res.notes.append("Fact: attack ROAS is lower by nature; judge it on competitor closeness and conversion.")
        return _finish(res, rng)

    if not srv.empty:
        srv_t, has_t = _with_target(ctx, srv)
    if not bid.empty:
        bid_t, has_t = _with_target(ctx, bid)
    if mode == "served":
        res.headline = ("By search query served: " + ", ".join(f"{r.bucket} {fmt.roas(r.roas)} ROAS" for r in srv_t.itertuples())
                        if not srv.empty else "No search query data loaded.")
        res.tables.append(("ROAS by bucket (searches actually served)", srv_t[cols + (["target", "gap_pts"] if has_t else [])]))
    else:
        base = bid_t if not bid.empty else srv_t
        res.headline = "Spend split by keyword bid: " + ", ".join(f"{r.bucket} {fmt.pct(r.share)}" for r in base.itertuples()) + "."
        if not bid.empty:
            res.tables.append(("By keyword bid (intent)", bid_t[cols + (["target", "gap_pts"] if has_t else [])]))
        if not srv.empty:
            res.tables.append(("By search query served (truth, search placements only)", srv_t[cols + (["target", "gap_pts"] if has_t else [])]))
        if mode == "scorecard":
            prev = previous_period(period)
            b2, s2, *_ = _bucket_tables(ctx, p, prev)
            t2 = s2 if not s2.empty else b2
            cur = srv if not srv.empty else bid
            if not t2.empty:
                m = cur.merge(t2[["bucket", "spend", "gmv", "roas"]], on="bucket", suffixes=("", "_prev"))
                m["spend_change"] = [metrics.growth(a, b) for a, b in zip(m["spend"], m["spend_prev"])]
                m["roas_change"] = [metrics.growth(num(m, "roas")[i], num(m, "roas_prev")[i]) for i in range(len(m))]
                res.tables.append((f"Trend vs previous period ({prev.start} to {prev.end})", m[["bucket", "spend", "spend_change", "roas", "roas_change"]]))
            res.notes.append("Defend: judge on ad rank and ROAS; exact-match brand and Guaranteed Top Slot are protected, never cut on ROAS alone.")
            res.notes.append("Attack: judge on competitor closeness and conversion. Expand: judge on ROAS vs break-even and volume.")
    if not has_t:
        notes.append("needs setting: split_target (gap against target not shown)")
    if mode == "split" and has_t and not srv.empty:
        tgt_total = srv["spend"].sum()
        res.notes.append("Rebalanced split in rupees (searches served basis): " + ", ".join(
            f"{r.bucket} {fmt.inr(r.target * tgt_total)}" for r in srv_t.itertuples()))
    res.notes += notes + _be_note(ctx)[:1]
    if not srv.empty and not bid.empty:
        diff = max([abs((a or 0) - (b or 0)) for a, b in zip(bid_t["share"], srv_t.set_index("bucket").reindex(bid_t["bucket"])["share"]) if a is not None and b == b] or [0])
        if diff > 0.05:
            res.notes.append("Bid and served splits differ meaningfully; the served split is the truth.")
    return _finish(res, rng)


# --------------------------------------------------------------------------
def match_type_view(ctx, p, mode):
    if not ctx.has_rows("granular") and not ctx.has_rows("search_query"):
        return empty_result()
    period = ctx.effective_period(p, "granular" if ctx.has_rows("granular") else "search_query")
    be = ctx.break_even()
    res = Result()
    if mode == "compare":
        df, rng = ctx.fetch("granular", ("match_type",), period, filters_from(p, "granular"),
                            extra="match_type NOT LIKE '%INVALID'")
        if df.empty:
            return empty_result()
        res.headline = "Broad vs exact: " + "; ".join(f"{r.match_type} {fmt.inr(r.spend)} at ROAS {fmt.roas(r.roas)}, CPC {fmt.inr(r.cpc, 2)}, CPM {fmt.inr(r.cpm, 2)}"
                                                         for r in df.itertuples()) + "."
        res.tables.append(("By match type (INVALID excluded)", df[["match_type", "spend", "clicks", "cpc", "cpm", "gmv", "roas"]]))
        res.notes.append("Fact: Collection Ads and Browse Boost rows with match type INVALID are excluded.")
        return _finish(res, rng)

    sq, rng = _sq(ctx, p, ("keyword", "match_type", "query"), period=period)
    if sq.empty:
        return empty_result()
    sq = sq.copy()
    sq["contains"] = [contains_keyword(q, k) for q, k in zip(sq["query"], sq["keyword"])]
    sq["q_bucket"] = sq["query"].map(ctx.clf.bucket)
    sq["k_bucket"] = sq["keyword"].map(ctx.clf.bucket)

    def kwsum(d):
        g = d.groupby(["keyword", "match_type"])[["spend", "gmv", "impressions", "clicks"]].sum().reset_index()
        return g

    if mode in ("spill_share", "broad_spill", "to_exact", "fixlist", "competitor_spill", "narrow_exact", "compete", "snackible_broad", "keyword_focus"):
        broad = sq[sq["match_type"] == "Broad"]
        if mode == "snackible_broad":
            kws = p.ent("keywords") or ["snackible"]
            sub = sq[(sq["keyword"].str.lower().isin([k.lower() for k in kws])) & (sq["match_type"] == "Broad")]
            if sub.empty:
                return _finish(Result(headline=f"No broad-match spend found for '{kws[0]}'."), rng)
            brand = sub[sub["query"].map(ctx.clf.has_brand)]
            tot = sub["spend"].sum()
            res.headline = (f"Broad '{kws[0]}' spent {fmt.inr(tot)} at ROAS {fmt.roas(metrics.roas(sub['gmv'].sum(), tot))}; "
                            f"only {fmt.pct(metrics.sdiv(brand['spend'].sum(), tot))} landed on brand searches.")
            res.tables.append(("Searches served by this broad keyword", top(sub, p.top_n, "spend")[["query", "spend", "roas", "gmv"]]))
            res.notes.append("Fact: broad match on 'snackible' is deliberate; non-brand spend counts as expand spend.")
            res.notes.append("Action: add the generic losers as negatives or lower the broad bid; move budget to category terms that convert.")
            return _finish(res, rng)
        if mode == "keyword_focus":  # exact vs broad for a named keyword
            kws = [k.lower() for k in p.ent("keywords")]
            if not kws:
                return _finish(Result(headline="Which keyword? Name it (for example 'healthy snacks') and I will compare exact and broad.", kind="unknown"), rng)
            d, rng = ctx.fetch("granular", ("keyword", "match_type"), period, filters_from(p, "granular"),
                               extra="match_type NOT LIKE '%INVALID'")
            d = d[d["keyword"].str.lower().isin(kws)]
            if d.empty:
                return _finish(Result(headline="No rows for that keyword."), rng)
            res.headline = "; ".join(f"{r.match_type} {fmt.inr(r.spend)} ROAS {fmt.roas(r.roas)} CPM {fmt.inr(r.cpm, 2)}" for r in d.itertuples())
            res.tables.append(("Exact vs broad", d[["keyword", "match_type", "spend", "clicks", "cpm", "gmv", "roas"]]))
            res.notes.append("Action: shift budget toward the match type with higher ROAS only if it is above break-even and impressions are not capped; check spill first.")
            res.notes += _be_note(ctx)
            return _finish(res, rng)
        if mode == "competitor_spill":
            kw_filter = [k.lower() for k in p.ent("keywords")]
            d = broad[(broad["q_bucket"] == "attack") & (broad["k_bucket"] != "attack") & (~broad["contains"])]
            if kw_filter:
                d = broad[broad["keyword"].str.lower().isin(kw_filter) & (~broad["contains"])]
            d = top(d, p.top_n, "spend")
            res.headline = f"{fmt.inr(d['spend'].sum())} of broad spend served competitor or other unrelated searches in the top {len(d)}." if not d.empty else "No broad keyword is serving competitor searches by accident."
            res.tables.append(("Broad keyword -> searches served", d[["keyword", "query", "spend", "roas"]]))
            return _finish(res, rng)
        ks = kwsum(sq)
        spill = sq[~sq["contains"]].groupby(["keyword", "match_type"])["spend"].sum().rename("spill_spend").reset_index()
        ks = ks.merge(spill, on=["keyword", "match_type"], how="left")
        ks["spill_spend"] = ks["spill_spend"].fillna(0)
        ks["spill_share"] = [metrics.sdiv(a, b) for a, b in zip(ks["spill_spend"], ks["spend"])]
        ks["roas"] = [metrics.roas(g, s) for g, s in zip(ks["gmv"], ks["spend"])]
        ks = _apply_min_data(ctx, ks.assign(days=ctx.con.execute("select count(distinct metrics_date) from search_query").fetchone()[0]))
        if mode in ("spill_share",):
            d = top(ks, p.top_n, "spill_spend")
            res.headline = "Share of each keyword's spend landing on searches that do not contain it (largest first)."
            res.tables.append(("Spill by keyword", d[["keyword", "match_type", "spend", "spill_spend", "spill_share", "roas"]]))
            return _finish(res, rng)
        if mode in ("broad_spill", "to_exact", "fixlist"):
            d = ks[ks["match_type"] == "Broad"].copy()
            d["lost"] = [loss_below_break_even(s, g, be) for s, g in zip(d["spend"], d["gmv"])]
            d["fix"] = [("keep deliberate; add negatives or lower bid" if ctx.clf.has_brand(k) else _fix_for("Broad", (sh or 0) > 0.5, None)) for k, sh in zip(d["keyword"], d["spill_share"])]
            d = top(d[num(d, "spill_spend") > 0], p.top_n, "lost")
            res.headline = (f"{len(d)} broad keywords spill into unrelated searches; the largest loss is '{d.iloc[0]['keyword']}' "
                            f"with {fmt.pct(d.iloc[0]['spill_share'])} of spend off-keyword"
                            + (f", losing {fmt.inr(d.iloc[0]['lost'])} against {'break-even' if be else 'ROAS 1.00'}." if d.iloc[0]['lost'] > 0
                               else f"; none of it is below {'break-even' if be else 'ROAS 1.00'} yet.")) if not d.empty else "No broad keyword is spilling."
            cols = ["keyword", "spend", "spill_spend", "spill_share", "roas", "lost", "fix"]
            res.tables.append(("Broad keywords with spill (expected saving = loss below break-even)", d[cols]))
            res.notes.append("Action: broad to exact where spill is large and ROAS is low; negatives where the broad reach is deliberate.")
            res.notes += _be_note(ctx) + _md_note(ctx, None)
            return _finish(res, rng)
        if mode == "narrow_exact":
            ex = ks[ks["match_type"] == "Exact"].copy()
            thresh = ctx.set("scale_rule.roas_at_least") or be or 1.0
            if ex.empty:
                return _finish(Result(headline="No exact keywords in search data."), rng)
            cut = num(ex, "impressions").quantile(0.33)
            d = ex[(num(ex, "roas") >= thresh) & (num(ex, "impressions") <= cut)]
            d = top(d, p.top_n, "roas")
            res.headline = f"{len(d)} exact keywords earn ROAS {fmt.roas(thresh)} or better with bottom-third impressions: too narrow."
            res.tables.append(("Exact keywords to widen", d[["keyword", "impressions", "spend", "roas"]]))
            res.notes.append("Action: add a broad version or raise the bid in small steps. Depends on stock, not in data.")
            return _finish(res, rng)
        if mode == "compete":
            both = sq.groupby("keyword")["match_type"].nunique()
            kws = both[both > 1].index
            d = sq[sq["keyword"].isin(kws)]
            piv = d.pivot_table(index=["keyword", "query"], columns="match_type", values="spend", aggfunc="sum").dropna().reset_index()
            if piv.empty:
                return _finish(Result(headline="No keyword has exact and broad versions serving the same search."), rng)
            piv["overlap_spend"] = piv.drop(columns=["keyword", "query"]).sum(axis=1)
            piv = top(piv, p.top_n, "overlap_spend")
            res.headline = f"{piv['keyword'].nunique()} keywords have exact and broad versions serving the same searches."
            res.tables.append(("Searches served by both exact and broad", piv))
            res.notes.append("Hypothesis: they bid against each other; test pausing the weaker one for a week.")
            return _finish(res, rng)
    return _finish(Result(headline="Match type view not available for this question.", kind="unknown"), rng)
