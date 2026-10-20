"""Actions, full review, budget moves and judgement answers."""
import math
from datetime import date, timedelta

import pandas as pd

from . import fmt, metrics
from .classify import contains_keyword
from .core import Context, Params, Result, empty_result, filters_from, needs_setting, num, range_text
from .helpers import lag_note, loss_below_break_even, sum_by, top
from .params import Period, previous_period
from . import q_cost, q_geo, q_search

DEFAULT_MAX_ACTIONS = 8
JUDGEMENT_NOTE = "Judgement answer needs a human review. Supporting data below."


def _fin(res, rng):
    res.date_range = range_text(rng)
    return res


def _protected(ctx, keyword, match_type, placement, query=None) -> bool:
    """Exact-match brand keywords and Guaranteed Top Slot on brand searches are
    never cut on ROAS alone."""
    brand = ctx.clf.bucket(keyword) == "defend" or (query is not None and ctx.clf.has_brand(query))
    return bool(brand and (match_type == "Exact" or (placement or "").startswith("Guaranteed Top Slot")))


def build_actions(ctx: Context, p: Params, cur: Period):
    """Candidate actions ranked by rupee impact, then confidence, then urgency.
    Returns (DataFrame, notes). Impact is spend saved (cuts) or GMV at stake
    (increase/test), normalised to a week."""
    notes = []
    wk = 7.0 / max(1, cur.days)
    be = ctx.break_even()
    floor = be or 1.0
    rows = []
    conf_base = "medium" if ctx.min_data_set() else "low"
    if not ctx.min_data_set():
        notes.append("needs setting: min_data (confidence is 'low' and nothing is a firm pause)")
    if be is None:
        notes.append("needs setting: contribution_before_ads_pct_of_mrp_gmv (ROAS 1.00 used as the floor)")
    mask_rule = ctx.set("waste_rule.spend_inr") is not None
    if not mask_rule:
        notes.append("needs setting: waste_rule (waste candidates use the break-even floor only)")

    # 1. negatives / bid cuts from search queries
    if ctx.has_rows("search_query"):
        sq, _ = ctx.fetch("search_query", ("campaign", "keyword", "match_type", "placement", "query"), cur, filters_from(p, "search_query"))
        sq = sq[(sq["spend"] > 0)]
        for r in sq.itertuples():
            loss = loss_below_break_even(r.spend, r.gmv, be)
            if not loss or loss <= 0:
                continue
            ok = ctx.enough_data(r.spend, r.clicks, r.days)
            if ok is False:
                continue
            if _protected(ctx, r.keyword, r.match_type, r.placement, r.query):
                continue
            spill = not contains_keyword(r.query, r.keyword)
            if r.match_type == "Broad" and spill:
                change = "add negative"
            elif r.match_type == "Broad":
                change = "change match type (to exact)"
            else:
                change = "reduce (lower bid)"
            rows.append(dict(action="cut", campaign=r.campaign, target=f"{r.keyword} / {r.query}", change=change,
                             reason=f"ROAS {fmt.roas(r.roas)} on {fmt.inr(r.spend)}", impact=loss * wk, kind="saving", confidence=conf_base))
    # 2. SKU x city x keyword fixes
    if ctx.has_rows("granular"):
        g, _ = ctx.fetch("granular", ("campaign", "keyword", "match_type", "city"), cur, filters_from(p, "granular"),
                         extra="keyword IS NOT NULL AND ad_property = 'Keyword Based Ads'")
        for r in g.itertuples():
            loss = loss_below_break_even(r.spend, r.gmv, be)
            if not loss or loss <= 0 or ctx.enough_data(r.spend, r.clicks, r.days) is False:
                continue
            if _protected(ctx, r.keyword, r.match_type, "Keyword Based Ads"):
                continue
            rows.append(dict(action="cut", campaign=r.campaign, target=f"{r.keyword} in {r.city}", change="reduce (lower bid)",
                             reason=f"ROAS {fmt.roas(r.roas)} on {fmt.inr(r.spend)}", impact=loss * wk, kind="saving", confidence=conf_base))
        # 3. campaigns to increase
        wh, args = ctx.where("granular", cur, filters_from(p, "granular"))
        d = ctx.sql("SELECT campaign_name c, metrics_date d, MAX(total_budget) b, SUM(total_budget_burnt) s, SUM(total_gmv) g FROM granular" + wh + " GROUP BY 1,2", args)
        step = ctx.set("budget_increase_step_pct")
        for c, x in d.groupby("c"):
            roas_ = metrics.roas(x["g"].sum(), x["s"].sum())
            full = (x["s"] >= q_cost.FULL_SPEND * x["b"]).mean()
            if roas_ is not None and roas_ >= floor and full >= 0.5 and be is not None:
                extra = (x["s"].sum() * wk) * ((step or 0) / 100.0)
                rows.append(dict(action="increase", campaign=c, target="daily budget", change=f"increase{' ' + str(step) + '%' if step else ''}",
                                 reason=f"ROAS {fmt.roas(roas_)}, budget fully spent on {fmt.pct(full)} of days; depends on stock, not in data",
                                 impact=extra * roas_, kind="gmv_at_stake", confidence=conf_base, extra_spend=extra))
    # 4. proven queries to test as exact keywords
    if ctx.has_rows("search_query") and ctx.has_rows("granular"):
        q, _ = ctx.fetch("search_query", ("campaign", "query"), cur, filters_from(p, "search_query"))
        exact = set(ctx.sql("SELECT DISTINCT lower(trim(keyword)) k FROM granular WHERE match_type LIKE '%EXACT'")["k"])
        win = q[(num(q, "roas") >= max(floor, ctx.set("scale_rule.roas_at_least") or 0)) & (~q["query"].isin(exact))]
        for r in top(win, 3, "gmv").itertuples():
            rows.append(dict(action="test", campaign=r.campaign, target=r.query, change="test (add as exact keyword)",
                             reason=f"proven: ROAS {fmt.roas(r.roas)}, {fmt.inr(r.gmv)} GMV", impact=r.gmv * wk, kind="gmv_at_stake", confidence="medium"))
    acts = pd.DataFrame(rows)
    if acts.empty:
        return acts, notes
    order = {"high": 0, "medium": 1, "low": 2}
    acts["_c"] = acts["confidence"].map(order)
    acts = acts.sort_values(["impact", "_c"], ascending=[False, True]).drop(columns="_c").reset_index(drop=True)
    return acts, notes


def _actions_result(ctx, p, cur, rng, limit=None):
    acts, notes = build_actions(ctx, p, cur)
    mx = ctx.set("max_weekly_actions")
    if mx is None:
        notes.append(f"needs setting: max_weekly_actions (showing {DEFAULT_MAX_ACTIONS})")
        mx = DEFAULT_MAX_ACTIONS
    if limit:
        mx = limit
    return acts, notes, int(mx)


def weekly_actions(ctx: Context, p: Params, mode="actions") -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    cur, _ = q_cost.week_pair(ctx, p)
    _, rng = ctx.totals("granular", cur)
    acts, notes, mx = _actions_result(ctx, p, cur, rng, 1 if mode == "one_thing" else None)
    res = Result()
    if acts.empty:
        res.headline = "No action clears the rules for this period."
        res.notes += notes
        return _fin(res, rng)
    t = acts.head(mx)
    cuts = acts[acts["action"] == "cut"].head(mx)
    inc = acts[acts["action"] == "increase"].head(mx)
    saved = cuts["impact"].sum()
    extra = inc["extra_spend"].sum() if "extra_spend" in inc else 0
    if mode == "one_thing":
        a = t.iloc[0]
        res.headline = f"Do this first: {a['change']} on {a['target']} ({a['campaign']}); worth about {fmt.inr(a['impact'])} a week ({'spend saved' if a['kind'] == 'saving' else 'GMV at stake'})."
    else:
        res.headline = (f"{len(t)} actions this week; cuts save about {fmt.inr(saved)} of spend"
                        + (f", increases add about {fmt.inr(extra)}. Net budget change {fmt.inr(extra - saved)}." if extra else f". Net budget change {fmt.inr(-saved)}."))
    show = t.assign(rupee_impact=t["impact"])[["action", "campaign", "target", "change", "reason", "rupee_impact", "confidence"]]
    res.tables.append((f"Actions, ranked by rupee impact ({cur.start} to {cur.end}, weekly basis)", show))
    res.notes.append("Fact: impact for cuts is spend not covered by contribution; for increases and tests it is GMV at stake at blended ROAS, an upper bound because marginal ROAS is unknown.")
    res.notes.append("Fact: exact-match brand keywords and Guaranteed Top Slot on brand searches are excluded from cuts.")
    res.notes += notes + lag_note(ctx, rng)[:2]
    return _fin(res, rng)


def full_review(ctx: Context, p: Params) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    cur, prev = q_cost.week_pair(ctx, p)
    h = q_cost.health(ctx, p, "wow")
    res = Result(headline=h.headline, date_range=h.date_range)
    res.tables += [("2. Numbers", h.tables[0][1])] if h.tables else []
    # 3. what drove it
    rows = []
    for dim in ("campaign", "placement", "city", "product"):
        d, _ = ctx.fetch("granular", (dim,), cur, None)
        if not d.empty:
            r = top(d, 1, "spend").iloc[0]
            rows.append(dict(by=dim, biggest=r[dim], spend=r["spend"], gmv=r["gmv"], roas=r["roas"]))
    sq, _ = ctx.fetch("search_query", ("query",), cur, None) if ctx.has_rows("search_query") else (pd.DataFrame(), None)
    if not sq.empty:
        r = top(sq, 1, "spend").iloc[0]
        rows.append(dict(by="search query", biggest=r["query"], spend=r["spend"], gmv=r["gmv"], roas=r["roas"]))
    mt, _ = ctx.fetch("granular", ("match_type",), cur, None, extra="match_type NOT LIKE '%INVALID'")
    if not mt.empty:
        r = top(mt, 1, "spend").iloc[0]
        rows.append(dict(by="match type", biggest=r["match_type"], spend=r["spend"], gmv=r["gmv"], roas=r["roas"]))
    b = q_search.bucket_split(ctx, p, "split")
    res.tables.append(("3. What drove it (biggest spender in each cut)", pd.DataFrame(rows)))
    res.tables += [("3b. Attack / defend / expand", b.tables[-1][1])] if b.tables else []
    # 4. waste
    w = q_search.query_waste(ctx, p, "top_wasteful")
    if w.tables:
        res.tables.append(("4. Where money is wasted (search queries)", w.tables[0][1]))
    else:
        res.sections.append(("4. Where money is wasted", w.headline))
    # 5. invest
    inv = q_cost.campaign_budget(ctx, p)
    if inv.tables:
        res.tables.append(("5. Where to invest more (stock unknown)", inv.tables[0][1]))
    # 6. actions
    acts, notes, mx = _actions_result(ctx, p, cur, None)
    if not acts.empty:
        t = acts.head(mx)
        res.tables.append(("6. This week's actions", t.assign(rupee_impact=t["impact"])[["action", "campaign", "target", "change", "reason", "rupee_impact"]]))
        saved = acts[acts["action"] == "cut"].head(mx)["impact"].sum()
        res.sections.append(("6b. Net budget change", f"Cuts save about {fmt.inr(saved)}; see increases in the table. Net change is shown on a weekly basis."))
    res.sections.append(("7. Risks and data gaps",
                         "data not loaded: stock/fill rate, new-to-brand, budget change log, SKU map for Collection Ads and similar. "
                         "ROAS is at MRP and overstates real revenue. " + " ".join(lag_note(ctx, (cur.start, cur.end))[:2])))
    res.sections.append(("8. How to measure",
                         "Next week, for each cut compare spend saved vs GMV lost on the same keyword or query; for each increase compare "
                         "marginal ROAS (extra GMV / extra spend) against break-even after 7 days; for each test check clicks, conversion and ROAS on the new exact keyword."))
    res.notes += notes + h.notes[:2]
    return res


# --------------------------------------------------------------- budget moves
def budget_move(ctx: Context, p: Params, mode: str) -> Result:
    if not ctx.has_rows("granular"):
        return empty_result()
    cur, _ = q_cost.week_pair(ctx, p)
    _, rng = ctx.totals("granular", cur)
    amount = (p.entities.get("amount") or [None])[0]
    be = ctx.break_even()
    wk = 7.0 / max(1, cur.days)
    res = Result()
    if mode == "table":
        return _campaign_table(ctx, p, cur, rng)
    if amount is None:
        return _fin(Result(headline="How much? Say the rupee amount (for example 'cut 50000')", kind="unknown"), rng)
    if mode == "cut":
        k, _ = ctx.fetch("granular", ("campaign", "keyword", "match_type"), cur, None, extra="keyword IS NOT NULL")
        k = k[[not _protected(ctx, r.keyword, r.match_type, None) for r in k.itertuples()]]
        k = k[[ctx.enough_data(r.spend, r.clicks, r.days) is not False for r in k.itertuples()]]
        k = k.assign(weekly_spend=k["spend"] * wk).sort_values("roas", key=lambda s: pd.to_numeric(s, errors="coerce"))
        left, rows = float(amount), []
        for r in k.itertuples():
            if left <= 0:
                break
            cut = min(r.weekly_spend, left)
            rows.append(dict(campaign=r.campaign, keyword=r.keyword, match_type=r.match_type, cut=cut, roas=r.roas,
                             gmv_given_up=cut * (r.roas or 0)))
            left -= cut
        t = pd.DataFrame(rows)
        res.headline = (f"To cut {fmt.inr(amount)} a week, take it from {len(t)} keywords starting with the lowest ROAS; this gives up about {fmt.inr(t['gmv_given_up'].sum())} GMV (at MRP)."
                        if not t.empty else "No keyword qualifies for a cut after protections and min data.")
        if not t.empty:
            res.tables.append(("Where to cut (lowest ROAS first)", t))
            if left > 0:
                res.notes.append(f"Only {fmt.inr(amount - left)} could be found without touching protected brand defence or too-little-data keywords.")
        res.notes.append("Assumption: GMV given up assumes the cut keyword keeps its average ROAS (proportional), so it is an estimate; check marginal ROAS after a week.")
        res.notes.append("Fact: exact-match brand keywords and Guaranteed Top Slot on brand searches are excluded.")
    else:  # add
        if be is None:
            return _fin(needs_setting("contribution_before_ads_pct_of_mrp_gmv", "I will not place extra money without a break-even ROAS."), rng)
        inc = q_cost.campaign_budget(ctx, p)
        if not inc.tables:
            return _fin(Result(headline="No campaign clears the bar for extra budget."), rng)
        t = inc.tables[0][1].copy()
        step = ctx.set("budget_increase_step_pct")
        n = len(t)
        if step is not None:
            cap = [r.daily_budget * 7 * step / 100 for r in t.itertuples()]
        else:
            cap = [float(amount) / n] * n
        left, alloc = float(amount), []
        for c in cap:
            a = min(c, left)
            alloc.append(a)
            left -= a
        t["extra_per_week"] = alloc
        t["gmv_at_blended_roas"] = [a * (r or 0) for a, r in zip(alloc, t["roas"])]
        res.headline = f"Put {fmt.inr(sum(alloc))} of the {fmt.inr(amount)} into {len([a for a in alloc if a > 0])} campaigns that clear break-even."
        res.tables.append(("Where to add (best ROAS first)", t[["campaign", "roas", "daily_budget", "extra_per_week", "gmv_at_blended_roas"]]))
        if left > 0:
            res.notes.append(f"{fmt.inr(left)} is left over: the step cap (budget_increase_step_pct) limits how much each campaign can take. Hold it or test new exact keywords.")
        if step is None:
            res.notes.append("needs setting: budget_increase_step_pct (split evenly instead)")
        res.notes.append("Fact: GMV at blended ROAS is an upper bound; the real return is the marginal ROAS, judged after 7 days. depends on stock, not in data.")
    return _fin(res, rng)


def _campaign_table(ctx, p, cur, rng):
    be = ctx.break_even()
    d, _ = ctx.fetch("granular", ("campaign",), cur, filters_from(p, "granular"))
    if be is None:
        r = needs_setting("contribution_before_ads_pct_of_mrp_gmv", "A pause/reduce/hold/scale table needs break-even ROAS. Campaigns by ROAS below.")
        r.tables.append(("Campaigns", top(d, 20, "spend")[["campaign", "spend", "gmv", "roas"]]))
        return _fin(r, rng)
    sr, ss = ctx.set("scale_rule.roas_at_least"), ctx.set("scale_rule.spend_inr")
    acts = []
    for r in d.itertuples():
        ok = ctx.enough_data(r.spend, r.clicks, r.days)
        zero = ctx.set("waste_rule.zero_gmv_spend_inr")
        if ok is False:
            a = "hold (too little data)"
        elif zero is not None and r.spend >= zero and (r.gmv or 0) == 0:
            a = "pause"
        elif (r.roas or 0) < be:
            a = "reduce"
        elif sr is not None and ss is not None and (r.roas or 0) >= sr and r.spend >= ss:
            a = "scale (depends on stock, not in data)"
        else:
            a = "hold"
        acts.append(a)
    d = d.assign(action=acts)
    res = Result(headline="Campaign actions: " + ", ".join(f"{k} {v}" for k, v in d["action"].str.split(" ").str[0].value_counts().items()) + ".")
    res.tables.append(("Pause / reduce / hold / scale", top(d, 25, "spend")[["campaign", "spend", "gmv", "roas", "action"]]))
    if sr is None or ss is None:
        res.notes.append("needs setting: scale_rule (no campaign is marked scale)")
    if not ctx.min_data_set():
        res.notes.append("needs setting: min_data")
    return _fin(res, rng)


def keyword_payback(ctx: Context, p: Params) -> Result:
    """At what daily spend does each top keyword stop paying back? Observed in
    this data only: days are split into three spend bands and the incremental
    ROAS between bands is compared with break-even."""
    if not ctx.has_rows("granular"):
        return empty_result()
    be = ctx.break_even()
    period = ctx.effective_period(p)
    wh, args = ctx.where("granular", period, filters_from(p, "granular"), "keyword IS NOT NULL")
    d = ctx.sql("SELECT keyword, metrics_date d, SUM(total_budget_burnt) s, SUM(total_gmv) g FROM granular" + wh + " GROUP BY 1,2", args)
    if d.empty:
        return empty_result()
    rng = (d["d"].min().date(), d["d"].max().date())
    if be is None:
        return _fin(needs_setting("contribution_before_ads_pct_of_mrp_gmv", "Payback needs break-even ROAS."), rng)
    tops = d.groupby("keyword")["s"].sum().sort_values(ascending=False).head(p.top_n).index
    rows = []
    for k in tops:
        x = d[d["keyword"] == k].sort_values("s")
        if len(x) < 9:
            rows.append(dict(keyword=k, result="too little data (fewer than 9 days)"))
            continue
        bands = [x.iloc[i * len(x) // 3:(i + 1) * len(x) // 3] for i in range(3)]
        m = [(b["s"].mean(), b["g"].mean()) for b in bands]
        inc = [metrics.marginal_roas(m[i][1], m[i][0], m[i + 1][1], m[i + 1][0]) for i in range(2)]
        stop = next((f"around {fmt.inr(m[i + 1][0])} a day" for i in range(2) if inc[i] is not None and inc[i] < be), "not reached in observed range")
        rows.append(dict(keyword=k, low_band=m[0][0], mid_band=m[1][0], high_band=m[2][0],
                         incremental_roas_low_to_mid=inc[0], incremental_roas_mid_to_high=inc[1], stops_paying_back=stop, result="ok"))
    res = Result(headline=f"Break-even ROAS is {fmt.roas(be)}; incremental ROAS below it means the extra spend did not pay back.")
    res.tables.append(("Payback by daily spend band", pd.DataFrame(rows)))
    res.notes.append("Hypothesis: observed spend-vs-return across days is confounded by demand swings and weekdays; treat as a lead to test with a controlled budget step.")
    return _fin(res, rng)


def phasing(ctx: Context, p: Params) -> Result:
    need = [k for k in ("monthly_budget_inr", "monthly_gmv_target_inr", "a2s_ceiling_pct") if ctx.set(k) is None]
    lo, hi = ctx.bounds()
    if lo is None:
        return empty_result()
    ms = hi.replace(day=1)
    t, rng = ctx.totals("granular", Period(ms, hi, "month to date"))
    if need:
        r = needs_setting(", ".join(need), f"Month to date ({ms} to {hi}): spend {fmt.inr(t.get('spend'))}, GMV {fmt.inr(t.get('gmv'))}.")
        return _fin(r, rng)
    nxt = date(hi.year + (hi.month == 12), (hi.month % 12) + 1, 1)
    left_days = (nxt - hi).days - 1
    bud, tgt = ctx.set("monthly_budget_inr"), ctx.set("monthly_gmv_target_inr")
    rem_b, rem_g = bud - t["spend"], tgt - t["gmv"]
    need_roas = metrics.sdiv(rem_g, rem_b)
    res = Result(headline=f"{left_days} days left: {fmt.inr(rem_b)} budget remaining ({fmt.inr(metrics.sdiv(rem_b, left_days) if left_days else 0)} a day) and {fmt.inr(rem_g)} GMV still needed, "
                          f"which needs ROAS {fmt.roas(need_roas)} on the remaining spend (month to date: {fmt.roas(t['roas'])}).")
    res.notes.append("A2S check: " + ("keep spend at or below the ceiling share of sales; compute with a2s_sales_basis once it is a number or 'total_gmv'."))
    res.notes.append("Assumption: the month is the calendar month of the latest loaded date, and the remaining days spend evenly.")
    return _fin(res, rng)


# ------------------------------------------------------------------- judgement
PRINCIPLES = [
    "Profitable growth beats revenue growth.",
    "Every rupee of extra spend should earn its keep; judge it on its own, not hidden in an average.",
    "Stock first, ads second.",
    "Systems beat one-off fixes: if a problem keeps coming back, set a rule or a recurring check.",
]


def judgement(ctx: Context, p: Params, qn: int) -> Result:
    """Q103 to Q111: supporting numbers plus the fixed note."""
    if not ctx.has_rows("granular"):
        return empty_result()
    cur, prev = q_cost.week_pair(ctx, p)
    h = q_cost.health(ctx, p, "wow")
    res = Result(headline=JUDGEMENT_NOTE, date_range=h.date_range)
    notes = res.notes
    if qn in (103, 104):
        why = q_cost.health(ctx, p, "why")
        res.tables += why.tables
        notes.append(why.headline)
        if qn == 104:
            wk, _ = ctx.fetch("granular", ("week",), ctx.effective_period(p), None, order="week")
            if len(wk) >= 3:
                s = [r for r in wk["roas"] if r is not None]
                sign = [1 if b > a else -1 for a, b in zip(s, s[1:])]
                notes.append(f"Fact: weekly ROAS moved {'up' if sum(sign) > 0 else 'down'} in {abs(sum(sign)) if sign else 0} net steps across {len(wk)} weeks. A single drop in the latest week is a one-off candidate; several down weeks in a row point to a structural issue.")
            res.tables.append(("Weekly ROAS", wk[["week", "spend", "gmv", "roas"]]))
        notes += lag_note(ctx, (cur.start, cur.end))
    elif qn == 105:
        res.tables += h.tables[:1]
        notes.append("ROAS is at MRP, so it overstates real revenue.")
        ns = q_cost.health(ctx, p, "no_sku")
        notes.append(ns.headline)
        notes += lag_note(ctx, (cur.start, cur.end))
        notes += [f"Warning: {m}" for m in q_cost.table_crosscheck(ctx)]
        t, _ = ctx.fetch("granular", ("campaign",), cur, None)
        if not t.empty:
            r = top(t, 1, "gmv").iloc[0]
            share = metrics.sdiv(r["gmv"], t["gmv"].sum())
            notes.append(f"Concentration: {r['campaign']} drives {fmt.pct(share)} of GMV; a good blended ROAS may rest on one campaign.")
        notes.append("Conversion above 100% on Collection Ads, Search Inline Banner and Auto Suggest is not comparable with keyword ads.")
    elif qn == 106:
        missing = [k for k in ("contribution_before_ads_pct_of_mrp_gmv", "min_data", "waste_rule", "lag_days", "scale_rule") if
                   ctx.set(k) is None and not (k in ("min_data", "waste_rule", "scale_rule") and ctx.set(k + ".spend_inr" if k != "scale_rule" else "scale_rule.roas_at_least") is not None)]
        notes.append("Least certain: every scale or increase recommendation, because stock is not in the data.")
        notes += [f"Also uncertain because a setting is empty: {k}" for k in missing]
        res.tables += h.tables[:1]
    elif qn == 107:
        notes.append("Data that would change the advice: stock and fill rate by SKU and city (scale decisions), a budget change log (marginal ROAS), new-to-brand data (attack judgement), organic sales (ad-driven vs demand growth).")
        notes += [f"needs setting: {k}" for k in ("contribution_before_ads_pct_of_mrp_gmv", "lag_days", "min_data.spend_inr", "waste_rule.spend_inr", "split_target.attack") if ctx.set(k) is None]
    elif qn == 108:
        be = ctx.break_even()
        res.tables += h.tables[:1]
        if be is None:
            notes.append("needs setting: contribution_before_ads_pct_of_mrp_gmv, so I cannot say whether extra spend clears break-even.")
        else:
            notes.append(f"Break-even ROAS is {fmt.roas(be)}. A higher target is only worth chasing if the extra rupees return at least that (marginal ROAS), not the blended ROAS.")
        notes.append("Challenge: blended ROAS hides diminishing returns; stock is not in the data; ROAS is at MRP. Test a 15 to 20% step on the best campaign and judge after 7 days.")
    elif qn == 109:
        a = h.tables[0][1].set_index("metric") if h.tables else None
        if a is not None:
            roas_ = a.loc["roas"]
            res.headline = "Draft for review: " + JUDGEMENT_NOTE
            notes += [
                f"1. Instamart ads {cur.start} to {cur.end}: spend {fmt.inr(a.loc['spend', 'current'])}, GMV {fmt.inr(a.loc['gmv', 'current'])}, ROAS {fmt.roas(roas_['current'])} (at MRP).",
                f"2. Versus the previous period: spend {fmt.signed_pct(a.loc['spend', 'change'])}, GMV {fmt.signed_pct(a.loc['gmv', 'change'])}, ROAS {fmt.signed_pct(roas_['change'])}.",
                "3. Biggest driver: see the campaign and city tables in the full review (not asserted here).",
                "4. Main risk: stock availability and attribution lag are not confirmed in the data.",
                "5. Next step: run the weekly actions table and re-check marginal ROAS in 7 days.",
            ]
    elif qn == 110:
        notes.append("Principle to take away (from your own rules): " + PRINCIPLES[0] + " " + PRINCIPLES[1])
        res.tables += h.tables[:1]
    elif qn == 111:
        t, rng = ctx.totals("granular", cur)
        be = ctx.break_even()
        if t:
            target_gmv = t["gmv"] * 1.30
            need_spend = metrics.sdiv(target_gmv, t["roas"])
            notes.append(f"Arithmetic: 30% more GMV is {fmt.inr(target_gmv)} a period (from {fmt.inr(t['gmv'])}); at the current blended ROAS {fmt.roas(t['roas'])} that needs {fmt.inr(need_spend)} of spend, {fmt.inr(need_spend - t['spend'])} more.")
        notes += [
            "Option 1: cut waste first and recycle it into winners (low risk; needs the waste list).",
            "Option 2: scale proven campaigns in 15 to 20% steps (upside high; depends on stock, not in data).",
            "Option 3: add proven search queries as exact keywords (cheap test; limited volume).",
            "Option 4: shift budget between cities by share-of-GMV vs share-of-spend (medium; check availability).",
            "Option 5: widen exact keywords with strong ROAS and few impressions (medium; watch CPM).",
            "Recommendation: start with Option 1 plus a 15% step on the single best campaign, and test Option 3 with a small budget. First question: is stock available in the top cities?",
        ]
        if be is None:
            notes.append("needs setting: contribution_before_ads_pct_of_mrp_gmv (the break-even floor for 'not below break-even')")
        if ctx.set("monthly_gmv_target_inr") is None:
            notes.append("needs setting: monthly_gmv_target_inr")
    elif qn in (112,):
        pass
    return _fin(res, (cur.start, cur.end))
