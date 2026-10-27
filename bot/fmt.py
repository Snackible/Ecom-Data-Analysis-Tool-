"""Number formatting: rupees with Indian grouping, ROAS 2 dp, percent 1 dp.
No em dashes anywhere in bot output."""
import math

import pandas as pd

NA = "n/a"


def _bad(x):
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def _indian_group(n: int) -> str:
    s = str(abs(int(n)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + s


def inr(x, decimals: int = 0, short: bool = True) -> str:
    """₹1,38,378 below one lakh; ₹1.38L / ₹1.38Cr above (short=True)."""
    if _bad(x):
        return NA
    x = float(x)
    a = abs(x)
    sign = "-" if x < 0 else ""
    if short and a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f}Cr"
    if short and a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f}L"
    if decimals:
        whole = int(a)
        frac = f"{a - whole:.{decimals}f}"[1:]
        return f"{sign}₹{_indian_group(whole)}{frac}"
    return f"{sign}₹{_indian_group(abs(round(x)))}"


def pct(x, decimals: int = 1) -> str:
    return NA if _bad(x) else f"{float(x) * 100:.{decimals}f}%"


def roas(x) -> str:
    return NA if _bad(x) else f"{float(x):.2f}"


def num(x) -> str:
    return NA if _bad(x) else _indian_group(round(float(x)))


def signed_pct(x) -> str:
    return NA if _bad(x) else f"{float(x) * 100:+.1f}%"


def clean(text: str) -> str:
    """Final guard: no em or en dashes in anything shown."""
    return str(text).replace("—", "-").replace("–", "-")
