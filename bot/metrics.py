"""Snackible metric definitions. Ratios are always recomputed from totals."""
import math

import pandas as pd

BASE_SQL = (
    "SUM(total_budget_burnt) AS spend, SUM(total_impressions) AS impressions, "
    "SUM(total_clicks) AS clicks, SUM(total_a2c) AS a2c, SUM(total_gmv) AS gmv, "
    "SUM(total_conversions) AS conversions"
)


def _bad(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA


def sdiv(n, d):
    """None for missing values or a zero denominator (shown as n/a)."""
    if _bad(n) or _bad(d) or d == 0:
        return None
    return n / d


def roas(gmv, spend):
    return sdiv(gmv, spend)


def ctr(clicks, impressions):
    return sdiv(clicks, impressions)


def cpc(spend, clicks):
    return sdiv(spend, clicks) if not _bad(clicks) and clicks > 0 else None


def cpm(spend, impressions):
    v = sdiv(spend, impressions)
    return None if v is None else v * 1000


def a2c_rate(a2c, clicks):
    return sdiv(a2c, clicks)


def conv_rate(conversions, clicks):
    return sdiv(conversions, clicks)


def growth(cur, prev):
    """Relative change; n/a from a zero or missing base."""
    if _bad(cur) or _bad(prev) or prev == 0:
        return None
    return (cur - prev) / prev


def break_even_roas(contribution):
    return sdiv(1, contribution) if contribution else None


def marginal_roas(gmv0, spend0, gmv1, spend1):
    if any(_bad(x) for x in (gmv0, spend0, gmv1, spend1)):
        return None
    return sdiv(gmv1 - gmv0, spend1 - spend0)


def add_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Add roas, ctr, cpc, cpm, a2c_rate, conv_rate from summed columns.
    Missing denominators give None, never 0 and never a mean of ratios."""
    if df.empty:
        return df
    df = df.copy()

    def col(fn, a, b):
        return pd.Series([fn(x, y) for x, y in zip(df[a], df[b])], index=df.index, dtype="object")

    df["roas"] = col(roas, "gmv", "spend")
    df["ctr"] = col(ctr, "clicks", "impressions")
    df["cpc"] = col(cpc, "spend", "clicks")
    df["cpm"] = col(cpm, "spend", "impressions")
    if "a2c" in df:
        df["a2c_rate"] = col(a2c_rate, "a2c", "clicks")
    if "conversions" in df:
        df["conv_rate"] = col(conv_rate, "conversions", "clicks")
    return df
