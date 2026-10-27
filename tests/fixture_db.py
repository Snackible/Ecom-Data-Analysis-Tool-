"""Builds a synthetic DuckDB (matching ingest.py's schemas) in a temp dir.

Only synthetic rows are used; tests never touch real data or the repo's
db/ads.duckdb. Months are parameters so no test hardcodes a real month.
"""
import calendar
from datetime import date

import duckdb
import pandas as pd

import ingest

CITIES = ["Bangalore", "Mumbai", "Delhi", "Pune"]
# (campaign, ad_property, keyword, match_type, product)
SETUPS = [
    ("KBA_Millet", "Keyword Based Ads", "snackible", "KEYWORD_MATCH_TYPE_BROAD", "Snackible Ragi Chips 60g"),
    ("KBA_Millet", "Keyword Based Ads", "ragi chips", "KEYWORD_MATCH_TYPE_EXACT", "Snackible Ragi Chips 60g"),
    ("KBA_Attack", "Keyword Based Ads", "troovy", "KEYWORD_MATCH_TYPE_EXACT", "Snackible Cheese Dosa Khakhra 100g"),
    ("KBA_Attack", "Keyword Based Ads", "choki choki", "KEYWORD_MATCH_TYPE_BROAD", "Snackible Cheese Dosa Khakhra 100g"),
    ("Collection_IM", "Collection Ads - Cart Page", None, "KEYWORD_MATCH_TYPE_INVALID", None),
    ("SIB_Brand", "Search Inline Banner", "healthy snacks", "KEYWORD_MATCH_TYPE_BROAD", None),
]
# served search queries per keyword
QUERIES = {
    "snackible": ["snackible", "chips", "snacks", "mad angles"],
    "ragi chips": ["ragi chips", "ragi chip"],
    "troovy": ["troovy", "protien bars"],
    "choki choki": ["choki choki", "wafers", "dark fantasy"],
    "healthy snacks": ["healthy snacks", "khakra", "khakhra"],
}
SEARCH_PLACEMENTS = {"Keyword Based Ads", "Search Inline Banner", "Guaranteed Top Slot Ads"}


def _days(month: str):
    y, m = map(int, month.split("-"))
    return [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]


def _row_metrics(seed: int, boost: float = 1.0):
    imps = 1000 + (seed * 37) % 900
    clicks = 20 + (seed * 11) % 30
    spend = round(imps * 0.1 * boost, 2)
    gmv = round(spend * (1.0 + (seed % 7) * 0.5), 2)
    return dict(
        total_impressions=imps, total_budget_burnt=spend, total_clicks=clicks,
        total_a2c=clicks // 2, total_gmv=gmv, total_conversions=max(1, clicks // 4),
    )


def build_fixture(path, months=("2026-07",), noida=False, empty=False, collection_conv_over_clicks=True):
    con = duckdb.connect(str(path))
    ingest.ensure_schema(con)
    if empty:
        con.close()
        return path
    g_rows, s_rows = [], []
    seed = 0
    for month in months:
        for d in _days(month):
            for (camp, prop, kw, mt, prod) in SETUPS:
                for city in CITIES + (["Noida", "Noida 1"] if noida else []):
                    seed += 1
                    m = _row_metrics(seed)
                    if prop.startswith("Collection") and collection_conv_over_clicks:
                        m["total_clicks"] = 5
                        m["total_conversions"] = 9  # conversion > 100%: not an error
                    g_rows.append(dict(
                        metrics_date=d, campaign_id=camp.lower(), campaign_name=camp,
                        campaign_status="ACTIVE", bidding_type="BIDDING_STRATEGY_TYPE_CPM",
                        budget_type="DAILY", ad_property=prop, keyword=kw, match_type=mt,
                        product_name=prod, city=city, total_budget=1000.0,
                        total_direct_gmv_7d=m["total_gmv"] * 0.8,
                        total_direct_gmv_14d=m["total_gmv"] * (0.8 if d.day > 24 else 0.95),
                        ad_rank=2.0 + (seed % 3), **m,
                    ))
    # search_query rows are splits of the granular totals per (date, campaign,
    # placement, keyword, match type, product), so both tables agree.
    gdf = pd.DataFrame(g_rows)
    sdf = gdf[gdf["ad_property"].isin(SEARCH_PLACEMENTS) & gdf["keyword"].notna()]
    keys = ["metrics_date", "campaign_id", "campaign_name", "campaign_status", "bidding_type", "budget_type",
            "ad_property", "keyword", "match_type", "product_name"]
    sums = ["total_impressions", "total_budget_burnt", "total_clicks", "total_a2c", "total_gmv", "total_conversions",
            "total_direct_gmv_7d", "total_direct_gmv_14d"]
    agg = sdf.groupby(keys, dropna=False)[sums].sum().reset_index()
    for r in agg.to_dict("records"):
        qs = QUERIES[r["keyword"]]
        n = len(qs)
        w_spend = [i + 1 for i in range(n)]
        w_gmv = [n - i for i in range(n)]
        for i, q in enumerate(qs):
            row = dict(r)
            row["search_query"] = q
            row["city_count"] = 4
            row["ad_rank"] = 2.0
            for c in ("total_impressions", "total_budget_burnt", "total_clicks", "total_a2c", "total_conversions"):
                row[c] = r[c] * w_spend[i] / sum(w_spend)
            for c in ("total_gmv", "total_direct_gmv_7d", "total_direct_gmv_14d"):
                row[c] = r[c] * w_gmv[i] / sum(w_gmv)
            s_rows.append(row)
    for table, rows in (("granular", g_rows), ("search_query", s_rows)):
        if not rows:
            continue
        df = pd.DataFrame(rows)
        cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
        for c in cols:
            if c not in df:
                df[c] = None
        con.register("_df", df[cols])
        con.execute(f"INSERT INTO {table} SELECT * FROM _df")
        con.unregister("_df")
    first, last = _days(months[0])[0], _days(months[-1])[-1]
    con.execute("INSERT INTO ingested_files VALUES ('g.csv.gz','granular',?,?,?,now())", [first, last, len(g_rows)])
    con.execute("INSERT INTO ingested_files VALUES ('s.csv.gz','search_query',?,?,?,now())", [first, last, len(s_rows)])
    con.close()
    return path
