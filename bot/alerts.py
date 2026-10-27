"""Load-time alerts. Runs on dashboard open and surfaces anything that would
make an ecom manager look twice: a big WoW ROAS drop, waste spend above the
scale rule, or missing settings that gate recommendations.

No LLM, no new tables: pure DuckDB totals and the loaded settings.
"""
from datetime import timedelta

from . import metrics
from .core import Context
from .params import Period


def _wow_change(ctx: Context):
    """ROAS change on the latest full week vs the previous full week."""
    lo, hi = ctx.bounds("granular")
    if lo is None or (hi - lo).days < 14:
        return None
    cur = Period(hi - timedelta(days=6), hi, "latest 7 days")
    prev = Period(cur.start - timedelta(days=7), cur.start - timedelta(days=1), "previous 7 days")
    a, _ = ctx.totals("granular", cur)
    b, _ = ctx.totals("granular", prev)
    if not a or not b:
        return None
    ra = metrics.roas(a.get("gmv"), a.get("spend"))
    rb = metrics.roas(b.get("gmv"), b.get("spend"))
    return {"cur_roas": ra, "prev_roas": rb, "cur_spend": a.get("spend"), "prev_spend": b.get("spend"),
            "cur_period": cur, "prev_period": prev}


def _waste_above_rule(ctx: Context):
    """Spend on queries with ROAS below the waste threshold, over the latest
    week. Returns None when waste_rule.roas_below is unset."""
    thr = ctx.set("waste_rule.roas_below")
    if thr is None or not ctx.has_rows("search_query"):
        return None
    lo, hi = ctx.bounds("search_query")
    if lo is None:
        return None
    cur = Period(max(lo, hi - timedelta(days=6)), hi, "latest 7 days")
    df, _ = ctx.fetch("search_query", ("query",), cur)
    if df.empty:
        return None
    bleed = df[df["roas"].fillna(0) < float(thr)]
    return {"spend": float(bleed["spend"].sum()), "queries": int(len(bleed)), "threshold": float(thr), "period": cur}


def build(ctx: Context) -> list:
    """[(level, message)] where level is 'warn' or 'info'. Empty list = quiet."""
    out = []
    try:
        wow = _wow_change(ctx)
        if wow and wow["prev_roas"] and wow["cur_roas"] is not None:
            ch = (wow["cur_roas"] - wow["prev_roas"]) / wow["prev_roas"]
            if ch <= -0.10:
                out.append(("warn", f"ROAS fell {abs(ch) * 100:.1f}% week on week ({wow['prev_roas']:.2f} to {wow['cur_roas']:.2f})."))
            elif ch >= 0.10:
                out.append(("info", f"ROAS up {ch * 100:.1f}% week on week ({wow['prev_roas']:.2f} to {wow['cur_roas']:.2f})."))
    except Exception:
        pass
    try:
        w = _waste_above_rule(ctx)
        if w and w["spend"] > 0:
            out.append(("warn", f"₹{int(w['spend']):,} on {w['queries']} queries below ROAS {w['threshold']:.2f} in the latest week."))
    except Exception:
        pass
    return out
