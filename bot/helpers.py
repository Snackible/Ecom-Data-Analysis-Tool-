"""Small DataFrame helpers shared by the query modules."""
import pandas as pd

from . import fmt, metrics

BASE_COLS = ["spend", "impressions", "clicks", "a2c", "gmv", "conversions"]


def sum_by(df: pd.DataFrame, key) -> pd.DataFrame:
    """Group, sum base metrics, recompute ratios (never average ratios)."""
    if df.empty:
        return df
    g = df.groupby(key, dropna=False)[BASE_COLS].sum(min_count=1).reset_index()
    return metrics.add_ratios(g)


def add_share(df: pd.DataFrame, col: str, name: str) -> pd.DataFrame:
    tot = df[col].sum()
    df[name] = pd.Series([metrics.sdiv(x, tot) for x in df[col]], index=df.index, dtype="object")
    return df


def loss_below_break_even(spend, gmv, be):
    """Rupees of spend not covered by contribution (gmv / break-even ROAS).
    With no break-even set the floor is ROAS 1 (gmv >= spend)."""
    if spend is None or gmv is None:
        return None
    covered = gmv / be if be else gmv
    return max(0.0, spend - covered)


def is_nan(x) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and x != x)


def date_line(rng) -> str:
    from .core import range_text
    t = range_text(rng)
    return f"Date range: {t}" if t else ""


def lag_note(ctx, rng, table="granular"):
    """Notes about attribution lag for a range, or []."""
    lo, hi = rng
    out = []
    if lo is None:
        return out
    lag = ctx.lag_days()
    if lag is None:
        out.append("needs setting: lag_days (recent days may still be collecting sales, so no lag-dependent judgement was made)")
    else:
        from datetime import timedelta
        _, dmax = ctx.bounds(table)
        if dmax is not None:
            start = dmax - timedelta(days=lag - 1)
            if hi >= start:
                out.append(f"{max(lo, start).isoformat()} to {hi.isoformat()} is still collecting sales; "
                           "a drop there is not proof of decline")
    obs = ctx.observed_lag_start(table)
    if obs is not None and hi >= obs:
        out.append(f"Fact: from {obs.isoformat()} direct 14-day GMV equals 7-day GMV, which suggests those days are still collecting sales")
    return out


def cols_for(df: pd.DataFrame, wanted):
    return df[[c for c in wanted if c in df.columns]]


def top(df: pd.DataFrame, n: int, by: str, asc=False) -> pd.DataFrame:
    if df.empty or by not in df:
        return df
    key = pd.to_numeric(df[by], errors="coerce")
    return df.assign(_k=key).sort_values("_k", ascending=asc, na_position="last").drop(columns="_k").head(n)


def inr_k(x):
    return fmt.inr(x)
