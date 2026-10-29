"""Snackibella deep dive: WHAT / WHY / HOW / WHOM / SIMILARITY / CAUSE / RECOMMENDATION.

For the subject of an answer (a named city, keyword, SKU, campaign, placement or
search query, else the top row of the first table, else the whole account) this
pulls the subject's own figures and every first-degree affector (the dimensions
that sit directly next to it in the data) and writes them up in fixed templates.
Everything is computed from the loaded tables: no LLM, no network, no
hardcoded dates, cities or SKUs.

The similarity index is the same word-overlap (Jaccard) the Search Query Deep
Dive uses for broad-match intent gap: shared words / all unique words between
a search query and the keyword that served it.
"""
import pandas as pd

from . import fmt, metrics
from .core import DIMS, FILTERS, Context, Params, Result
from .normalise import short_sku
from .params import previous_period

GRAN_DIMS = ["campaign", "placement", "keyword", "match_type", "product", "city"]
SQ_DIMS = ["campaign", "placement", "keyword", "match_type", "product", "query"]
DIM_NAME = {"campaign": "Campaign", "placement": "Placement", "keyword": "Keyword", "match_type": "Match type",
            "product": "SKU", "city": "City", "query": "Search query"}
ENT_ORDER = [("queries", "query"), ("cities", "city"), ("keywords", "keyword"), ("products", "product"),
             ("campaigns", "campaign"), ("placements", "placement")]
LABEL_COLS = {"query": "query", "search_query": "query", "keyword": "keyword", "city": "city", "product": "product",
              "sku": "product", "campaign": "campaign", "placement": "placement"}
TAB_LABELS = (("📊", "What"), ("❓", "Why"), ("⚙️", "How"), ("🎯", "Whom"), ("🔗", "Similarity"), ("🧭", "Cause"), ("🎬", "Do next"))
GENERIC_SUGGESTIONS = ["What changes should I make this week?", "How did spend and ROAS change versus last week?",
                       "What should my negative keyword list be?", "Which cities should I cut, hold or scale?"]
GAP = 0.10         # within 10% of the peer counts as "in line" (labelled assumption in the output)
WEAK = 0.25        # a stage more than 25% worse than the peer is named as a cause
LEAK_SHARE = 0.25  # a slice below 1.00 ROAS holding this share of spend is called out as a drag
TIGHT, LOOSE = 0.6, 0.3  # similarity bands (assumption)


# ------------------------------------------------------------------ helpers
def jaccard(a, b):
    sa, sb = set(str(a).lower().split()), set(str(b).lower().split())
    u = sa | sb
    return len(sa & sb) / len(u) if u else None


def _num(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA else v


def _ratios(t: dict) -> dict:
    return {
        "roas": _num(t.get("roas")), "ctr": _num(t.get("ctr")), "a2c": _num(t.get("a2c_rate")),
        "cart_to_order": metrics.sdiv(_num(t.get("conversions")), _num(t.get("a2c"))),
        "cpm": _num(t.get("cpm")), "cpc": _num(t.get("cpc")),
        "cpa": metrics.sdiv(_num(t.get("spend")), _num(t.get("conversions"))),
    }


def _nm(v, dim):
    if v is None or str(v).strip().lower() in ("nan", "none", ""):
        return {"keyword": "(no keyword)", "product": "(no SKU)"}.get(dim, "(none)")
    return str(v)


def _dl(d):
    return "SKU" if d == "product" else DIM_NAME[d].lower()


def _vs(v, peer, higher_is_better=True):
    """(text, verdict) where verdict is 'good', 'bad' or 'flat'; text never uses an em dash."""
    if v is None or peer in (None, 0):
        return "n/a", "flat"
    d = (v - peer) / peer
    if abs(d) < GAP:
        return f"{abs(d) * 100:.0f}% {'above' if d > 0 else 'below'}, in line", "flat"
    good = (d > 0) == higher_is_better
    return f"{abs(d) * 100:.0f}% {'above' if d > 0 else 'below'}, {'strong' if good else 'weak'}", "good" if good else "bad"


def _resolve_products(ctx: Context, label):
    cat = ctx.catalog()
    hit = [x for x in cat.products if short_sku(x, ctx.sku_map) == label or str(x).lower() == str(label).lower()]
    return hit or [label]


def pick_focus(ctx: Context, p: Params, res: Result):
    for key, dim in ENT_ORDER:
        vals = p.ent(key)
        if vals:
            return {"dim": dim, "values": vals[:1], "label": str(vals[0])}
    if res.tables:
        df = res.tables[0][1]
        if df is not None and not df.empty:
            for c in df.columns:
                dim = LABEL_COLS.get(c)
                if dim:
                    label = df.iloc[0][c]
                    if label is None or str(label) in ("nan", "(no product)"):
                        continue
                    vals = _resolve_products(ctx, label) if dim == "product" else [str(label)]
                    return {"dim": dim, "values": vals, "label": str(label)}
    return {"dim": None, "values": [], "label": "the whole account"}


def _filters(focus):
    return {focus["dim"]: focus["values"]} if focus["dim"] else {}


def _spend_share_line(name, df, focus_spend, focus_roas):
    """'**x** 41% of spend at ROAS 2.10' for the top three rows plus a flagged drag."""
    parts, drag = [], []
    for _, r in df.head(3).iterrows():
        sh = metrics.sdiv(r["spend"], focus_spend)
        parts.append(f"**{_nm(r[name], name)}** {fmt.pct(sh) if sh is not None else 'n/a'} of spend at ROAS {fmt.roas(r['roas'])}")
    for _, r in df.iterrows():
        sh = metrics.sdiv(r["spend"], focus_spend)
        if r["roas"] is not None and not pd.isna(r["roas"]) and r["roas"] < 1 and sh is not None and sh >= 0.05:
            drag.append((sh, r))
    tail = ""
    if drag:
        sh, r = max(drag, key=lambda x: x[0])
        tail = f"; drag: **{_nm(r[name], name)}** (ROAS {fmt.roas(r['roas'])} on {fmt.pct(sh)} of spend)"
    return "; ".join(parts) + tail, drag


def _distinct(ctx: Context, table, dim, period, filters):
    wh, args = ctx.where(table, period, filters)
    expr = DIMS[table][dim]
    return int(ctx.con.execute(f"SELECT count(DISTINCT {expr}) FROM {table}{wh}", args).fetchone()[0] or 0)


# ------------------------------------------------------------- similarity
def similarity_block(ctx: Context, period, focus):
    """(lines, index) where index is the spend-weighted word overlap, or (None, None)."""
    if "search_query" not in ctx.tables() or not ctx.has_rows("search_query"):
        return [], None
    dim = focus["dim"]
    if dim == "city":
        return [], None  # the search query table has no city
    filt = _filters(focus)
    if dim in ("query", "keyword", "product", "campaign", "placement", None):
        pass
    df, _ = ctx.fetch("search_query", ("query", "keyword"), period, filt, limit=1500)
    if df.empty:
        return [], None
    df = df.copy()
    df["sim"] = [jaccard(q, k) for q, k in zip(df["query"], df["keyword"])]
    df = df[df["sim"].notna() & (df["spend"] > 0)]
    if df.empty:
        return [], None
    w = df["spend"].sum()
    index = float((df["sim"] * df["spend"]).sum() / w)
    band = "tight" if index >= TIGHT else "loose" if index >= LOOSE else "stretched"
    lines = [f"**Similarity index {index:.2f}** ({band}): spend-weighted word overlap between what shoppers typed and the "
             "keyword that served them (1.00 means identical words, 0.00 means no shared words)."]
    zero = df[df["sim"] == 0]
    if not zero.empty:
        zs = zero["spend"].sum()
        rest = df[df["sim"] > 0]
        zr = metrics.roas(zero["gmv"].sum(), zs)
        rr = metrics.roas(rest["gmv"].sum(), rest["spend"].sum()) if not rest.empty else None
        lines.append(f"{fmt.pct(zs / w)} of spend ({fmt.inr(zs)}) went to searches that share **no word** with the keyword, "
                     f"at ROAS {fmt.roas(zr)}" + (f" against {fmt.roas(rr)} where words overlap." if rr is not None else "."))
    if dim == "query":
        top = df.sort_values("spend", ascending=False).head(3)
        lines.append("Served by: " + "; ".join(f"**{r['keyword']}** (overlap {r['sim']:.2f}, {fmt.inr(r['spend'])}, ROAS {fmt.roas(r['roas'])})"
                                                for _, r in top.iterrows()) + ".")
        try:
            allq, _ = ctx.fetch("search_query", ("query",), period, {}, limit=2000)
            me = str(focus["label"]).lower()
            allq = allq[allq["query"] != me].copy()
            allq["sim"] = [jaccard(me, q) for q in allq["query"]]
            near = allq[allq["sim"] >= 0.3].sort_values(["sim", "spend"], ascending=False).head(3)
            if not near.empty:
                lines.append("Closest look-alike searches: " + "; ".join(
                    f"**{r['query']}** (similarity {r['sim']:.2f}, {fmt.inr(r['spend'])}, ROAS {fmt.roas(r['roas'])})" for _, r in near.iterrows()) + ".")
        except Exception:
            pass
    return lines, index


# ------------------------------------------------------------------ build
def build(ctx: Context, p: Params, res: Result) -> list:
    """Return [(title, markdown)] sections for the deep dive, or [] when there is nothing to dive into."""
    focus = pick_focus(ctx, p, res)
    dim = focus["dim"]
    table = "search_query" if dim == "query" else "granular"
    if not ctx.has_rows(table):
        return []
    period = ctx.effective_period(p, table)
    filt = _filters(focus)
    t, _ = ctx.totals(table, period, filt)
    if not t or not _num(t.get("spend")):
        return []
    spend, gmv = t["spend"], t["gmv"]
    r = _ratios(t)
    sections = []

    # ---- peer: the account (for a subject) or the previous period (for the whole account)
    if dim:
        peer_t, _ = ctx.totals(table, period, {})
        peer_name = "account"
    else:
        peer_t, peer_name = {}, "previous period"
        if period is not None:
            prev = p.prev or previous_period(period)
            peer_t, _ = ctx.totals(table, prev, {})
    pr = _ratios(peer_t) if peer_t and _num(peer_t.get("spend")) else None

    # ---- WHAT
    what = []
    what.append(f"- **Return:** ROAS {fmt.roas(r['roas'])} on {fmt.inr(spend)} spend and {fmt.inr(gmv)} GMV at MRP, "
                f"{fmt.num(t.get('conversions'))} orders over {int(t.get('days') or 0)} days.")
    if dim and peer_t:
        sh_s, sh_g = metrics.sdiv(spend, peer_t.get("spend")), metrics.sdiv(gmv, peer_t.get("gmv"))
        if sh_s is not None and sh_g is not None:
            what.append(f"- **Weight in the account:** {fmt.pct(sh_s)} of spend and {fmt.pct(sh_g)} of GMV"
                        + (f", so it earns {'more' if sh_g > sh_s else 'less'} than its spend share." if abs(sh_g - sh_s) > 0.01 else "."))
    what.append(f"- **Funnel:** {fmt.num(t.get('impressions'))} impressions, {fmt.num(t.get('clicks'))} clicks (CTR {fmt.pct(r['ctr'])}), "
                f"{fmt.num(t.get('a2c'))} carts (A2C {fmt.pct(r['a2c'])}), {fmt.num(t.get('conversions'))} orders "
                f"({fmt.pct(r['cart_to_order'])} of carts).")
    what.append(f"- **Unit cost:** CPM {fmt.inr(r['cpm'])}, CPC {fmt.inr(r['cpc'])}, cost per order {fmt.inr(r['cpa'])}.")
    sections.append((f"📊 WHAT is happening: {focus['label']}" + (f" ({DIM_NAME[dim].lower()})" if dim else ""), "\n".join(what)))

    # ---- WHY (every funnel stage against the peer)
    verdicts = {}
    if pr:
        rows = []
        for key, name, hib, f in (("roas", "ROAS", True, fmt.roas), ("ctr", "CTR", True, fmt.pct), ("a2c", "Add-to-cart rate", True, fmt.pct),
                                   ("cart_to_order", "Cart to order", True, fmt.pct), ("cpm", "CPM", False, fmt.inr),
                                   ("cpc", "CPC", False, fmt.inr), ("cpa", "Cost per order", False, fmt.inr)):
            txt, v = _vs(r[key], pr[key], hib)
            verdicts[key] = (v, r[key], pr[key])
            rows.append(f"| {name} | {f(r[key])} | {f(pr[key])} | {txt} |")
        head = f"| Stage | This | {peer_name.capitalize()} | Gap |\n|---|---|---|---|\n"
        sections.append((f"❓ WHY it looks like this (against the {peer_name})", head + "\n".join(rows)))
    else:
        sections.append(("❓ WHY it looks like this", "No earlier period is loaded, so there is nothing to compare against."))

    # ---- HOW: first-degree affectors
    how, drags, active = [], {}, {}
    dims = [d for d in (GRAN_DIMS if table == "granular" else SQ_DIMS) if d != dim]
    cities_info = None
    ent_names = {}
    for d in dims:
        df, _ = ctx.fetch(table, (d,), period, filt, limit=8)
        if df.empty:
            continue
        n = _distinct(ctx, table, d, period, filt)
        line, drag = _spend_share_line(d, df, spend, r["roas"])
        active[d] = n
        if d in ("campaign", "keyword", "city"):
            ent_names[d] = (df, drag)
        how.append(f"- **{DIM_NAME[d]}** ({n} active): {line}.")
        if drag:
            drags[d] = drag
        if d == "city":
            cities_info = (df, n)
    if dim not in (None, "query") and "search_query" in ctx.tables() and ctx.has_rows("search_query") and dim != "city":
        df, _ = ctx.fetch("search_query", ("query",), period, filt, limit=8)
        if not df.empty:
            line, drag = _spend_share_line("query", df, spend, r["roas"])
            how.append(f"- **Search query** ({_distinct(ctx, 'search_query', 'query', period, filt)} active): {line}.")
            if drag:
                drags["query"] = drag
    if dim is None:
        df, _ = ctx.fetch("granular", ("date",), period, {}, order="date")
        if len(df) >= 4:
            h = len(df) // 2
            a, b = df.iloc[:h], df.iloc[h:]
            ra, rb = metrics.roas(a["gmv"].sum(), a["spend"].sum()), metrics.roas(b["gmv"].sum(), b["spend"].sum())
            how.append(f"- **Time:** ROAS {fmt.roas(ra)} in the first half of the period and {fmt.roas(rb)} in the second half.")
    if how:
        sections.append(("⚙️ HOW it is being made (first-degree affectors)", "\n".join(how)))

    # ---- WHOM: concentration
    whom = []
    dfw = cities_info[0] if cities_info else None
    if dfw is not None and not dfw.empty:
        n = cities_info[1]
        top1 = metrics.sdiv(dfw.iloc[0]["spend"], spend)
        top3 = metrics.sdiv(dfw.head(3)["spend"].sum(), spend)
        whom.append(f"- Reaches **{n} cities**; **{_nm(dfw.iloc[0]['city'], 'city')}** takes {fmt.pct(top1)} of spend and the top three take {fmt.pct(top3)}.")
        if top1 is not None and top1 > 0.4:
            whom.append(f"- Hypothesis: a single-city dependency on {dfw.iloc[0]['city']}; any delivery, competition or stock change there moves the whole result.")
    else:
        for d in ("product", "keyword", "campaign"):
            if d in dims:
                df, _ = ctx.fetch(table, (d,), period, filt, limit=3)
                if not df.empty:
                    top1 = metrics.sdiv(df.iloc[0]["spend"], spend)
                    if top1 is not None and _nm(df.iloc[0][d], d).startswith("("):
                        whom.append(f"- {fmt.pct(top1)} of the spend here has no {_dl(d)} attached (placements such as Auto Suggest or Collection carry none).")
                    elif top1 is not None:
                        whom.append(f"- **{df.iloc[0][d]}** is {fmt.pct(top1)} of the spend here"
                                    + (", so the result rides on one name." if top1 > 0.6 else "."))
                break
    if whom:
        sections.append(("🎯 WHOM it reaches (concentration)", "\n".join(whom)))

    # ---- SIMILARITY
    sim_lines, sim_index = similarity_block(ctx, period, focus)
    if sim_lines:
        sections.append(("🔗 SIMILARITY index (shopper search vs keyword)", "\n".join(f"- {x}" for x in sim_lines)))

    # ---- CAUSE
    causes = []
    if pr:
        for key, label, text in (
                ("ctr", "CTR", "the ad is not earning the click, so look at creative, price and pack in the tile"),
                ("a2c", "Add-to-cart rate", "shoppers click and do not add, usually price, pack size or an unclear listing"),
                ("cart_to_order", "Cart to order", "carts are abandoned, so check delivery promise, minimum order value and stock")):
            v, a, b = verdicts.get(key, ("flat", None, None))
            if a is not None and b and a < b * (1 - WEAK):
                causes.append(f"Hypothesis: **{label} is {abs(a / b - 1) * 100:.0f}% under the {peer_name}**; {text}.")
        v, a, b = verdicts.get("cpm", ("flat", None, None))
        if a is not None and b and a > b * (1 + WEAK):
            causes.append(f"Hypothesis: **CPM is {abs(a / b - 1) * 100:.0f}% over the {peer_name}**; the same reach is costing more, so check bids, rank and placement mix.")
    ranked = sorted(((sum(x[0] for x in dg), d, dg) for d, dg in drags.items() if sum(x[0] for x in dg) >= LEAK_SHARE and active.get(d, 0) > 1),
                    key=lambda t: -t[0])
    for sh, d, drag in ranked[:3]:
        names = ", ".join(f"**{_nm(x[1][d], d)}**" for x in sorted(drag, key=lambda x: -x[0])[:3])
        causes.append(f"Fact: below-1.00 {_dl(d)} slices ({names}) hold {fmt.pct(sh)} of the spend here, so the {_dl(d)} mix is a direct drag.")
    if sim_index is not None and sim_index < LOOSE:
        causes.append(f"Hypothesis: the similarity index is only {sim_index:.2f}, so broad or loose matching is serving searches that are not really about this keyword.")
    if not causes:
        causes.append("No single stage is more than 25% off the " + peer_name + "; the gap, if any, is many small differences, so treat it with care on small volumes.")
    sections.append(("🧭 CAUSE: where this most likely comes from", "\n".join(f"- {c}" for c in causes)))

    # ---- RECOMMENDATION
    recs = []
    good = r["roas"] is not None and pr is not None and pr["roas"] and r["roas"] >= pr["roas"] * (1 + GAP) and r["roas"] >= 1
    be = ctx.break_even()
    if good:
        recs.append("Action: raise budget or bids on this in small steps and watch ROAS for a week before the next step.")
        for d in ("city", "keyword", "product"):
            if d in dims and d != dim:
                df, _ = ctx.fetch(table, (d,), period, filt, limit=15)
                if not df.empty:
                    med = df["spend"].median()
                    up = df[(df["roas"].notna()) & (df["roas"] > r["roas"]) & (df["spend"] < med)]
                    if not up.empty:
                        recs.append(f"Action: the best room is in {DIM_NAME[d].lower()} slices that beat this ROAS on below-median spend: "
                                    + ", ".join(f"**{x}**" for x in up.head(3)[d]) + ".")
                        break
        recs.append("Fact: stock and fill rate are not loaded, so confirm availability before scaling (data not loaded: stock/fill rate).")
    else:
        cand = [(sum(x[0] for x in dg), d, dg) for d, dg in drags.items() if active.get(d, 0) > 1 and sum(x[0] for x in dg) < 0.95]
        if cand:
            _, d, drag = max(cand, key=lambda t: t[0])
            names = ", ".join(f"**{_nm(x[1][d], d)}**" for x in sorted(drag, key=lambda x: -x[0])[:3])
            recs.append(f"Action: trim or pause the {_dl(d)} slices {names}, the biggest below-1.00 spend that is not the whole budget.")
        elif drags:
            recs.append("Action: every slice is below 1.00, so lower bids across the board and rebuild from the best keyword outward.")
        if verdicts.get("ctr", ("flat",))[0] == "bad":
            recs.append("Action: fix the tile first (hero image, price-forward copy) because bids cannot buy back a weak click rate.")
        if verdicts.get("cart_to_order", ("flat",))[0] == "bad":
            recs.append("Action: check stock, delivery promise and minimum order value in the cities taking the spend.")
        if sim_index is not None and sim_index < LOOSE:
            recs.append("Action: add the searches with no word overlap as negatives and move the converting ones to exact.")
        if not recs:
            recs.append("Action: hold the budget, let more days of data in, and re-run this check next week.")
    if be is None:
        recs.append("needs setting: contribution_before_ads_pct_of_mrp_gmv (to turn ROAS into a break-even test for scaling or cutting).")
    else:
        recs.append(f"Fact: break-even ROAS is {fmt.roas(be)}; this is {'above' if (r['roas'] or 0) >= be else 'below'} it at ROAS {fmt.roas(r['roas'])}.")
    sections.append(("🎬 RECOMMENDATION", "\n".join(f"- {x}" for x in recs)))
    sug = []
    for d, (df, drag) in ent_names.items():
        if drag:
            nm = _nm(max(drag, key=lambda x: x[0])[1][d], d)
            if not nm.startswith("("):
                sug.append(f"and for {nm}?")
        ok = df[(df["roas"].notna()) & (df["spend"] > 0)]
        if not ok.empty:
            nm = _nm(ok.sort_values("roas", ascending=False).iloc[0][d], d)
            if not nm.startswith("(") and f"and for {nm}?" not in sug:
                sug.append(f"and for {nm}?")
    res.suggestions = (sug[:2] + [g for g in GENERIC_SUGGESTIONS if g.lower().rstrip("?") != str(res.meta.get("text", "")).lower().rstrip("?")])[:4]
    return sections
