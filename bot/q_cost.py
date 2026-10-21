"""Cost, health, campaign, budget and cross-check intents."""
import math
import re
from datetime import timedelta

import pandas as pd

from . import fmt, metrics
from .core import Context, Params, Result, data_not_loaded, empty_result, filters_from, needs_setting, num, range_text
from .helpers import add_share, lag_note, loss_below_break_even, sum_by, top
from .params import Period, previous_period

FULL_SPEND = 0.95  # a campaign-day counts as "fully spent" at 95% of daily budget (assumption, stated in answers)


def _fin(res, rng):
    res.date_range = range_text(rng)
    return res


def _md(ctx):
    return ["needs setting: min_data (every row is shown; nothing is labelled 'too little data')"] if not ctx.min_data_set() else []


def _keep(ctx, df):
    if df.empty or not ctx.min_data_set() or "days" not in df:
        return df
    return df[[bool(ctx.enough_data(r.spend, r.clicks, r.days)) for r in df.itertuples()]]


def week_pair(ctx, p, table="granular"):
    """(current, previous) periods. Default: latest 7 days vs the 7 before."""
    lo, hi = ctx.bounds(table)
    if lo is None:
        return None, None
    if p.period is not None:
        cur = p.period
    else:
        cur = Period(max(lo, hi - timedelta(days=6)), hi, "latest 7 days", "")
    prev = p.prev if p.prev is not None else previous_period(cur)
    return cur, prev


def ref_roas(ctx, spend, gmv):
    be = ctx.break_even()
    return (be, "break-even") if be else (metrics.roas(gmv, spend), "account ROAS")


def _be_note(ctx):
    return (["needs setting: contribution_before_ads_pct_of_mrp_gmv (break-even ROAS). Account ROAS is used as the reference instead"]
            if ctx.break_even() is None else [])


# ------------------------------------------------------------------ placements
def placement_cost(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    period = ctx.effective_period(p)
    f = filters_from(p, "granular")
    n = p.top_n
    res = Result()
    notes = []
    if mode in ("cpm_by_placement", "cost_per_order", "bidding_compare"):
        dim = "bidding" if mode == "bidding_compare" else "placement"
        df, rng = ctx.fetch("granular", (dim,), period, f)
        if df.empty:
            return empty_result()
        df = df.assign(cost_per_order=[metrics.sdiv(s, c) for s, c in zip(df["spend"], df["conversions"])])
        if mode == "cpm_by_placement":
            res.headline = "Cost per 1,000 impressions by placement: " + "; ".join(f"{r.placement} {fmt.inr(r.cpm, 2)}" for r in df.itertuples()) + "."
            res.tables.append(("CPM by placement", df[["placement", "spend", "impressions", "cpm", "ctr", "cpc", "roas"]]))
            res.notes.append("Fact: compare CPM within one placement only; Collection Ads, keyword ads and banners have different cost structures. The report's eCPM column is not used.")
        elif mode == "cost_per_order":
            t = top(df[num(df, "cost_per_order") > 0], n, "cost_per_order", asc=True)
            res.headline = f"Cheapest cost per order: {t.iloc[0]['placement']} at {fmt.inr(t.iloc[0]['cost_per_order'], 2)}." if not t.empty else "No placement has conversions."
            res.tables.append(("Cost per order (spend / TOTAL_CONVERSIONS)", t[["placement", "spend", "conversions", "cost_per_order", "roas"]]))
            res.notes.append("Assumption: an order is TOTAL_CONVERSIONS. Confirm before I switch to TOTAL_A2C. Conversions can exceed clicks for Collection Ads, Search Inline Banner and Auto Suggest; do not compare those with keyword ads.")
        else:
            res.headline = "Cost per conversion by bidding type: " + "; ".join(f"{r.bidding} {fmt.inr(r.cost_per_order, 2)}" for r in df.itertuples()) + "."
            res.tables.append(("Bidding type comparison", df[["bidding", "spend", "cpm", "cpc", "conversions", "cost_per_order", "roas"]]))
            if len(df) < 2:
                res.notes.append("Fact: only one bidding type appears in the loaded data, so there is nothing to compare yet.")
    elif mode == "high_cpm_low_ctr":
        df, rng = ctx.fetch("granular", ("campaign", "placement"), period, f)
        pl, _ = ctx.fetch("granular", ("placement",), period, f)
        ref = pl.set_index("placement")
        df = _keep(ctx, df)
        flag = [(c or 0) > (ref.loc[pm, "cpm"] or 0) and (k or 0) < (ref.loc[pm, "ctr"] or 0) for pm, c, k in zip(df["placement"], df["cpm"], df["ctr"])]
        d = top(df[flag], n, "spend")
        res.headline = f"{len(d)} campaigns pay above their placement's CPM with below-placement CTR." if not d.empty else "No campaign has both high CPM and low CTR within its placement."
        res.tables.append(("High CPM, low CTR (vs own placement)", d[["campaign", "placement", "cpm", "ctr", "spend", "roas"]]))
        res.notes.append("Fact: each campaign is compared with the total of its own placement, never across placements.")
        notes += _md(ctx)
    elif mode == "cpc_over":
        acc = ctx.set("cpc_acceptable_by_bucket")
        df, rng = ctx.fetch("granular", ("keyword",), period, f, extra="keyword IS NOT NULL")
        df["bucket"] = df["keyword"].map(ctx.clf.bucket)
        out = sum_by(df, "bucket")
        if not acc:
            res = needs_setting("cpc_acceptable_by_bucket", "Showing current CPC by bucket instead.")
            res.tables.append(("CPC by bucket", out[["bucket", "spend", "clicks", "cpc"]]))
            return _fin(res, rng)
        over = out[[(r.cpc or 0) > acc.get(r.bucket, math.inf) for r in out.itertuples()]]
        res.headline = f"{len(over)} buckets pay more per click than you accept." if not over.empty else "No bucket pays above the accepted CPC."
        res.tables.append(("CPC vs accepted", out[["bucket", "cpc", "spend", "clicks"]]))
    elif mode == "collection_worth":
        plc = [x for x in ctx.catalog().placements if x and x.lower().startswith("collection")] or p.ent("placements")
        if not plc:
            return _fin(Result(headline="No Collection Ads placement found in the data."), (None, None))
        df, rng = ctx.fetch("granular", ("week",), period, {"placement": plc}, order="week")
        if df.empty:
            return empty_result()
        tot = df[["spend", "gmv"]].sum()
        be = ctx.break_even()
        res.headline = f"Collection Ads spent {fmt.inr(tot['spend'])} for {fmt.inr(tot['gmv'])} GMV (ROAS {fmt.roas(metrics.roas(tot['gmv'], tot['spend']))}); " + \
            (f"break-even is {fmt.roas(be)}." if be else "break-even is not set.")
        res.tables.append(("Collection Ads by week", df[["week", "spend", "gmv", "roas", "conversions"]]))
        res.notes.append("Fact: Collection Ads has no SKU and conversion can exceed clicks; judge it on spend vs GMV trend only.")
        res.notes.append("Hypothesis: it may keep serving when products are out of stock (stock not in data).")
        notes += _be_note(ctx)
    elif mode in ("cpm_poor_rank", "top_rank_no_return"):
        wh, args = ctx.where("granular", period, f, "keyword IS NOT NULL AND ad_rank IS NOT NULL")
        df = ctx.sql("SELECT keyword, SUM(total_budget_burnt) spend, SUM(total_impressions) impressions, SUM(total_clicks) clicks, "
                     "SUM(total_a2c) a2c, SUM(total_gmv) gmv, SUM(total_conversions) conversions, COUNT(DISTINCT metrics_date) AS days, "
                     "SUM(ad_rank*total_impressions)/NULLIF(SUM(total_impressions),0) AS avg_rank FROM granular" + wh + " GROUP BY 1", args)
        rng = ctx.con.execute("SELECT min(metrics_date), max(metrics_date) FROM granular" + wh, args).fetchone()
        df = metrics.add_ratios(df)
        df = _keep(ctx, df)
        if df.empty:
            return empty_result()
        if mode == "cpm_poor_rank":
            d = df[(num(df, "cpm") >= num(df, "cpm").median()) & (num(df, "avg_rank") >= num(df, "avg_rank").median())]
            d = top(d, n, "cpm")
            res.headline = f"{len(d)} keywords pay above-median CPM for below-median ad rank." if not d.empty else "No keyword pays a high CPM for a poor rank."
        else:
            ref, label = ref_roas(ctx, df["spend"].sum(), df["gmv"].sum())
            d = df[(num(df, "avg_rank") <= num(df, "avg_rank").median()) & (num(df, "roas") < ref)]
            d = top(d, n, "spend")
            res.headline = f"{len(d)} keywords hold above-median ad rank with ROAS below {label} ({fmt.roas(ref)})." if not d.empty else "No keyword buys top rank without a return."
            notes += _be_note(ctx)
        res.tables.append(("Keywords", d[["keyword", "avg_rank", "cpm", "spend", "ctr", "roas"]]))
        res.notes.append("Assumption: a lower ad_rank number is a better position. Rank is impression-weighted.")
        res.notes.append("Fact: brand defence and Guaranteed Top Slot on brand searches are protected; do not cut them on ROAS alone.")
        notes += _md(ctx)
    elif mode == "unspent_budget":
        wh, args = ctx.where("granular", period, f)
        d = ctx.sql("SELECT campaign_name campaign, metrics_date d, MAX(total_budget) budget, SUM(total_budget_burnt) spend FROM granular" + wh +
                    " GROUP BY 1,2", args)
        rng = (d["d"].min().date(), d["d"].max().date()) if not d.empty else (None, None)
        if d.empty:
            return empty_result()
        d["full"] = d["spend"] >= FULL_SPEND * d["budget"]
        g = d.groupby("campaign").agg(days=("d", "count"), days_full=("full", "sum"), budget=("budget", "median"),
                                      spend=("spend", "sum"), budget_total=("budget", "sum")).reset_index()
        g["utilisation"] = [metrics.sdiv(a, b) for a, b in zip(g["spend"], g["budget_total"])]
        g["avg_daily_spend"] = g["spend"] / g["days"]
        under = g[num(g, "utilisation") < FULL_SPEND].sort_values("utilisation")
        res.headline = f"{len(under)} of {len(g)} campaigns do not spend their full daily budget; lowest is {under.iloc[0]['campaign']} at {fmt.pct(under.iloc[0]['utilisation'])}." if not under.empty else "Every campaign spends its full daily budget."
        res.tables.append(("Budget utilisation", top(under, n * 2, "utilisation", asc=True)[["campaign", "budget", "avg_daily_spend", "days_full", "days", "utilisation"]]))
        res.notes.append(f"Assumption: a day counts as fully spent at {int(FULL_SPEND * 100)}% of daily budget. Daily budget is one value per campaign per day (TOTAL_BUDGET is never summed).")
        res.notes.append("Hypotheses to test: bid or rank too low to win auctions, targeting too narrow, or products out of stock (not in data).")
    elif mode in ("cpm_trend", "lower_bids"):
        cur, prev = week_pair(ctx, p)
        dim = "city" if "city" in (p.text or "").lower() else "placement"
        a, rng = ctx.fetch("granular", (dim,), cur, f)
        b, _ = ctx.fetch("granular", (dim,), prev, f)
        if a.empty or b.empty:
            return _fin(Result(headline="No earlier period to compare."), rng)
        if mode == "cpm_trend":
            m = a.merge(b, on=dim, suffixes=("", "_prev"))
            m["cpm_change"] = [metrics.growth(x, y) for x, y in zip(num(m, "cpm"), num(m, "cpm_prev"))]
            m = top(_keep(ctx, m), n, "cpm_change")
            res.headline = (f"CPM rose most in {m.iloc[0][dim]}: {fmt.signed_pct(m.iloc[0]['cpm_change'])} "
                            f"({fmt.inr(m.iloc[0]['cpm_prev'], 2)} to {fmt.inr(m.iloc[0]['cpm'], 2)}).") if not m.empty else "No data."
            res.tables.append((f"CPM change by {dim} ({cur.start} to {cur.end} vs {prev.start} to {prev.end})", m[[dim, "cpm_prev", "cpm", "cpm_change", "ctr", "roas"]]))
            res.notes.append("Fact: compare within one placement; a mix shift between placements can move blended CPM.")
        else:
            acc = ctx.set("cpm_acceptable_by_placement")
            d, rng = ctx.fetch("granular", ("placement",), period, f)
            if not acc:
                res = needs_setting("cpm_acceptable_by_placement", "Showing current CPM by placement. Where I should lower bids, and by how much, needs this setting.")
                res.tables.append(("CPM by placement", d[["placement", "spend", "cpm", "roas"]]))
                return _fin(res, rng)
            over = d[[(r.cpm or 0) > acc.get(r.placement, math.inf) for r in d.itertuples()]]
            res.headline = f"{len(over)} placements pay above the accepted CPM."
            res.tables.append(("CPM vs accepted", d[["placement", "cpm", "spend", "roas"]]))
        notes += _md(ctx)
    res.notes += notes
    return _fin(res, rng)


# ---------------------------------------------------------------------- health
HEALTH_ROWS = [("spend", fmt.inr), ("impressions", fmt.num), ("clicks", fmt.num), ("ctr", fmt.pct), ("cpm", lambda x: fmt.inr(x, 2)),
               ("cpc", lambda x: fmt.inr(x, 2)), ("gmv", fmt.inr), ("roas", fmt.roas), ("conversions", fmt.num)]


def _compare_table(a: dict, b: dict) -> pd.DataFrame:
    rows = []
    for k, _ in HEALTH_ROWS:
        rows.append({"metric": k, "current": a.get(k), "previous": b.get(k), "change": metrics.growth(a.get(k), b.get(k))})
    return pd.DataFrame(rows)


def _decompose(a: dict, b: dict):
    """ROAS = CTR * conv_rate * (GMV per conversion) / (CPM / 1000), so the log
    change splits exactly into cost, click-through, conversion and order value."""
    def parts(x):
        return {
            "cost (CPM)": x.get("cpm"), "click-through (CTR)": x.get("ctr"),
            "conversion rate": metrics.conv_rate(x.get("conversions"), x.get("clicks")),
            "GMV per order": metrics.sdiv(x.get("gmv"), x.get("conversions")),
        }
    pa, pb = parts(a), parts(b)
    out = {}
    for k in pa:
        if pa[k] and pb[k]:
            v = math.log(pa[k] / pb[k])
            out[k] = -v if k.startswith("cost") else v
    return out


def health(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    cur, prev = week_pair(ctx, p)
    f = filters_from(p, "granular")
    a, rng = ctx.totals("granular", cur, f)
    b, _ = ctx.totals("granular", prev, f)
    if not a or a.get("spend") is None:
        return empty_result()
    res = Result()
    notes = lag_note(ctx, rng)
    tab = _compare_table(a, b or {})
    no_prev = not b or b.get("spend") in (None, 0)
    if no_prev:
        notes.append("No earlier period of the same length is loaded, so changes are n/a.")

    if mode in ("verdict", "wow", "numbers"):
        s, g, r = a["spend"], a["gmv"], a["roas"]
        ch = metrics.growth(r, b.get("roas")) if b else None
        res.headline = (f"{cur.start} to {cur.end}: spend {fmt.inr(s)}, GMV {fmt.inr(g)} (at MRP), ROAS {fmt.roas(r)}"
                        + (f", {fmt.signed_pct(ch)} on ROAS and {fmt.signed_pct(metrics.growth(s, b.get('spend')))} on spend versus the previous period." if not no_prev else "."))
        t = tab.copy()
        res.tables.append((f"{cur.start} to {cur.end} vs {prev.start} to {prev.end}", t))
        res.notes.append("Fact: ROAS is at MRP and overstates real revenue.")
    elif mode == "top3":
        rows = []
        for dim in ("campaign", "placement", "city"):
            x, _ = ctx.fetch("granular", (dim,), cur, f)
            y, _ = ctx.fetch("granular", (dim,), prev, f)
            if x.empty or y.empty:
                continue
            m = x.merge(y, on=dim, how="outer", suffixes=("", "_prev")).fillna({"gmv": 0, "gmv_prev": 0, "spend": 0, "spend_prev": 0})
            m["gmv_change"] = m["gmv"] - m["gmv_prev"]
            m["spend_change"] = m["spend"] - m["spend_prev"]
            for r in m.itertuples():
                rows.append({"dimension": dim, "item": getattr(r, dim), "gmv_change": r.gmv_change, "spend_change": r.spend_change})
        d = pd.DataFrame(rows)
        if d.empty:
            return _fin(Result(headline="No earlier period to compare."), rng)
        d = d.reindex(d["gmv_change"].abs().sort_values(ascending=False).index).head(3)
        res.headline = "Biggest movers: " + "; ".join(f"{r.item} ({r.dimension}) GMV {fmt.inr(r.gmv_change)}, spend {fmt.inr(r.spend_change)}" for r in d.itertuples()) + "."
        res.tables.append(("Top 3 changes by GMV movement", d))
        res.notes.append("Hypothesis: spend moving with GMV suggests budget changes; GMV moving alone suggests demand or conversion.")
    elif mode == "why":
        dec = _decompose(a, b or {})
        if not dec:
            return _fin(Result(headline="Cannot split the ROAS change: no earlier period."), rng)
        tot = sum(dec.values())
        worst = min(dec, key=dec.get)
        res.headline = (f"ROAS moved {fmt.signed_pct(metrics.growth(a['roas'], b['roas']))}; the biggest drag is {worst} "
                        f"({fmt.signed_pct(math.exp(dec[worst]) - 1)} effect).")
        res.tables.append(("What drove the ROAS change (each factor's effect)", pd.DataFrame(
            [{"factor": k, "effect": math.exp(v) - 1} for k, v in dec.items()])))
        res.notes.append("Fact: ROAS = CTR x conversion rate x GMV per order / (CPM / 1000), so these effects multiply to the total.")
        res.notes.append("Check attribution lag: the latest days may still be collecting sales and can make conversion look worse than it is.")
        res.notes.append("How to check it worked: recompute after the lag window closes and compare conversion rate first.")
        tab = pd.DataFrame()
    elif mode == "lag":
        lag = ctx.lag_days()
        obs = ctx.observed_lag_start()
        _, hi = ctx.bounds()
        lines = []
        if lag is not None:
            lines.append(f"Per settings.lag_days = {lag}: {(hi - timedelta(days=lag - 1)).isoformat()} to {hi.isoformat()} are still collecting sales.")
        else:
            res.kind = "needs_setting"
            lines.append("needs setting: lag_days")
        if obs is not None:
            lines.append(f"Fact: from {obs.isoformat()} the direct 14-day GMV equals the 7-day GMV, which means those days are still collecting sales.")
        res.headline = lines[0] if lines else "No lag sign found."
        res.notes += lines[1:]
        tab = pd.DataFrame()
    elif mode == "no_sku":
        t, _ = ctx.totals("granular", cur, f)
        ns, _ = ctx.fetch("granular", ("placement",), cur, f, extra="product_name IS NULL")
        sh = metrics.sdiv(ns["spend"].sum(), t["spend"]) if not ns.empty else 0
        res.headline = f"{fmt.pct(sh)} of spend ({fmt.inr(ns['spend'].sum() if not ns.empty else 0)}) went to placements without a SKU."
        if not ns.empty:
            res.tables.append(("Placements with no SKU", ns[["placement", "spend", "gmv", "roas", "conversions", "clicks"]]))
        res.notes.append("Fact: this spend cannot be tied to a SKU, and these placements may keep serving when products are out of stock.")
        if ctx.set("collection_ads_sku_map") is None:
            res.notes.append("needs setting: collection_ads_sku_map (to attribute these campaigns to SKUs)")
        tab = pd.DataFrame()
    elif mode == "a2s":
        basis = ctx.set("a2s_sales_basis")
        if basis is None:
            res = needs_setting("a2s_sales_basis", f"Spend for the period is {fmt.inr(a['spend'])}.")
            return _fin(res, rng)
        if isinstance(basis, (int, float)):
            sales = float(basis)
        elif str(basis).strip().lower() in ("total_gmv", "gmv", "ads gmv", "ads_gmv"):
            sales = a["gmv"]
        else:
            return _fin(needs_setting("a2s_sales_basis", f"'{basis}' is not a number or 'total_gmv'; I cannot compute A2S from it."), rng)
        v = metrics.sdiv(a["spend"], sales)
        ceil = ctx.set("a2s_ceiling_pct")
        res.headline = f"A2S is {fmt.pct(v)} (spend {fmt.inr(a['spend'])} / sales {fmt.inr(sales)})" + \
            (f", {'above' if v is not None and v * 100 > ceil else 'within'} your {ceil}% ceiling." if ceil is not None else ".")
        if ceil is None:
            res.notes.append("needs setting: a2s_ceiling_pct")
        tab = pd.DataFrame()
    elif mode in ("campaigns_waste", "improved"):
        x, _ = ctx.fetch("granular", ("campaign",), cur, f)
        y, _ = ctx.fetch("granular", ("campaign",), prev, f)
        x = _keep(ctx, x)
        if mode == "campaigns_waste":
            be = ctx.break_even()
            x = x.assign(lost=[loss_below_break_even(s, g, be) for s, g in zip(x["spend"], x["gmv"])])
            t = top(x, p.top_n, "lost") if (num(x, "lost") > 0).any() else top(x, p.top_n, "spend")
            t = t.assign(rank_basis=("loss below " + ("break-even" if be else "ROAS 1.00")) if (num(x, "lost") > 0).any() else "spend")
            res.headline = (f"{t.iloc[0]['campaign']} spent {fmt.inr(t.iloc[0]['spend'])} for {fmt.inr(t.iloc[0]['gmv'])} GMV (ROAS {fmt.roas(t.iloc[0]['roas'])})."
                            if not t.empty else "No data.")
            res.tables.append(("Campaigns with the least to show for spend", t[["campaign", "spend", "gmv", "roas", "lost"]]))
            notes += _be_note(ctx)
        else:
            if y.empty:
                return _fin(Result(headline="No earlier period to compare."), rng)
            m = x.merge(y, on="campaign", suffixes=("", "_prev"))
            m["roas_change"] = [metrics.growth(u, v) for u, v in zip(num(m, "roas"), num(m, "roas_prev"))]
            m["gmv_change"] = m["gmv"] - m["gmv_prev"]
            worse = bool(re.search(r"worse|declin|drop|fell|fall|underperform|down|slip", p.text or "", re.I))
            t = top(m, p.top_n, "roas_change", asc=worse)
            verb = "declined" if worse else "improved"
            res.headline = (f"{t.iloc[0]['campaign']} {verb} most: ROAS {fmt.signed_pct(t.iloc[0]['roas_change'])} "
                            f"({fmt.roas(t.iloc[0]['roas_prev'])} to {fmt.roas(t.iloc[0]['roas'])}).") if not t.empty else "No data."
            res.tables.append((f"Campaigns by ROAS {'decline' if worse else 'improvement'}", t[["campaign", "spend", "roas_prev", "roas", "roas_change", "gmv_change"]]))
        notes += _md(ctx)
        tab = pd.DataFrame()
    res.notes += notes
    return _fin(res, rng)


# ------------------------------------------------------------ budget / scaling
def campaign_budget(ctx: Context, p: Params, mode="increase") -> Result:
    """Increase-budget candidates with daily budget, days fully spent, ROAS and
    marginal ROAS when the daily budget changed in the period."""
    if not ctx.has_rows("granular"):
        return empty_result()
    period = ctx.effective_period(p)
    f = filters_from(p, "granular")
    wh, args = ctx.where("granular", period, f)
    d = ctx.sql("SELECT campaign_name campaign, metrics_date d, MAX(total_budget) budget, SUM(total_budget_burnt) spend, SUM(total_gmv) gmv, "
                "SUM(total_clicks) clicks, SUM(total_conversions) conversions FROM granular" + wh + " GROUP BY 1,2 ORDER BY 1,2", args)
    if d.empty:
        return empty_result()
    rng = (d["d"].min().date(), d["d"].max().date())
    be = ctx.break_even()
    rows = []
    for camp, g in d.groupby("campaign"):
        g = g.sort_values("d")
        full = (g["spend"] >= FULL_SPEND * g["budget"]).sum()
        tot_s, tot_g = g["spend"].sum(), g["gmv"].sum()
        roas_ = metrics.roas(tot_g, tot_s)
        half = len(g) // 2
        cr1 = metrics.conv_rate(g.iloc[:half]["conversions"].sum(), g.iloc[:half]["clicks"].sum()) if half else None
        cr2 = metrics.conv_rate(g.iloc[half:]["conversions"].sum(), g.iloc[half:]["clicks"].sum())
        mroas, chg = None, ""
        bud = g["budget"].round(2)
        if bud.nunique() > 1:
            last_change = bud[bud.diff().fillna(0) != 0].index[-1]
            pos = list(g.index).index(last_change)
            b_, a_ = g.iloc[:pos], g.iloc[pos:]
            if len(b_) and len(a_):
                mroas = metrics.marginal_roas(b_["gmv"].mean(), b_["spend"].mean(), a_["gmv"].mean(), a_["spend"].mean())
                chg = f"budget changed on {g.loc[last_change, 'd'].date()}"
        rows.append(dict(campaign=camp, daily_budget=g["budget"].iloc[-1], days_full=int(full), days=len(g), spend=tot_s, gmv=tot_g, roas=roas_,
                         conv_trend=("rising" if (cr2 or 0) > (cr1 or 0) * 1.05 else "falling" if (cr2 or 0) < (cr1 or 0) * 0.95 else "stable") if cr1 else "n/a",
                         marginal_roas=mroas, note=chg))
    t = pd.DataFrame(rows)
    res = Result()
    if be is None:
        res.notes.append("needs setting: contribution_before_ads_pct_of_mrp_gmv (break-even ROAS), so the ranking uses account ROAS")
    ref = be or metrics.roas(t["gmv"].sum(), t["spend"].sum())
    cand = t[(num(t, "roas") >= ref) & (t["days_full"] >= t["days"] * 0.5) & (t["conv_trend"] != "falling")]
    cand = top(cand, p.top_n, "roas")
    step = ctx.set("budget_increase_step_pct")
    res.headline = (f"{len(cand)} campaigns clear {'break-even' if be else 'account ROAS'} ({fmt.roas(ref)}), spend their budget on at least half the days and have stable or rising conversion: "
                    + ", ".join(cand["campaign"].head(3)) + ".") if not cand.empty else "No campaign clears the bar for a budget increase."
    res.tables.append(("Increase candidates", cand[["campaign", "daily_budget", "days_full", "days", "roas", "conv_trend", "marginal_roas", "note"]]))
    if step is None:
        res.notes.append("needs setting: budget_increase_step_pct")
    else:
        res.notes.append(f"Action: raise in steps of {step}% and judge after 7 days on marginal ROAS (extra GMV / extra spend), not blended ROAS.")
    res.notes.append("data not loaded: stock/fill rate. Every increase depends on stock, not in data; stock in transit never counts.")
    res.notes.append(f"Assumption: a day counts as fully spent at {int(FULL_SPEND * 100)}% of daily budget; 'stable or rising' compares conversion in the two halves of the range.")
    res.notes += _md(ctx)
    return _fin(res, rng)


# ----------------------------------------------------------------- cross-check
def table_crosscheck(ctx: Context, include_info: bool = False):
    """None when fine or not comparable; else a warning string. Compares the
    search placements across granular and search_query, and summary where its
    period overlaps the granular dates."""
    msgs = []
    if ctx.has_rows("granular") and ctx.has_rows("search_query"):
        a = ctx.sql("SELECT SUM(total_budget_burnt) s, SUM(total_gmv) g, SUM(total_clicks) c FROM granular WHERE ad_property IN (?,?,?)",
                    ["Keyword Based Ads", "Search Inline Banner", "Guaranteed Top Slot Ads"]).iloc[0]
        b = ctx.sql("SELECT SUM(total_budget_burnt) s, SUM(total_gmv) g, SUM(total_clicks) c FROM search_query").iloc[0]
        for k, lab in (("s", "spend"), ("g", "GMV"), ("c", "clicks")):
            x, y = a[k], b[k]
            if x is not None and y is not None and not (pd.isna(x) or pd.isna(y)):
                if abs(x - y) > max(1.0, 0.001 * max(abs(x), abs(y))):
                    msgs.append(f"{lab}: granular {fmt.num(x)} vs search_query {fmt.num(y)}")
        lo1, hi1 = ctx.bounds("granular")
        lo2, hi2 = ctx.bounds("search_query")
        if (lo1, hi1) != (lo2, hi2):
            msgs.append(f"granular covers {lo1} to {hi1} but search_query covers {lo2} to {hi2}")
    if ctx.has_rows("summary") and ctx.has_rows("granular"):
        lo, hi = ctx.bounds("granular")
        s = ctx.sql("SELECT MIN(period_start) a, MAX(period_end) b FROM summary").iloc[0]
        if s["a"] is not None and not pd.isna(s["a"]) and (s["b"].date() < lo or s["a"].date() > hi):
            if include_info:
                msgs.append(f"summary covers {s['a'].date()} to {s['b'].date()}, which does not overlap granular ({lo} to {hi}), so it cannot be compared")
        else:
            sm = ctx.sql("SELECT SUM(total_budget_burnt) s FROM summary").iloc[0]["s"]
            gr = ctx.sql("SELECT SUM(total_budget_burnt) s FROM granular").iloc[0]["s"]
            if sm and gr and abs(sm - gr) > max(1.0, 0.001 * max(sm, gr)):
                msgs.append(f"total spend: summary {fmt.num(sm)} vs granular {fmt.num(gr)}")
    return msgs


def crosscheck_intent(ctx: Context, p: Params) -> Result:
    if not ctx.has_rows("granular") and not ctx.has_rows("search_query"):
        return empty_result()
    msgs = table_crosscheck(ctx, include_info=True)
    bad = [m for m in msgs if "cannot be compared" not in m]
    r = Result(headline=("Warning: the tables do not agree." if bad else "The tables agree for search placements."))
    r.notes += [(f"Warning: {m}" if m in bad else f"Fact: {m}") for m in msgs]
    return r


def unavailable(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("granular") and mode != "budget_log":
        return empty_result()
    if mode == "budget_log":
        r = data_not_loaded("budget or cost change log",
                            "Offer: a daily spend vs GMV trend for the period, with no causal claim (extra spend and extra GMV need a known change date).")
        if ctx.has_rows("granular"):
            d, rng = ctx.fetch("granular", ("week",), ctx.effective_period(p), None, order="week")
            r.tables.append(("Weekly spend and GMV (no causal claim)", d[["week", "spend", "gmv", "roas"]]))
            r.date_range = range_text(rng)
        return r
    r = data_not_loaded("stock/fill rate",
                        "Without it I cannot say which campaigns or SKU x city combinations kept spending on poor availability. Stock in transit never counts as available.")
    r.notes.append("Fact: placements without a SKU (Collection Ads, Auto Suggest, Search Inline Banner) may keep serving when products are out of stock.")
    return r
