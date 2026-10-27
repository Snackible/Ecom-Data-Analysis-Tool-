"""Shared plumbing for the Ads Bot: context, result type and the SQL builder.

SQL safety: column and dimension names come only from the whitelists below.
User text never reaches SQL; it can only choose a whitelist key, and any
value (city name, keyword, dates) is passed as a bound parameter.
"""
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

import config

from . import metrics, settings as settings_mod
from .classify import Classifier
from .normalise import clean_bidding, clean_match_type, norm_city, short_sku
from .params import Catalog, Period

NO_DATA = "No data loaded for this question yet."

CITY_EXPR = "CASE WHEN lower(trim(city)) IN ('noida','noida 1') THEN 'Noida' ELSE trim(city) END"

# dimension key -> SQL expression, per table
DIMS = {
    "granular": {
        "campaign": "campaign_name", "placement": "ad_property", "keyword": "keyword",
        "match_type": "match_type", "product": "product_name", "city": CITY_EXPR,
        "date": "metrics_date", "week": "date_trunc('week', metrics_date)::DATE",
        "month": "date_trunc('month', metrics_date)::DATE", "bidding": "bidding_type",
        "targeting": "targeting", "status": "campaign_status", "l1_category": "l1_category",
        "l2_category": "l2_category",
    },
    "search_query": {
        "campaign": "campaign_name", "placement": "ad_property", "keyword": "keyword",
        "match_type": "match_type", "product": "product_name", "query": "lower(trim(search_query))",
        "date": "metrics_date", "week": "date_trunc('week', metrics_date)::DATE",
        "month": "date_trunc('month', metrics_date)::DATE", "bidding": "bidding_type",
        "status": "campaign_status",
    },
}
# filter key -> SQL expression compared case-insensitively
FILTERS = {
    "granular": {"campaign": "campaign_name", "placement": "ad_property", "keyword": "keyword",
                 "match_type": "match_type", "product": "product_name", "city": CITY_EXPR},
    "search_query": {"campaign": "campaign_name", "placement": "ad_property", "keyword": "keyword",
                     "match_type": "match_type", "product": "product_name",
                     "query": "lower(trim(search_query))"},
}
SEARCH_PLACEMENTS = ("Keyword Based Ads", "Search Inline Banner", "Guaranteed Top Slot Ads")
# placements whose rows carry no product name (no SKU)
NO_SKU_PLACEMENTS = ("Collection Ads", "Auto Suggest", "Search Inline Banner")


@dataclass
class Params:
    text: str = ""
    period: Period | None = None
    prev: Period | None = None
    compare: bool = False
    entities: dict = field(default_factory=dict)
    top_n: int = 10
    explicit_n: bool = False
    note: str = ""  # date anchoring note

    def ent(self, key):
        return list(self.entities.get(key) or [])


@dataclass
class Result:
    headline: str = ""
    tables: list = field(default_factory=list)   # (title, DataFrame)
    notes: list = field(default_factory=list)    # short lines: insight, action, flags
    sections: list = field(default_factory=list)  # (title, text) blocks after the tables
    kind: str = "ok"                              # ok | empty | needs_setting | data_not_loaded | unknown
    date_range: str = ""
    meta: dict = field(default_factory=dict)       # memory of this answer, used for follow-ups
    suggestions: list = field(default_factory=list)  # follow-up questions offered as buttons


def empty_result() -> Result:
    return Result(headline=NO_DATA, kind="empty")


def data_not_loaded(item: str, offer: str = "") -> Result:
    r = Result(headline=f"data not loaded: {item}", kind="data_not_loaded")
    if offer:
        r.notes.append(offer)
    return r


def needs_setting(key: str, extra: str = "") -> Result:
    r = Result(headline=f"needs setting: {key}", kind="needs_setting")
    if extra:
        r.notes.append(extra)
    return r


class Context:
    def __init__(self, con, settings: dict | None = None):
        self.con = con
        self.settings = settings if settings is not None else settings_mod.load()
        self.clf = Classifier.from_settings(self.settings)
        self._tables = None
        self._catalog = None
        self._fetch_cache: dict = {}  # (table, dims, period, filters_key, extra, having, order, limit) -> (df.copy, rng)

    @classmethod
    def open(cls, settings: dict | None = None):
        return cls(config.connect_db(), settings)

    def close(self):
        try:
            self.con.close()
        except Exception:
            pass

    # -- basics --------------------------------------------------------------
    def set(self, key, required=False):
        return settings_mod.get(self.settings, key, required=required)

    @property
    def sku_map(self):
        return self.set("product_title_to_sku")

    def tables(self) -> set:
        if self._tables is None:
            rows = self.con.execute("SELECT table_name FROM information_schema.tables").fetchall()
            self._tables = {r[0] for r in rows}
        return self._tables

    def has_rows(self, table="granular") -> bool:
        if table not in self.tables():
            return False
        return self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0

    def bounds(self, table="granular"):
        if not self.has_rows(table):
            return None, None
        return self.con.execute(f"SELECT min(metrics_date), max(metrics_date) FROM {table}").fetchone()

    def sql(self, sql: str, params=None) -> pd.DataFrame:
        return self.con.execute(sql, params or []).fetchdf()

    def break_even(self):
        return metrics.break_even_roas(self.set("contribution_before_ads_pct_of_mrp_gmv"))

    def min_data_set(self) -> bool:
        return any(self.set(f"min_data.{k}") is not None for k in ("spend_inr", "clicks", "days"))

    def enough_data(self, spend=None, clicks=None, days=None) -> bool | None:
        """True/False per settings.min_data (any configured threshold met).
        None when min_data is not set at all."""
        if not self.min_data_set():
            return None
        for key, val in (("spend_inr", spend), ("clicks", clicks), ("days", days)):
            t = self.set(f"min_data.{key}")
            if t is not None and val is not None and val >= t:
                return True
        return False

    # -- catalog (discovered at runtime) --------------------------------------
    def catalog(self) -> Catalog:
        if self._catalog is not None:
            return self._catalog
        c = Catalog()
        if self.has_rows("granular"):
            col = lambda sql: [r[0] for r in self.con.execute(sql).fetchall() if r[0] is not None]
            c.campaigns = col("SELECT DISTINCT campaign_name FROM granular")
            c.cities = col(f"SELECT DISTINCT {CITY_EXPR} FROM granular")
            c.keywords = col("SELECT DISTINCT keyword FROM granular")
            c.products = col("SELECT DISTINCT product_name FROM granular")
            c.placements = col("SELECT DISTINCT ad_property FROM granular")
        if self.has_rows("search_query"):
            c.queries = {r[0] for r in self.con.execute(
                "SELECT DISTINCT lower(trim(search_query)) FROM search_query").fetchall() if r[0]}
        self._catalog = c
        return c

    # -- aggregation ----------------------------------------------------------
    def where(self, table, period: Period | None, filters: dict | None, extra: str = ""):
        clauses, args = [], []
        if period is not None:
            clauses.append("metrics_date BETWEEN ? AND ?")
            args += [period.start, period.end]
        for key, vals in (filters or {}).items():
            if not vals:
                continue
            expr = FILTERS[table].get(key)
            if expr is None:
                continue
            vals = [vals] if isinstance(vals, str) else list(vals)
            clauses.append("lower(" + expr + ") IN (" + ",".join("?" * len(vals)) + ")")
            args += [str(v).lower() for v in vals]
        if extra:
            clauses.append(extra)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def fetch(self, table, dims=(), period=None, filters=None, extra="", having="",
              order="spend DESC", limit=None):
        """Aggregate base metrics grouped by whitelisted dims. Returns
        (DataFrame with ratios, (min_date, max_date) actually covered).
        Memoised per Context: deep-dive + manager read + follow-ups hit the
        same slice several times inside one ask."""
        if not self.has_rows(table):
            return pd.DataFrame(), (None, None)
        dmap = DIMS[table]
        for d in dims:
            if d not in dmap:
                raise KeyError(f"dimension not allowed: {d}")
        per_key = (period.start, period.end, period.label) if period is not None else None
        flt_key = tuple(sorted((k, tuple(v) if isinstance(v, (list, tuple)) else (v,)) for k, v in (filters or {}).items() if v))
        cache_key = (table, tuple(dims), per_key, flt_key, extra, having, order, limit)
        hit = self._fetch_cache.get(cache_key)
        if hit is not None:
            df, rng = hit
            return df.copy(), rng
        wh, args = self.where(table, period, filters, extra)
        sel = ", ".join(f"{dmap[d]} AS {d}" for d in dims)
        grp = (" GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims)))) if dims else ""
        sql = (f"SELECT {sel + ', ' if sel else ''}{metrics.BASE_SQL}, "
               f"COUNT(DISTINCT metrics_date) AS days FROM {table}{wh}{grp}"
               f"{(' HAVING ' + having) if having else ''}"
               f"{(' ORDER BY ' + order) if order and dims else ''}"
               f"{(' LIMIT ' + str(int(limit))) if limit else ''}")
        df = self.sql(sql, args)
        rng = self.con.execute(f"SELECT min(metrics_date), max(metrics_date) FROM {table}{wh}", args).fetchone()
        df = metrics.add_ratios(df)
        if "match_type" in df:
            df["match_type"] = df["match_type"].map(clean_match_type)
        if "bidding" in df:
            df["bidding"] = df["bidding"].map(clean_bidding)
        if "product" in df:
            df["product"] = df["product"].map(lambda x: short_sku(x, self.sku_map))
        self._fetch_cache[cache_key] = (df.copy(), rng)
        return df, rng

    def totals(self, table, period=None, filters=None, extra=""):
        df, rng = self.fetch(table, (), period, filters, extra)
        return (df.iloc[0].to_dict() if not df.empty else {}), rng

    # -- period helpers -------------------------------------------------------
    def effective_period(self, p: Params, table="granular") -> Period | None:
        lo, hi = self.bounds(table)
        if lo is None:
            return None
        if p.period is not None:
            return p.period
        return Period(lo, hi, "all loaded data")

    def observed_lag_start(self, table="granular"):
        """First date of the trailing run of days where direct 14d GMV equals
        direct 7d GMV (a data-observed sign the days are still collecting
        sales). None if the last day shows no such sign."""
        if not self.has_rows(table):
            return None
        df = self.sql(f"SELECT metrics_date d, SUM(total_direct_gmv_7d) a, SUM(total_direct_gmv_14d) b "
                      f"FROM {table} GROUP BY 1 ORDER BY 1 DESC")
        start = None
        for d, a, b in zip(df["d"], df["a"], df["b"]):
            if a is not None and b is not None and abs((a or 0) - (b or 0)) < 1e-6 and (a or 0) > 0:
                start = pd.Timestamp(d).date()
            else:
                break
        return start

    def lag_days(self):
        v = self.set("lag_days")
        return int(v) if v is not None else None


def range_text(rng) -> str:
    lo, hi = rng
    if lo is None:
        return ""
    return f"{lo.isoformat()} to {hi.isoformat()}" if lo != hi else lo.isoformat()


def filters_from(p: "Params", table: str) -> dict:
    """Turn extracted entities into whitelisted filters for a table."""
    f = {
        "campaign": p.ent("campaigns"), "keyword": p.ent("keywords"),
        "product": p.ent("products"), "placement": p.ent("placements"),
    }
    if table == "granular":
        f["city"] = p.ent("cities")
    else:
        f["query"] = p.ent("queries")
    return {k: v for k, v in f.items() if v}


def num(df: pd.DataFrame, col: str) -> pd.Series:
    """Numeric view of a column (None -> NaN) for filtering and sorting."""
    return pd.to_numeric(df[col], errors="coerce")
