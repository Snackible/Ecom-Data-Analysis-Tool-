"""Flexible query engine: free-form lookups, top N, trends, comparisons.

The question is turned into a small structured spec (table, metric,
dimension, filters, period, mode). Column and dimension names come only from
the whitelists in core.py and the vocab below; user text never reaches SQL.
"""
import re
from dataclasses import dataclass, field

import pandas as pd

from . import fmt, metrics
from .core import DIMS, Context, Params, Result, empty_result, filters_from, num, range_text
from .helpers import add_share, sum_by, top
from .normalise import norm_text
from .params import Period, previous_period

METRIC_WORDS = {
    "spend": ["spend", "spent", "budget", "cost", "burnt", "burn"], "impressions": ["impressions", "impression", "views", "reach"],
    "clicks": ["clicks", "click"], "gmv": ["gmv", "revenue", "sales"], "conversions": ["conversions", "orders", "conversion"],
    "a2c": ["a2c", "add to cart", "carts"], "roas": ["roas", "return on ad spend"], "ctr": ["ctr", "click through", "click-through"],
    "cpc": ["cpc", "cost per click"], "cpm": ["cpm", "cost per thousand", "cost per 1000"],
    "conv_rate": ["conversion rate", "conv rate"], "a2c_rate": ["a2c rate", "add to cart rate"],
}
DIM_WORDS = {
    "query": ["search query", "search queries", "queries", "query", "searches", "search term", "search terms"],
    "city": ["city", "cities"], "campaign": ["campaign", "campaigns"], "keyword": ["keyword", "keywords"],
    "product": ["sku", "product", "products", "skus"], "placement": ["placement", "placements", "ad property", "ad type"],
    "match_type": ["match type", "match types", "broad", "exact"], "date": ["day", "daily", "date", "days"],
    "week": ["week", "weekly", "weeks"], "month": ["month", "monthly", "months"], "bidding": ["bidding", "bid type", "bidding type"],
}
RATIO_METRICS = {"roas", "ctr", "cpc", "cpm", "conv_rate", "a2c_rate"}
BASE_METRICS = {"spend", "impressions", "clicks", "gmv", "conversions", "a2c"}
FMT = {"spend": fmt.inr, "gmv": fmt.inr, "cpc": lambda x: fmt.inr(x, 2), "cpm": lambda x: fmt.inr(x, 2), "roas": fmt.roas,
       "ctr": fmt.pct, "conv_rate": fmt.pct, "a2c_rate": fmt.pct}


@dataclass
class Spec:
    mode: str = "topn"          # topn | trend | compare | share | lookup | count | outlier | months
    metric: str = "spend"
    dim: str | None = None
    ascending: bool = False
    table: str = "granular"
    n: int = 10
    min_impr: int | None = None
    only_down: bool = False
    notes: list = field(default_factory=list)


def _first(text, vocab, longest=True):
    best, pos = None, 10 ** 9
    for key, words in vocab.items():
        for w in words:
            m = re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text)
            if m and (m.start() < pos or (m.start() == pos and best and len(w) > 0)):
                best, pos = key, m.start()
    return best


def _metric_of(t):
    # prefer multiword metric names first (conversion rate before conversions)
    for key in ("conv_rate", "a2c_rate", "cpc", "cpm", "ctr", "roas"):
        for w in METRIC_WORDS[key]:
            if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", t):
                return key
    return _first(t, METRIC_WORDS)


def parse_spec(text: str, p: Params) -> Spec:
    t = norm_text(text)
    s = Spec(n=p.top_n)
    s.metric = _metric_of(t) or "spend"
    s.dim = _first(t, DIM_WORDS)
    if re.search(r"\b(lowest|worst|least|bottom|smallest|fewest|poorest)\b", t):
        s.ascending = True
    if re.search(r"\b(trend|daily|weekly|monthly|over time|rising|falling|going up|going down|is .* (rising|falling|up|down))\b", t) or \
            (s.dim in ("date", "week", "month") and not p.compare):
        s.mode = "trend"
        if s.dim not in ("date", "week", "month"):
            s.dim = "week" if re.search(r"weekly|week", t) else "date"
    if p.compare and len(p.entities.get("_periods", [])) >= 1 or p.compare:
        s.mode = "compare"
    if re.search(r"\bshare of\b|\bwhat (percent|percentage|%)\b|\bpercent of\b", t):
        s.mode = "share"
    if re.search(r"\bhow many\b|\bnumber of\b|\bcount\b", t):
        s.mode = "count"
    if re.search(r"\bwhich months?\b|\bwhat months?\b|\bwhat dates?\b|\bdate range\b|\bdata loaded\b|\bwhat data\b", t):
        s.mode = "months"
    if re.search(r"\bunusual\b|\banomal|\boutlier|\bspike|\bjump", t):
        s.mode = "outlier"
    if re.search(r"\bshow (me )?(the )?rows\b|\brows (for|of|on)\b|\bdetails? of\b|\blist (all )?rows\b", t):
        s.mode = "lookup"
    if re.search(r"\bis (a |an )?['\"]?\w[\w ]*['\"]? (a )?(search )?quer(y|ies)\b|\bdid we (serve|show|appear)\b", t):
        s.mode = "exists"
    if s.dim == "query" or (p.ent("queries") and s.mode != "lookup"):
        s.table = "search_query"
    if s.mode == "lookup":
        s.table = "search_query" if p.ent("queries") else "granular"
    m = re.search(r"(\d[\d,]*)\s*(k|thousand)?\s*\+?\s*impressions", t)
    if m:
        s.min_impr = int(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
    if re.search(r"fell but|drop(ped)? but|down but", t):
        s.only_down = True
    return s


def _ratio_df(df: pd.DataFrame) -> pd.DataFrame:
    return metrics.add_ratios(df)


def run(ctx: Context, p: Params, text: str) -> Result:
    if not ctx.has_rows("granular") and not ctx.has_rows("search_query"):
        return empty_result()
    s = parse_spec(text, p)
    t = norm_text(text)
    table = s.table if ctx.has_rows(s.table) else ("granular" if ctx.has_rows("granular") else "search_query")
    period = ctx.effective_period(p, table)
    flt = filters_from(p, table)
    if table == "search_query" and p.ent("cities"):
        s.notes.append("Search query x city is not available in any report, so the city filter was ignored. Closest: keyword x city (granular) and search query x keyword (search_query).")
    notes = list(s.notes)
    res = Result()
    dmap = DIMS[table]
    dim = s.dim if s.dim in dmap else None

    if s.mode == "months":
        files = ctx.sql("SELECT file_type, period_start, period_end, row_count FROM ingested_files ORDER BY period_start") if "ingested_files" in ctx.tables() else pd.DataFrame()
        lo, hi = ctx.bounds("granular")
        res.headline = f"Loaded data: granular {lo} to {hi}." if lo else "No data loaded."
        for tb in ("search_query", "summary"):
            a, b = ctx.bounds(tb) if tb != "summary" else (None, None)
            if a:
                res.notes.append(f"search_query covers {a} to {b}.")
        if ctx.has_rows("summary"):
            sm = ctx.sql("SELECT MIN(period_start) a, MAX(period_end) b FROM summary").iloc[0]
            res.notes.append(f"summary covers {sm['a'].date()} to {sm['b'].date()}.")
        if not files.empty:
            res.tables.append(("Ingested files", files))
        res.date_range = range_text((lo, hi))
        return res

    if s.mode == "exists":
        qs = p.ent("quoted") or []
        q = (qs[0] if qs else "").lower()
        if not q:
            return Result(headline="Which search query? Put it in quotes and I will check.", kind="unknown")
        hit = q in ctx.catalog().queries
        res.headline = f"Yes, '{q}' was served." if hit else f"No, '{q}' is not in the search query data."
        if hit:
            d, rng = ctx.fetch("search_query", ("query",), period, {"query": [q]})
            res.tables.append(("Performance", d[["query", "spend", "impressions", "clicks", "ctr", "gmv", "roas"]]))
            res.date_range = range_text(rng)
        else:
            from rapidfuzz import process
            near = process.extract(q, list(ctx.catalog().queries), limit=3, score_cutoff=75)
            if near:
                res.notes.append("Did you mean: " + ", ".join(f"'{x[0]}'" for x in near))
        return res

    if s.mode == "lookup":
        wh, args = ctx.where(table, period, flt)
        cols = ["metrics_date", "campaign_name", "ad_property", "keyword", "match_type", "product_name"] + (["city"] if table == "granular" else ["search_query"]) + \
               ["total_impressions", "total_clicks", "total_budget_burnt", "total_gmv"]
        df = ctx.sql(f"SELECT {', '.join(cols)} FROM {table}{wh} ORDER BY metrics_date, total_budget_burnt DESC LIMIT 50", args)
        cnt = ctx.con.execute(f"SELECT count(*) FROM {table}{wh}", args).fetchone()[0]
        rng = ctx.con.execute(f"SELECT min(metrics_date), max(metrics_date) FROM {table}{wh}", args).fetchone()
        if df.empty:
            return Result(headline="No rows match those filters.", date_range=range_text(rng))
        res.headline = f"{cnt:,} rows match" + (f"; showing the first {len(df)}." if cnt > len(df) else ".")
        res.tables.append(("Rows", df))
        res.date_range = range_text(rng)
        return res

    metric = s.metric
    if s.mode == "count":
        target = dim or "city"
        if target in ("date", "week", "month"):
            target = "date"
        expr = dmap[target]
        wh, args = ctx.where(table, period, flt)
        n = ctx.con.execute(f"SELECT count(DISTINCT {expr}) FROM {table}{wh}", args).fetchone()[0]
        rng = ctx.con.execute(f"SELECT min(metrics_date), max(metrics_date) FROM {table}{wh}", args).fetchone()
        res.headline = f"{n:,} distinct {target}{'s' if not target.endswith('s') else ''} in this range" + (f" for {', '.join(sum([v for v in flt.values()], []))}." if flt else ".")
        res.date_range = range_text(rng)
        return res

    if s.mode == "share":
        d = dim or "placement"
        df, rng = ctx.fetch(table, (d,), period, None)
        if df.empty:
            return empty_result()
        base = metric if metric in BASE_METRICS else "gmv"
        df = add_share(df, base, "share")
        hit = df
        ents = [x for k in ("placements", "campaigns", "cities", "keywords", "products") for x in p.ent(k)]
        if ents:
            hit = df[df[d].astype(str).str.lower().isin([e.lower() for e in ents])]
        res.headline = ("; ".join(f"{r[d]} is {fmt.pct(r['share'])} of {base} ({FMT.get(base, fmt.num)(r[base])})" for _, r in hit.iterrows())
                        if not hit.empty else f"Share of {base} by {d} is below.")
        res.tables.append((f"Share of {base} by {d}", top(df, s.n, "share")[[d, base, "share", "roas"]]))
        res.date_range = range_text(rng)
        return res

    if s.mode == "trend":
        d = dim if dim in ("date", "week", "month") else "date"
        df, rng = ctx.fetch(table, (d,), period, flt, order=d)
        if df.empty:
            return empty_result()
        col = metric
        df["_v"] = pd.to_numeric(df[col], errors="coerce")
        shown = df if len(df) <= 31 else df.tail(31)
        first, last = df["_v"].dropna().iloc[0] if df["_v"].notna().any() else None, df["_v"].dropna().iloc[-1] if df["_v"].notna().any() else None
        ch = metrics.growth(last, first)
        slope = None
        if df["_v"].notna().sum() >= 3:
            y = df["_v"].dropna().values
            x = range(len(y))
            n_ = len(y)
            mx, my = sum(x) / n_, sum(y) / n_
            den = sum((a - mx) ** 2 for a in x)
            slope = sum((a - mx) * (b - my) for a, b in zip(x, y)) / den if den else None
        direction = "flat" if slope is None or abs(slope) < 1e-12 or (my and abs(slope * n_ / my) < 0.02) else ("rising" if slope > 0 else "falling")
        res.headline = f"{col.upper() if col in RATIO_METRICS else col.title()} by {d} is {direction}: {FMT.get(col, fmt.num)(first)} at the start to {FMT.get(col, fmt.num)(last)} at the end ({fmt.signed_pct(ch)})."
        res.tables.append((f"{col} by {d}" + (" (latest 31 shown)" if len(df) > 31 else ""), shown[[d, "spend", "impressions", "clicks", "gmv", "roas", "cpm"]]))
        notes.append("Fact: weeks start on Monday. Direction uses the slope of the plotted values (flat if under 2% movement across the range).")
        from .helpers import lag_note
        notes += lag_note(ctx, rng, table)[:1]
        res.date_range = range_text(rng)
        res.notes += notes
        return res

    if s.mode == "compare":
        cur = p.period
        if cur is None:
            lo, hi = ctx.bounds(table)
            from datetime import timedelta
            cur = Period(max(lo, hi - timedelta(days=6)), hi, "latest 7 days")
        prev = p.prev if p.prev is not None else previous_period(cur)
        d = dim or "placement"
        a, rng = ctx.fetch(table, (d,), cur, flt)
        b, _ = ctx.fetch(table, (d,), prev, flt)
        if a.empty or b.empty:
            return Result(headline=f"No data for one of the periods ({cur.start} to {cur.end} vs {prev.start} to {prev.end}).", date_range=range_text(rng))
        m = a.merge(b, on=d, how="outer", suffixes=("", "_prev"))
        col = metric
        m[f"{col}_change"] = [metrics.growth(x, y) for x, y in zip(num(m, col), num(m, f"{col}_prev"))]
        if s.only_down or re.search(r"fell but|dropped but", t):
            m["roas_change"] = [metrics.growth(x, y) for x, y in zip(num(m, "roas"), num(m, "roas_prev"))]
            m["spend_change"] = [metrics.growth(x, y) for x, y in zip(num(m, "spend"), num(m, "spend_prev"))]
            m = m[(num(m, "roas_change") < 0) & (num(m, "spend_change") > 0)]
            col = "roas"
        m = top(m, s.n, f"{col}_change", asc=s.ascending) if not m.empty else m
        tot_a, tot_b = a[col].sum() if col in BASE_METRICS else metrics.roas(a["gmv"].sum(), a["spend"].sum()), \
            b[col].sum() if col in BASE_METRICS else metrics.roas(b["gmv"].sum(), b["spend"].sum())
        res.headline = (f"{col} overall: {FMT.get(col, fmt.num)(tot_a)} vs {FMT.get(col, fmt.num)(tot_b)} ({fmt.signed_pct(metrics.growth(tot_a, tot_b))}), "
                        f"{cur.start} to {cur.end} vs {prev.start} to {prev.end}.")
        cols = [d, col, f"{col}_prev", f"{col}_change"] if col in m else [d]
        res.tables.append((f"{col} by {d}, current vs previous", m[cols] if not m.empty else m))
        notes.append("Fact: changes are recomputed from totals; a zero base gives n/a.")
        if col == "cpm":
            notes.append("Fact: compare CPM within one placement; across placements it is not like for like.")
        res.date_range = range_text(rng)
        res.notes += notes
        return res

    if s.mode == "outlier":
        df, rng = ctx.fetch(table, ("date",), period, flt, order="date")
        if len(df) < 5:
            return Result(headline="Too few days to call anything unusual.", date_range=range_text(rng))
        v = pd.to_numeric(df[metric if metric in df else "spend"], errors="coerce")
        med = v.median()
        mad = (v - med).abs().median()
        scale = mad * 1.4826 if mad else v.std()
        df = df.assign(score=((v - med) / scale) if scale else 0.0, value=v)
        odd = df[df["score"].abs() >= 3].reindex(df["score"].abs().sort_values(ascending=False).index).head(s.n)
        name = metric if metric in df else "spend"
        res.headline = (f"{len(odd)} days look unusual on {name} (3 or more robust deviations from the median {FMT.get(name, fmt.num)(med)})."
                        if not odd.empty else f"No day looks unusual on {name}.")
        if not odd.empty:
            res.tables.append(("Unusual days", odd[["date", "value", "score"]]))
        res.notes.append("Fact: 'unusual' is measured against this data's own median and spread, not an external benchmark.")
        res.date_range = range_text(rng)
        return res

    # topn (default)
    d = dim or ("city" if table == "granular" else "query")
    df, rng = ctx.fetch(table, (d,), period, flt)
    if df.empty:
        return empty_result()
    if s.min_impr:
        df = df[num(df, "impressions") >= s.min_impr]
    col = metric if metric in df.columns else "spend"
    d2 = top(df, s.n, col, asc=s.ascending)
    if d2.empty:
        return Result(headline="Nothing matches that filter.", date_range=range_text(rng))
    r0 = d2.iloc[0]
    res.headline = f"{'Lowest' if s.ascending else 'Highest'} {col} by {d}: {r0[d]} at {FMT.get(col, fmt.num)(r0[col])}."
    show = [d] + [c for c in dict.fromkeys([col, "spend", "impressions", "clicks", "ctr", "cpm", "gmv", "roas"]) if c in d2.columns]
    plural = {"city": "cities", "query": "queries", "campaign": "campaigns", "keyword": "keywords", "product": "SKUs",
              "placement": "placements", "match_type": "match types", "bidding": "bidding types"}.get(d, d + "s")
    res.tables.append((f"Top {len(d2)} {plural} by {col}" if not s.ascending else f"Bottom {len(d2)} {plural} by {col}", d2[show]))
    if col in ("cpm",) and d == "placement":
        notes.append("Fact: compare CPM within the same placement.")
    if s.min_impr:
        notes.append(f"Filtered to at least {s.min_impr:,} impressions.")
    if table == "granular" and d == "city":
        notes.append("Fact: Noida and Noida 1 are merged.")
    res.notes += notes
    res.date_range = range_text(rng)
    return res
