"""Turn a Result into text-only markdown: answer first, small tables, no charts,
no em dashes. Rupees use Indian grouping."""
import datetime as _dt

import pandas as pd

from . import fmt
from .core import NO_DATA, Result

MONEY = {"spend", "gmv", "lost", "cut", "extra_per_week", "gmv_given_up", "gmv_at_blended_roas", "rupee_impact", "impact",
         "spill_spend", "budget", "daily_budget", "avg_daily_spend", "low_band", "mid_band", "high_band", "spend_prev", "gmv_prev",
         "gmv_change", "spend_change", "budget_total", "weekly_spend", "value", "extra_spend"}
UNIT_MONEY2 = {"cpc", "cpm", "cost_per_order", "cpm_prev", "cpc_prev"}
PCT = {"ctr", "conv_rate", "a2c_rate", "share", "spend_share", "gmv_share", "utilisation", "spill_share", "target", "ctr_prev"}
SIGNED = {"change", "roas_change", "cpm_change", "ctr_change", "conv_change", "cost_change", "effect", "vs_avg", "ctr_change"}
ROAS = {"roas", "roas_prev", "kw_roas", "kw_roas_without", "marginal_roas", "sku_avg_roas", "incremental_roas_low_to_mid",
        "incremental_roas_mid_to_high", "score", "share_index", "avg_rank", "cpm_vs_account"}
COUNTS = {"impressions", "clicks", "a2c", "conversions", "days", "days_full", "queries", "impressions_prev", "clicks_prev"}


def _cell(col: str, v):
    if v is None or (isinstance(v, float) and v != v) or v is pd.NA or v is pd.NaT:
        return fmt.NA
    if isinstance(v, (pd.Timestamp, _dt.date, _dt.datetime)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str):
        return v if len(v) <= 70 else v[:69] + "~"
    c = col.lower()
    if c in UNIT_MONEY2 or c.endswith("_cpm") or c.endswith("_cpc"):
        return fmt.inr(v, 2)
    if c in SIGNED or c.endswith("_change"):
        return fmt.signed_pct(v) if abs(float(v)) < 1000 else fmt.num(v)
    if c in PCT:
        return fmt.pct(v)
    if c in MONEY:
        return fmt.inr(v)
    if c in ROAS:
        return fmt.roas(v)
    if c in COUNTS:
        return fmt.num(v)
    if isinstance(v, (int, float)):
        return fmt.num(v) if float(v).is_integer() else f"{float(v):.2f}"
    return str(v)


def _md_table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "_No rows._"
    cols = list(df.columns)
    head = "| " + " | ".join(c.replace("_", " ") for c in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    # the metric/current/previous table has a different format per row
    per_row = set(cols) >= {"metric", "current", "previous"}
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            if per_row and c in ("current", "previous"):
                cells.append(_metric_cell(r["metric"], r[c]))
            elif per_row and c == "change":
                cells.append(fmt.signed_pct(r[c]))
            else:
                cells.append(_cell(c, r[c]))
        rows.append("| " + " | ".join(str(x).replace("|", "/") for x in cells) + " |")
    return "\n".join([head, sep] + rows)


def _metric_cell(metric, v):
    if metric in ("spend", "gmv"):
        return fmt.inr(v)
    if metric in ("cpm", "cpc"):
        return fmt.inr(v, 2)
    if metric == "roas":
        return fmt.roas(v)
    if metric == "ctr":
        return fmt.pct(v)
    return fmt.num(v)


def render_parts(res: Result):
    """(prose, tables) as markdown. Prose leads with the date range and the
    answer, weaves the figures into sentences and groups the notes; the tables
    are the supporting numbers."""
    from .narrate import narrate
    if res.kind == "empty":
        return fmt.clean(NO_DATA), ""
    nl = chr(10) * 2
    head = f"_Date range: {res.date_range}_" + nl if res.date_range else ""
    prose = fmt.clean(head + narrate(res))
    tables = nl.join(f"**{t}**" + nl + _md_table(df) for t, df in res.tables)
    return prose, fmt.clean(tables)


def render(res: Result) -> str:
    """One markdown string: prose first, then the supporting tables."""
    prose, tables = render_parts(res)
    nl = chr(10) * 2
    return prose + (nl + "---" + chr(10) + "_Supporting numbers_" + nl + tables if tables else "")


def render_struct(res: Result) -> dict:
    """The same answer as render_parts, split for the chat UI: headline, lead
    paragraph, deep-dive sections (kept as separate blocks for tabs), other
    sections and notes, supporting tables, and follow-up suggestions."""
    from . import narrate as nar
    from .deepdive import TAB_LABELS
    if res.kind == "empty":
        return {"head": fmt.clean(NO_DATA), "date": "", "lead": "", "deep": [], "extra": "", "notes": "", "tables": "", "suggestions": []}
    nl = chr(10) * 2
    prefixes = tuple(e for e, _ in TAB_LABELS)
    deep = [(next(lbl for e, lbl in TAB_LABELS if t.startswith(e)), fmt.clean(t), fmt.clean(x)) for t, x in res.sections if t.startswith(prefixes)]
    other = [(t, x) for t, x in res.sections if not t.startswith(prefixes)]
    lead = nl.join(d for i, (t, df) in enumerate(res.tables) for d in [nar.describe_table(t, df, full=(i == 0))] if d and i == 0)
    extra = nl.join(f"**{t}**" + nl + x for t, x in other)
    notes = nl.join(nar.narrate_notes(list(res.notes) + nar.manager_read(res)))
    tables = nl.join(f"**{t}**" + nl + _md_table(df) for t, df in res.tables)
    return {"date": res.date_range or "", "head": fmt.clean(res.headline or ""), "lead": fmt.clean(lead), "deep": deep,
            "extra": fmt.clean(extra), "notes": fmt.clean(notes), "tables": fmt.clean(tables), "suggestions": list(res.suggestions)}
