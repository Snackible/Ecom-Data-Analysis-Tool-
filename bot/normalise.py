"""Text and value cleaning shared by the matcher, params and queries."""
import re

# Spelling variants: only applied in theme-level views and in question
# matching. Query-level views show the raw (lowercased, trimmed) text.
VARIANTS = {
    "khakra": "khakhra", "kakra": "khakhra", "khakhara": "khakhra",
    "protien": "protein", "proten": "protein", "protean": "protein",
    "snackibel": "snackible", "snakible": "snackible", "snackable": "snackible",
    "snackables": "snackible", "snackibles": "snackible",
    "chip": "chips", "biscuit": "biscuits", "puff": "puffs",
}

# Abbreviations in questions -> canonical words.
ABBREVIATIONS = {
    "wow": "week on week", "w/w": "week on week", "mom": "month on month",
    "vs": "versus", "kw": "keyword", "kws": "keywords", "sq": "search query",
    "sqs": "search queries", "impr": "impressions", "imps": "impressions",
    "skus": "sku", "blr": "bangalore", "bengaluru": "bangalore",
    "bombay": "mumbai", "gurgaon": "gurugram",
}

_PUNCT = re.compile(r"[^\w\s'/%.-]")
_WS = re.compile(r"\s+")


def norm_query(s) -> str:
    """Search-query normalisation: lowercase, trim, collapse spaces."""
    if s is None:
        return ""
    return _WS.sub(" ", str(s).strip().lower())


def theme_key(s) -> str:
    """Group spelling variants together (theme-level views only)."""
    return " ".join(VARIANTS.get(w, w) for w in norm_query(s).split())


def norm_text(s) -> str:
    """Normalise a user question for matching."""
    s = str(s or "").lower().replace("’", "'").replace("‘", "'")
    s = s.replace("–", "-").replace("—", "-")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    out = []
    for w in s.split():
        w = w.strip(".,")
        w = VARIANTS.get(w, w)
        w = ABBREVIATIONS.get(w, w)
        out.append(w)
    return " ".join(out)


# ---- data values -----------------------------------------------------------

def norm_city(city):
    """Merge Noida and Noida 1; every other name is only trimmed."""
    if city is None:
        return None
    c = _WS.sub(" ", str(city).strip())
    if c.lower() in ("noida", "noida 1"):
        return "Noida"
    return c


def duplicate_city_candidates(cities) -> list:
    """Groups of city names that look like the same city (case, spaces,
    trailing digits, punctuation). Noida/Noida 1 is merged automatically and
    excluded; anything else is only flagged, never merged."""
    groups = {}
    for c in cities:
        if c is None:
            continue
        raw = _WS.sub(" ", str(c).strip())
        if raw.lower() in ("noida", "noida 1"):
            continue
        key = re.sub(r"[\s\d\W_]+", "", raw.lower())
        groups.setdefault(key, set()).add(raw)
    return [sorted(v) for v in groups.values() if len(v) > 1]


def clean_match_type(m):
    if m is None:
        return None
    s = str(m).upper().replace("KEYWORD_MATCH_TYPE_", "").strip()
    return s.title() if s else None  # Broad / Exact / Invalid


def clean_bidding(b):
    if b is None:
        return None
    s = str(b).upper().replace("BIDDING_STRATEGY_TYPE_", "").strip()
    return s or None


def short_sku(product_name, sku_map) -> str:
    """Short SKU name from settings.product_title_to_sku, else the raw title
    truncated to 40 characters."""
    if product_name is None:
        return "(no product)"
    name = str(product_name).strip()
    if sku_map:
        low = {str(k).strip().lower(): v for k, v in sku_map.items()}
        if name.lower() in low:
            return low[name.lower()]
    return name if len(name) <= 40 else name[:39] + "~"


# ---- looser matching (conversational wording) ---------------------------------
# Word-level synonyms: everyday wording -> the words the question bank uses.
SYNONYMS = {
    "money": "budget", "cash": "budget", "rupees": "budget", "investment": "budget",
    "wasting": "wasteful", "wasted": "wasteful", "waste": "wasteful", "burning": "wasteful", "bleeding": "wasteful",
    "leaking": "wasteful", "losing": "wasteful",
    "worst": "weak", "poor": "weak", "bad": "weak", "underperforming": "weak", "terrible": "weak", "lowest": "weak",
    "best": "strong", "winning": "strong", "top": "strong", "highest": "strong", "biggest": "strong",
    "expensive": "costly", "pricey": "costly", "overpaying": "costly",
    "earning": "revenue", "income": "revenue", "sales": "revenue", "returns": "revenue",
    "doing": "performing", "going": "performing", "perform": "performing", "performance": "performing",
    "fix": "change", "improve": "change", "tweak": "change", "adjust": "change", "modify": "change",
    "boost": "increase", "raise": "increase", "grow": "increase", "scale": "increase", "bump": "increase",
    "reduce": "cut", "trim": "cut", "slash": "cut", "lower": "cut", "decrease": "cut",
    "stop": "pause", "kill": "pause", "switch": "change",
    "terms": "queries", "searches": "queries", "searched": "queries", "phrases": "queries",
    "towns": "cities", "metros": "cities", "locations": "cities", "regions": "cities",
    "products": "sku", "items": "sku", "variants": "sku",
    "ads": "ads", "advertising": "ads", "adverts": "ads",
}


def stem(w: str) -> str:
    """Very light stemmer: plural and common verb endings."""
    if len(w) <= 4 or w.isdigit():
        return w
    for suf, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not w.endswith("ss"):
            return w[: -len(suf)] + keep
    return w


def norm_match(text) -> str:
    """norm_text plus synonyms and light stemming. Used only by the question matcher."""
    out = []
    for w in norm_text(text).split():
        w = SYNONYMS.get(w, w)
        out.append(stem(w))
    return " ".join(out)
