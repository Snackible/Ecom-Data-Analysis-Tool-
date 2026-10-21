"""Local natural-language generation: turn a Result into readable prose.

Everything is computed from the result tables (never estimated) and phrased
with fixed templates: no LLM, no network. Figures sit inside the sentences;
the tables stay available as supporting numbers.
"""
import re

import pandas as pd

from . import fmt, metrics
from .answers import _cell
from .core import Result

TIME_COLS = ("date", "week", "month")
# (column, phrase template) in the order metrics are mentioned
PHRASE = {
    "spend": "{v} spend", "gmv": "{v} GMV", "roas": "ROAS {v}", "clicks": "{v} clicks", "ctr": "CTR {v}",
    "cpm": "CPM {v}", "cpc": "CPC {v}", "conv_rate": "{v} conversion", "impressions": "{v} impressions",
    "lost": "{v} below break-even", "spill_share": "{v} off-keyword", "spend_share": "{v} of spend",
    "gmv_share": "{v} of GMV", "share": "{v} share", "cut": "{v} to cut", "extra_per_week": "{v} extra a week",
    "conversions": "{v} orders", "cost_per_order": "{v} per order", "utilisation": "{v} of budget used",
    "avg_rank": "ad rank {v}", "spill_spend": "{v} on off-keyword searches", "roas_change": "ROAS {v}",
}
PREFER = ["spend", "gmv", "roas", "clicks", "ctr", "cpm", "conv_rate", "impressions"]
LEAD_COLS = ["lost", "cut", "extra_per_week", "spill_spend", "spend", "gmv", "impressions", "clicks"]
NUMERIC_KINDS = "biufc"


def _is_num(s: pd.Series) -> bool:
    return pd.to_numeric(s, errors="coerce").notna().sum() > 0 and s.map(lambda x: isinstance(x, str)).sum() == 0


def _label_cols(df):
    return [c for c in df.columns if c not in TIME_COLS and not _is_num(df[c])]


def _label(row, cols):
    parts = [str(row[c]) for c in cols[:3] if row[c] is not None and str(row[c]) != "nan"]
    return " / ".join(parts[:2]) if len(parts) > 2 and len(" / ".join(parts)) > 50 else " / ".join(parts)


def _p(col, v):
    t = PHRASE.get(col)
    val = _cell(col, v)
    if val == fmt.NA:
        return None
    return t.format(v=val) if t else f"{col.replace('_', ' ')} {val}"


def _item(row, label_cols, metric_cols, lead):
    bits = [x for x in (_p(c, row[c]) for c in metric_cols) if x]
    return f"**{_label(row, label_cols)}** ({', '.join(bits)})" if bits else f"**{_label(row, label_cols)}**"


def _pick_metrics(df, lead, k=3):
    cols = [lead] if lead in df.columns else []
    for c in PREFER:
        if c in df.columns and c not in cols and len(cols) < k:
            cols.append(c)
    for c in df.columns:
        if len(cols) >= k:
            break
        if c not in cols and c not in TIME_COLS and _is_num(df[c]) and c in PHRASE:
            cols.append(c)
    return cols


def _num(df, c):
    return pd.to_numeric(df[c], errors="coerce")


def _series_text(df, tcol):
    unit = {"date": "days", "week": "weeks", "month": "months"}[tcol]
    out = []
    n = len(df)
    for c in ("spend", "gmv", "roas", "cpm"):
        if c in df.columns and _num(df, c).notna().sum() >= 2:
            v = _num(df, c)
            hi, lo = v.idxmax(), v.idxmin()
            first, last = v.dropna().iloc[0], v.dropna().iloc[-1]
            name = {"spend": "Spend", "gmv": "GMV", "roas": "ROAS", "cpm": "CPM"}[c]
            ch = metrics.growth(last, first)
            s = (f"{name} went from {_cell(c, first)} to {_cell(c, last)} over {n} {unit}"
                 + (f" ({fmt.signed_pct(ch)})" if ch is not None else "")
                 + f", peaking on {_cell(tcol, df.loc[hi, tcol])} at {_cell(c, v[hi])} and bottoming on {_cell(tcol, df.loc[lo, tcol])} at {_cell(c, v[lo])}.")
            out.append(s)
        if len(out) >= 2:
            break
    return " ".join(out)


def _compare_text(df):
    rows = {r["metric"]: r for _, r in df.iterrows()}
    parts = []
    for m in ("spend", "gmv", "roas", "cpm", "ctr", "conversions"):
        r = rows.get(m)
        if r is None:
            continue
        cur, prev, ch = r["current"], r["previous"], r["change"]
        word = "up" if (ch or 0) > 0 else "down" if (ch or 0) < 0 else "flat"
        name = {"spend": "Spend", "gmv": "GMV", "roas": "ROAS", "cpm": "CPM", "ctr": "CTR", "conversions": "Orders"}[m]
        cell = lambda v: _cell({"spend": "spend", "gmv": "gmv", "roas": "roas", "cpm": "cpm", "ctr": "ctr", "conversions": "conversions"}[m], v)
        be = "are" if m == "conversions" else "is"
        if ch is None:
            parts.append(f"{name} {be} {cell(cur)} (no comparable earlier figure)")
        else:
            parts.append(f"{name} {be} {cell(cur)}, {word} {abs(ch) * 100:.1f}% from {cell(prev)}")
    movers = [(abs(r["change"]), m) for m, r in rows.items() if r["change"] is not None and m in ("spend", "gmv", "roas", "cpm", "ctr")]
    tail = ""
    if movers:
        tail = f" The biggest swing is in {max(movers)[1].upper() if max(movers)[1] in ('roas', 'cpm', 'ctr', 'gmv') else max(movers)[1]}."
    return "; ".join(parts) + "." + tail


def describe_table(title: str, df: pd.DataFrame, full: bool = True) -> str:
    if df is None or df.empty:
        return ""
    cols = set(df.columns)
    if {"metric", "current", "previous", "change"} <= cols:
        return _compare_text(df)
    tcol = next((c for c in TIME_COLS if c in cols), None)
    if tcol and len(df) >= 3:
        return _series_text(df, tcol)
    lab = _label_cols(df)
    if not lab:
        return ""
    lead = next((c for c in LEAD_COLS if c in cols), None) or next((c for c in df.columns if c not in lab and _is_num(df[c])), None)
    mcols = _pick_metrics(df, lead)
    n = len(df)
    out = []
    first = [_item(r, lab, mcols, lead) for _, r in df.head(3).iterrows()]
    if n == 1:
        out.append(f"{first[0]}.")
    elif n == 2:
        out.append(f"{first[0]} comes first, then {first[1]}.")
    else:
        out.append(f"At the top is {first[0]}, followed by {first[1]} and {first[2]}.")
    if not full or n < 2:
        return " ".join(out)
    if {"spend", "gmv"} <= cols:
        sp, gm = _num(df, "spend").sum(), _num(df, "gmv").sum()
        out.append(f"The {n} rows shown add up to {fmt.inr(sp)} of spend and {fmt.inr(gm)} of GMV at MRP, a blended ROAS of {fmt.roas(metrics.roas(gm, sp))}.")
        if n >= 4 and sp > 0:
            top1 = _num(df, "spend").iloc[0] / sp
            top3 = _num(df, "spend").head(3).sum() / sp
            if top1 >= 0.25:
                out.append(f"{_label(df.iloc[0], lab)} alone is {fmt.pct(top1)} of the spend shown.")
            elif top3 >= 0.5:
                out.append(f"The top three take {fmt.pct(top3)} of the spend shown.")
    if {"spend_share", "gmv_share"} <= cols and n >= 2:
        sp, gm = _num(df, "spend_share"), _num(df, "gmv_share")
        r0 = df.iloc[0]
        out.append(f"{_label(r0, lab)} takes {fmt.pct(sp.iloc[0])} of spend and earns {fmt.pct(gm.iloc[0])} of GMV.")
        idx = (gm / sp).replace([float("inf")], float("nan"))
        if idx.notna().sum() >= 3:
            over, under = idx.idxmin(), idx.idxmax()
            if idx[over] < 0.8:
                out.append(f"{_label(df.loc[over], lab)} looks overfunded ({fmt.pct(sp[over])} of spend for {fmt.pct(gm[over])} of GMV).")
            if idx[under] > 1.25:
                out.append(f"{_label(df.loc[under], lab)} looks underfunded ({fmt.pct(sp[under])} of spend for {fmt.pct(gm[under])} of GMV).")
    if "roas" in cols and n >= 3:
        r = _num(df, "roas")
        if r.notna().sum() >= 3:
            hi, lo = r.idxmax(), r.idxmin()
            below = int((r < 1).sum())
            out.append(f"ROAS runs from {fmt.roas(r[lo])} ({_label(df.loc[lo], lab)}) to {fmt.roas(r[hi])} ({_label(df.loc[hi], lab)}); "
                       + (f"{below} of {n} are below 1.00, meaning they cost more than the GMV they brought at MRP." if below else "none is below 1.00."))
    return " ".join(out)


# ------------------------------------------------------------------ notes
_LABEL = re.compile(r"^(action|insight|fact|assumption|hypothesis|warning)\s*:\s*", re.I)


def narrate_notes(notes) -> list:
    todo, context, missing, gaps, warns, other = [], [], [], [], [], []
    for n in dict.fromkeys(notes):
        low = n.lower()
        if low.startswith("action:"):
            todo.append(_LABEL.sub("", n).rstrip("."))
        elif low.startswith("warning:"):
            warns.append(n)
        elif low.startswith("needs setting"):
            missing.append(n)
        elif "data not loaded" in low:
            gaps.append(n)
        elif _LABEL.match(n) or low.startswith("insight"):
            context.append(n)
        else:
            other.append(n)
    out = []
    for w in warns:
        out.append(f"**{w}**")
    if todo:
        out.append("**What I'd do:** " + "; ".join(todo[:3]) + ".")
    if context or other:
        out.append("**Worth knowing:**\n" + "\n".join(f"- {c}" for c in context + other))
    if missing or gaps:
        out.append("**Still needed to sharpen this:**\n" + "\n".join(f"- {c}" for c in missing + gaps))
    return out


def narrate(res: Result) -> str:
    """Prose version of a result: lead sentence, one short paragraph per table,
    then grouped notes. Tables are rendered separately as supporting numbers."""
    paras = []
    if res.headline:
        paras.append(f"**{res.headline}**")
    for i, (title, df) in enumerate(res.tables):
        text = describe_table(title, df, full=(i == 0))
        if text:
            paras.append(text if i == 0 else f"_{title}:_ {text}")
    for title, text in res.sections:
        paras.append(f"**{title}**\n\n{text}")
    paras += narrate_notes(res.notes)
    return "\n\n".join(paras)
