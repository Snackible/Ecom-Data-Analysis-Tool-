"""Pull dates, entities and numbers out of a question.

Dates are relative to Asia/Kolkata. Because the loaded data can lag behind
today, relative phrases are anchored to min(today in IST, latest loaded
date), and the anchor is reported so the answer can say so. Weeks start on
Monday.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from rapidfuzz import fuzz, process

from .normalise import norm_text

# India has no DST, so a fixed +05:30 offset is exactly Asia/Kolkata and
# avoids needing the tzdata package on Windows.
IST = timezone(timedelta(hours=5, minutes=30))

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MON_RE = "|".join(sorted(MONTHS, key=len, reverse=True))


def today_ist(now: datetime | None = None) -> date:
    return (now.astimezone(IST) if now else datetime.now(IST)).date()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


@dataclass
class Period:
    start: date
    end: date
    label: str
    note: str = ""  # e.g. anchoring explanation

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def previous_period(p: Period) -> Period:
    """The equal-length period immediately before p."""
    n = p.days
    end = p.start - timedelta(days=1)
    return Period(end - timedelta(days=n - 1), end, "previous period", p.note)


def reference_date(min_date, max_date, now=None) -> tuple[date, str]:
    t = today_ist(now)
    if max_date is not None and max_date < t:
        return max_date, f"latest loaded date is {max_date.isoformat()}, so relative dates count from there"
    return t, ""


def _month_period(month: int, year: int | None, ref: date, max_date, label_src: str):
    if year is None:
        year = ref.year
        if date(year, month, 1) > ref:
            year -= 1
    start = date(year, month, 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    return Period(start, nxt - timedelta(days=1), label_src.title())


def parse_dates(text: str, min_date=None, max_date=None, now=None) -> dict:
    """Returns {'periods': [Period...], 'compare': bool}. periods are in the
    order they appear in the question."""
    t = norm_text(text)
    ref, note = reference_date(min_date, max_date, now)
    periods: list[tuple[int, Period]] = []

    def add(pos, p):
        p.note = note
        periods.append((pos, p))

    taken = []

    def free(m):
        return not any(a < m.end() and m.start() < b for a, b in taken)

    def claim(m):
        taken.append((m.start(), m.end()))

    # explicit ISO ranges / dates
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", t):
        claim(m)
    iso = re.findall(r"\d{4}-\d{2}-\d{2}", t)
    if iso:
        ds = [date.fromisoformat(x) for x in iso]
        if len(ds) >= 2:
            add(0, Period(min(ds[:2]), max(ds[:2]), f"{min(ds[:2])} to {max(ds[:2])}"))
        else:
            add(0, Period(ds[0], ds[0], str(ds[0])))
    # "3 sept [2026]" / "sept 3" single days and "3 sept to 9 sept" ranges
    dm = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON_RE})\b(?:\s+(\d{{4}}))?|\b({_MON_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s+(\d{{4}}))?")
    days = []
    for m in dm.finditer(t):
        if not free(m):
            continue
        if m.group(1):
            d_, mo, y = int(m.group(1)), MONTHS[m.group(2)], m.group(3)
        else:
            d_, mo, y = int(m.group(5)), MONTHS[m.group(4)], m.group(6)
        yr = int(y) if y else (ref.year if date(ref.year, mo, min(d_, 28)) <= ref else ref.year - 1)
        try:
            days.append((m.start(), date(yr, mo, d_)))
            claim(m)
        except ValueError:
            continue
    if len(days) >= 2 and re.search(r"\b(to|till|until|through|-)\b", t):
        a, b = days[0][1], days[1][1]
        add(days[0][0], Period(min(a, b), max(a, b), f"{min(a, b)} to {max(a, b)}"))
    else:
        for pos, d_ in days:
            add(pos, Period(d_, d_, d_.isoformat()))

    rel_patterns = [
        (r"\b(?:last|past|previous)\s+(\d+)\s+days?\b", lambda m: Period(ref - timedelta(days=int(m.group(1)) - 1), ref, f"last {m.group(1)} days")),
        (r"\b(?:last|past|previous)\s+(\d+)\s+weeks?\b", lambda m: Period(ref - timedelta(days=7 * int(m.group(1)) - 1), ref, f"last {m.group(1)} weeks")),
        (r"\byesterday\b", lambda m: Period(ref - timedelta(days=1), ref - timedelta(days=1), "yesterday")),
        (r"\btoday\b", lambda m: Period(ref, ref, "today")),
        (r"\blast week\b|\bprevious week\b|\bprior week\b", lambda m: Period(week_start(ref) - timedelta(days=7), week_start(ref) - timedelta(days=1), "last week")),
        (r"\bthis week\b|\bcurrent week\b", lambda m: Period(week_start(ref), ref, "this week")),
        (r"\blast month\b|\bprevious month\b", lambda m: _month_period((ref.replace(day=1) - timedelta(days=1)).month, (ref.replace(day=1) - timedelta(days=1)).year, ref, max_date, "last month")),
        (r"\bthis month\b|\bcurrent month\b", lambda m: Period(ref.replace(day=1), ref, "this month")),
    ]
    for pat, fn in rel_patterns:
        for m in re.finditer(pat, t):
            if free(m):
                claim(m)
                add(m.start(), fn(m))

    for m in re.finditer(rf"\b({_MON_RE})(?:\s+(\d{{4}}))?\b", t):
        if not free(m):
            continue
        if m.group(1) == "may" and not re.search(r"\b(in|of|for|during|from|vs|versus|and|with)\s+may\b|\bmay\s+(vs|versus|data|month|\d{4})\b", t):
            continue
        claim(m)
        yr = int(m.group(2)) if m.group(2) else None
        if yr is None and max_date is not None:
            # prefer the most recent such month at or before the latest data
            mo = MONTHS[m.group(1)]
            yr = max_date.year if date(max_date.year, mo, 1) <= max_date else max_date.year - 1
        add(m.start(), _month_period(MONTHS[m.group(1)], yr, ref, max_date, m.group(1)))

    periods.sort(key=lambda x: x[0])
    ps = [p for _, p in periods]
    compare = len(ps) >= 2 or bool(re.search(
        r"\b(compar\w*|versus|against|week on week|month on month|change[sd]? (from|since)|than last)\b", t)) \
        or ("how did" in t and "change" in t)
    return {"periods": ps, "compare": compare, "reference": ref, "note": note}


# ---- entities --------------------------------------------------------------

@dataclass
class Catalog:
    """Distinct values discovered from the database at runtime."""
    campaigns: list = field(default_factory=list)
    cities: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    products: list = field(default_factory=list)
    placements: list = field(default_factory=list)
    queries: set = field(default_factory=set)


_STOP = {"city", "cities", "campaign", "campaigns", "keyword", "keywords", "week", "total", "all", "other",
         "na", "nan", "none", "search", "chips"}


def _ngrams(words, maxn=4):
    for n in range(maxn, 0, -1):
        for i in range(len(words) - n + 1):
            yield " ".join(words[i:i + n])


def _find(text_norm: str, items, fuzzy=True, min_len=4, score=90):
    """Return matched catalog items. Exact whole-phrase hits first, then fuzzy
    n-gram hits (typos). Longest matches win; a hit inside a longer hit is
    dropped."""
    low = {}
    for it in items:
        if it is None:
            continue
        k = norm_text(it)
        if len(k) >= 2 and k not in _STOP:
            low.setdefault(k, it)
    hits = []
    for k, orig in low.items():
        if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", text_norm):
            hits.append((len(k), orig))
    if not hits and fuzzy and low:
        keys = [k for k in low if len(k) >= min_len]
        words = text_norm.split()
        for g in _ngrams(words, 3):
            if len(g) < min_len or g in _STOP:
                continue
            r = process.extractOne(g, keys, scorer=fuzz.ratio, score_cutoff=min(score, 82))
            # short names need a tighter match to avoid false hits
            if r and (len(r[0]) >= 6 or r[1] >= score):
                hits.append((len(r[0]), low[r[0]]))
    hits.sort(key=lambda x: -x[0])
    out, seen = [], []
    for ln, orig in hits:
        k = norm_text(orig)
        if any(k in s for s in seen):
            continue
        seen.append(k)
        out.append(orig)
    return out


def extract_entities(text: str, cat: Catalog) -> dict:
    t = norm_text(text)
    quoted = re.findall(r"[\"']([^\"']{2,60})[\"']", str(text).replace("“", '"').replace("”", '"'))
    quoted = [q.strip().lower() for q in quoted]
    ents = {
        "cities": _find(t, cat.cities, min_len=4),
        "campaigns": _find(t, cat.campaigns, min_len=5, score=88),
        "keywords": _find(t, cat.keywords, fuzzy=False),
        "products": _find(t, cat.products, fuzzy=False, min_len=5),
        "placements": _find(t, cat.placements, fuzzy=False),
        "quoted": quoted,
    }
    # a quoted phrase that is a known search query is a query filter
    ents["queries"] = [q for q in quoted if q in cat.queries]
    return ents


def extract_top_n(text: str, default: int = 10) -> int:
    t = norm_text(text)
    m = re.search(r"\b(?:top|bottom|first|worst|best|lowest|highest|biggest|largest|smallest)\s+(\d{1,3})\b", t) \
        or re.search(r"\b(\d{1,3})\s+(?:lowest|highest|worst|best|biggest|largest|smallest|top|bottom)\b", t) \
        or re.search(r"\blist\s+(?:the\s+)?(?:top\s+)?(\d{1,3})\b", t)
    return min(int(m.group(1)), 200) if m else default
