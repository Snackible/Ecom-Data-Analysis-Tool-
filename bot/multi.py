"""Compound-question splitter and dependent-question rewriter.

An ecom manager often asks several things at once ("which city spends most and
which skus are weak"), or asks a dependent follow-up ("why?", "and roas?",
"same but for Pune"). This file splits a compound message into atomic
sub-questions and rewrites dependent follow-ups against the previous answer so
the regular `engine.ask` can handle them.

Pure text work: no SQL, no network.
"""
import re

# Phrases a manager uses to join two questions. Order from longest to shortest.
_JOINERS = [
    r";\s+and\s+",
    r"\s+and also\s+",
    r"\s+also,?\s+",
    r"\s+plus,?\s+",
    r"\s+then,?\s+",
    r";\s+",
    r"\s+and (?=which|what|who|where|when|how|why|list|show|rank|give|tell|should|can|is|are|do|does)",
    r"\.\s+(?=which|what|who|where|when|how|why|list|show|rank|give|tell|should|can|is|are|do|does)",
    r"\?\s+(?=which|what|who|where|when|how|why|list|show|rank|give|tell|should|can|is|are|do|does)",
]
_JOINER_RE = re.compile("(?i)(" + "|".join(_JOINERS) + ")")

# Words that signal a sub-question (used to filter false splits).
_QUESTION_HEAD = re.compile(
    r"^\s*(which|what|who|where|when|how|why|list|show|rank|give|tell|should|can|is|are|do|does|top|bottom|rank)\b",
    re.I)
_DOMAIN = re.compile(
    r"\b(spend|budget|impressions?|clicks?|ctr|cpc|cpm|roas|gmv|revenue|sales|orders?|conversions?|a2c|a2s|cart|"
    r"campaigns?|keywords?|quer(y|ies)|searches|cit(y|ies)|sku|skus|product|products|placements?|match|broad|exact|"
    r"attack|defend|expand|negative|bid|bids|rank|waste|bleeding|change|increase|cut|save|share|split)\b", re.I)

# Dependent markers: these need the previous question to make sense.
_WHY_RE = re.compile(r"^\s*(why|why is that|why so|explain|reason|how come|because)\b|^\s*why\s*\?*\s*$", re.I)
_EXPAND_RE = re.compile(r"^\s*(break (it|that) down|more detail|dig deeper|go deeper|deep dive|elaborate|tell me more)\b", re.I)
# "and roas?" / "what about gmv" / "same for cpm"
_METRIC = {
    "roas": "roas", "gmv": "gmv", "revenue": "gmv", "sales": "gmv",
    "spend": "spend", "budget": "spend", "cost": "spend",
    "cpm": "cpm", "cpc": "cpc", "ctr": "ctr",
    "clicks": "clicks", "impressions": "impressions",
    "orders": "orders", "conversions": "conversions", "a2c": "a2c",
}
_METRIC_RE = re.compile(
    r"^\s*(and|what about|how about|now|also|same (?:but )?for)\s+(?:the\s+)?(" +
    "|".join(sorted(_METRIC, key=len, reverse=True)) + r")\s*\??\s*$", re.I)


def split_compound(text: str, max_parts: int = 3) -> list[str]:
    """Split a message into atomic sub-questions. Returns [text] when it
    does not look compound. Caps at `max_parts` pieces."""
    t = (text or "").strip()
    if len(t) < 20:
        return [t] if t else []
    # Quoted sections are kept intact ("snackible chips" must not split).
    guards: list[str] = []

    def hide(m):
        guards.append(m.group(0))
        return f"\x00{len(guards) - 1}\x00"

    masked = re.sub(r"[\"'][^\"']+[\"']", hide, t)
    parts = _JOINER_RE.split(masked)
    if len(parts) <= 1:
        return [t]
    # re.split keeps the joiner groups too; keep only the odd-indexed text pieces
    pieces = [parts[0]] + [parts[i] for i in range(2, len(parts), 2)]
    cleaned = []
    for p in pieces:
        p = p.strip(" ,.;?!")
        if not p:
            continue
        # un-mask quoted sections
        for i, g in enumerate(guards):
            p = p.replace(f"\x00{i}\x00", g)
        cleaned.append(p)
    # every piece must either start like a question or carry a domain word; otherwise
    # the split is a false alarm (e.g. a comma inside a longer sentence).
    def real(x):
        return bool(_QUESTION_HEAD.match(x) or _DOMAIN.search(x)) and len(x.split()) >= 2
    if len(cleaned) >= 2 and all(real(x) for x in cleaned):
        return cleaned[:max_parts]
    return [t]


def is_why(text: str) -> bool:
    return bool(_WHY_RE.match((text or "").strip()))


def is_expand(text: str) -> bool:
    return bool(_EXPAND_RE.match((text or "").strip()))


def metric_swap(text: str, prev_text: str | None) -> str | None:
    """`and roas?` after `rank cities by spend` becomes `rank cities by roas`."""
    if not prev_text:
        return None
    m = _METRIC_RE.match((text or "").strip())
    if not m:
        return None
    new = _METRIC[m.group(2).lower()]
    prev = prev_text.lower()
    swapped = re.sub(
        r"\b(spend|spent|budget|cost|gmv|revenue|sales|roas|cpm|cpc|ctr|clicks|impressions|orders|conversions|a2c)\b",
        new, prev, count=1)
    if swapped == prev:
        return f"{prev_text}; by {new}"
    return swapped


def expanded_text(prev_text: str | None) -> str | None:
    """`why?` after a prior question re-runs the previous question so the deep
    dive runs again on it. The caller may lean on `prev` to inherit entities
    and period; the text itself is the previous one."""
    return prev_text or None


def compound_summary(results) -> str:
    """One-line roll-up over several sub-results, so a stacked answer still
    reads as one thought. Pulls the headline of each and the kind."""
    if not results or len(results) < 2:
        return ""
    heads = []
    for r in results:
        h = (r.headline or "").strip()
        if h:
            heads.append(h[:140])
    if not heads:
        return ""
    return "Across your questions: " + " / ".join(heads)
