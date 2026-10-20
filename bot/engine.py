"""Ties it together: question text -> Result. Fully local, no network or LLM."""
import re

from . import flex, matcher as matcher_mod, q_cost, queries
from .core import Context, Params, Result, empty_result
from .params import extract_entities, extract_top_n, parse_dates
from .normalise import norm_text

BRAINSTORM = re.compile(r"\b(let'?s brainstorm|brainstorm|think with me|think this through)\b", re.I)
DOMAIN_WORDS = re.compile(
    r"\b(spend|spent|budget|impressions?|clicks?|ctr|cpc|cpm|roas|gmv|revenue|sales|orders?|conversions?|a2c|a2s|cart|"
    r"campaigns?|keywords?|quer(y|ies)|searches|city|cities|sku|skus|product|products|placements?|match|broad|exact|"
    r"rows?|trend|compare|versus|weekly|daily|monthly|loaded|share|top|bottom|lowest|highest|ads?|instamart|"
    r"attack|defend|expand|negative|bid|bids|rank)\b", re.I)
CROSSCHECK = re.compile(r"\b(tables?|reports?|totals?|files?)\b.*\b(agree|match|reconcile|tally)\b|\bcross.?check\b|\breconcile\b", re.I)
AMOUNT = re.compile(r"(?:₹|rs\.?\s*|inr\s*)?(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|l|lakh|lakhs|lac|cr|crore)?\b", re.I)
CHECK_SKIP = {90, 91, 92, 93, 94, 97}


def extract_amount(text: str):
    """Rupee amount from phrases like '₹50,000', '50k', '1.5 lakh'."""
    best = None
    for m in AMOUNT.finditer(text.replace("₹", "₹")):
        raw = m.group(1).replace(",", "")
        unit = (m.group(2) or "").lower()
        try:
            v = float(raw)
        except ValueError:
            continue
        mult = {"k": 1e3, "thousand": 1e3, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "cr": 1e7, "crore": 1e7}.get(unit, 1)
        v *= mult
        # ignore small numbers that are counts ("top 10", "7 days") unless a unit or ₹ is given
        if mult == 1 and v < 1000 and "₹" not in text and not re.search(r"\b(rs|inr|rupees?)\b", text, re.I):
            continue
        if best is None or v > best:
            best = v
    return best


def build_params(text: str, ctx: Context) -> Params:
    lo, hi = ctx.bounds("granular")
    if lo is None:
        lo, hi = ctx.bounds("search_query")
    d = parse_dates(text, lo, hi)
    ents = extract_entities(text, ctx.catalog())
    amt = extract_amount(text)
    if amt:
        ents["amount"] = [amt]
    n = extract_top_n(text, default=-1)
    p = Params(text=text, entities=ents, top_n=n if n > 0 else 10, explicit_n=n > 0, compare=d["compare"], note=d["note"])
    ps = d["periods"]
    if ps:
        p.period = ps[0]
        if len(ps) >= 2:
            p.prev = ps[1]
    return p


def _in_domain(text: str, p: Params) -> bool:
    return bool(DOMAIN_WORDS.search(text)) or any(p.entities.get(k) for k in ("cities", "campaigns", "keywords", "products", "queries", "placements"))


def _unknown(m, text, mt) -> Result:
    r = Result(headline="I could not match that to something I can answer from the ads data.", kind="unknown")
    sug = mt.suggestions(text)
    if sug:
        r.notes.append("Closest questions I can answer: " + " | ".join(f"{t}" for _, t in sug))
    r.notes.append("I answer from the loaded Instamart ads tables only (campaigns, placements, keywords, search queries, cities, SKUs, dates).")
    return r


def ask(text: str, settings: dict | None = None, log: bool = True, log_path=None) -> Result:
    """Answer one question. Opens (and closes) its own DuckDB connection via config.connect_db()."""
    text = (text or "").strip()
    if not text:
        return Result(headline="Ask me something about the Instamart ads data.", kind="unknown")
    ctx = Context.open(settings)
    try:
        if not (ctx.has_rows("granular") or ctx.has_rows("search_query")):
            return empty_result()
        mt = matcher_mod.get_matcher()
        p = build_params(text, ctx)
        m = mt.match(text)
        qn, handled = None, ""
        try:
            if BRAINSTORM.search(text):
                qn, handled = 111, "bank"
                res = queries.run_question(ctx, p, 111)
            elif CROSSCHECK.search(text):
                handled = "crosscheck"
                res = q_cost.crosscheck_intent(ctx, p)
            elif m.route == "bank" and m.qn is not None:
                qn, handled = m.qn, "bank"
                res = queries.run_question(ctx, p, m.qn)
            elif m.route == "flex" or _in_domain(text, p):
                handled = "flex"
                res = flex.run(ctx, p, text)
                if log and m.route != "flex":
                    matcher_mod.log_miss(text, m, handled, log_path)
            else:
                res = _unknown(m, text, mt)
                if log:
                    matcher_mod.log_miss(text, m, "unanswered", log_path)
        except Exception as e:  # never show a stack trace in chat
            res = Result(headline="I hit a problem answering that.", kind="unknown")
            res.notes.append(f"{type(e).__name__}: {str(e)[:160]}")
            if log:
                matcher_mod.log_miss(text, m, f"error:{type(e).__name__}", log_path)
            return res
        # period outside the loaded data
        if p.period is not None and res.kind == "empty":
            lo, hi = ctx.bounds("granular")
            res.headline = f"No data loaded for {p.period.start} to {p.period.end}. Loaded data covers {lo} to {hi}."
        if p.period is not None and p.period.note:
            res.notes.append(f"Fact: {p.period.note}.")
        if res.kind == "ok" and qn not in CHECK_SKIP:
            warn = q_cost.table_crosscheck(ctx)
            if warn:
                res.notes.insert(0, "Warning: the tables do not agree for search placements: " + "; ".join(warn))
        return res
    finally:
        ctx.close()
