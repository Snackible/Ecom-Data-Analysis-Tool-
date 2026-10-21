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


# ------------------------------------------------------------ manager's read
def _names(df, lab, idx, k=3):
    return ", ".join(f"**{_label(df.loc[i], lab)}**" for i in list(idx)[:k])


def _rows_read(df) -> list:
    """What a category manager would say about a ranked table: where money is
    leaking, where it earns, and what to check before moving budget."""
    cols = set(df.columns)
    lab = _label_cols(df)
    if not lab or not {"spend", "roas"} <= cols or len(df) < 3:
        return []
    sp, r = _num(df, "spend"), _num(df, "roas")
    ok = sp.notna() & r.notna()
    tot = sp[ok].sum()
    if tot <= 0:
        return []
    out = []
    low = ok & (r < 1)
    if low.any() and not low.all():
        ls = sp[low].sum()
        line = f"{int(low.sum())} of {int(ok.sum())} rows ({fmt.pct(ls / tot)} of the spend shown, {fmt.inr(ls)}) return less than they cost at MRP"
        if "gmv" in cols:
            g = _num(df, "gmv")[low].sum()
            line += f", bringing back only {fmt.inr(g)} of GMV"
        out.append(f"Fact: {line}.")
        worst = sp[low].sort_values(ascending=False).index
        out.append(f"Action: start the clean-up with {_names(df, lab, worst)}, where the leak is biggest.")
    elif low.all():
        out.append("Fact: every row shown is below 1.00, so this is a systematic leak and not a few bad rows. Tighten the match type or cut the bids on the keywords serving these first.")
    blended = metrics.roas(_num(df, "gmv")[ok].sum(), tot) if "gmv" in cols else None
    if blended:
        strong = ok & (r >= max(1.0, blended * 1.25)) & (sp <= sp[ok].median())
        if strong.any():
            idx = r[strong].sort_values(ascending=False).index
            out.append(f"Hypothesis: {_names(df, lab, idx)} {'earns' if len(idx) == 1 else 'earn'} well above the {fmt.roas(blended)} blended ROAS on below-median spend, "
                       "so they are the first candidates for more budget. Confirm stock and fill rate before scaling, because ROAS here ignores availability.")
    z = ok & (_num(df, "gmv").fillna(0) == 0) if "gmv" in cols else None
    if z is not None and z.any():
        out.append(f"Fact: {fmt.inr(sp[z].sum())} went to {int(z.sum())} rows with no GMV at all.")
    return out


def _compare_read(df) -> list:
    rows = {r["metric"]: r for _, r in df.iterrows()}

    def ch(m):
        r = rows.get(m)
        return None if r is None else r["change"]
    roas_ch = ch("roas")
    if roas_ch is None or abs(roas_ch) < 0.03:
        return []
    drivers = []
    for m, bad_when, name in (("cpm", 1, "CPM"), ("ctr", -1, "CTR"), ("conv_rate", -1, "conversion rate"), ("conversions", -1, "orders")):
        c = ch(m)
        if c is not None and abs(c) >= 0.03:
            drivers.append((abs(c), m, name, c, bad_when))
    if not drivers:
        return []
    drivers.sort(reverse=True)
    falling = roas_ch < 0
    top = drivers[0]
    d = "up" if top[3] > 0 else "down"
    line = (f"Hypothesis: the biggest mover behind the ROAS {'drop' if falling else 'rise'} is {top[2]}, {d} {abs(top[3]) * 100:.1f}%"
            + (" (a cost-side effect, so check bids and placement mix)" if top[1] == "cpm"
               else " (a relevance effect, so check keyword and creative fit)" if top[1] == "ctr"
               else " (a landing and availability effect, so check stock and price on the listings)" if top[1] == "conv_rate"
               else "") + ".")
    out = [line]
    if falling:
        out.append("Fact: the most recent days can look worse partly because attribution is still coming in, so compare like-for-like lag before reacting.")
    return out


def _series_read(df, tcol) -> list:
    if not {"spend", "gmv"} <= set(df.columns) or len(df) < 6:
        return []
    d = df.sort_values(tcol)
    h = len(d) // 2
    a, b = d.iloc[:h], d.iloc[h:]
    ra, rb = metrics.roas(_num(a, "gmv").sum(), _num(a, "spend").sum()), metrics.roas(_num(b, "gmv").sum(), _num(b, "spend").sum())
    if ra is None or rb is None:
        return []
    sa, sb = _num(a, "spend").sum(), _num(b, "spend").sum()
    g = metrics.growth(rb, ra)
    word = "improved" if rb > ra else "slipped" if rb < ra else "held"
    line = (f"Fact: the second half of the period spent {fmt.inr(sb)} against {fmt.inr(sa)} in the first half, and ROAS {word} "
            f"from {fmt.roas(ra)} to {fmt.roas(rb)}" + (f" ({fmt.signed_pct(g)})" if g is not None else "") + ".")
    out = [line]
    if sa > 0 and sb / sa > 1.1 and rb < ra:
        out.append("Hypothesis: spend rose while ROAS fell, which points to diminishing returns on the extra budget rather than a demand problem.")
    elif sa > 0 and sb / sa < 0.9 and rb > ra:
        out.append("Hypothesis: the cut in spend lifted efficiency, so the trimmed budget was probably the weakest money.")
    return out


def manager_read(res: Result) -> list:
    """Extra insight lines (labelled Fact / Hypothesis / Action) computed from the tables."""
    out = []
    if not res.tables:
        return out
    title, df = res.tables[0]
    if df is None or df.empty:
        return out
    cols = set(df.columns)
    if {"metric", "current", "previous", "change"} <= cols:
        out += _compare_read(df)
    else:
        tcol = next((c for c in TIME_COLS if c in cols), None)
        out += _series_read(df, tcol) if tcol else _rows_read(df)
    if "roas" in cols and not any("MRP" in n for n in res.notes):
        out.append("Fact: ROAS is GMV at MRP over spend, so it overstates real revenue; judge scale-up decisions against break-even, not against 1.00.")
    return out


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
    paras += narrate_notes(list(res.notes) + manager_read(res))
    return "\n\n".join(paras)
