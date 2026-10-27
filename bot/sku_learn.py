"""Suggest short SKU names from raw product titles so the manager does not
have to hand-write every entry in settings.product_title_to_sku.

Rules, in order:
1. Strip a leading brand prefix (default: 'snackible').
2. Drop a trailing weight ('60g', '100 g', '250 ml', '1 kg').
3. Drop a trailing size code or tag after a comma ('X Y, roasted' -> 'X Y').
4. Title-case what is left and collapse whitespace.

Nothing is written automatically; the chat tab shows these as proposals and
only writes to settings.yaml on the user's click.
"""
import re

_WEIGHT = re.compile(r"\s*[-,]?\s*\d+(?:\.\d+)?\s*(?:g|ml|kg|gm|gms|grams|l|litre|litres)\b.*$", re.I)
_TAIL_TAG = re.compile(r"\s*,.*$")
_PREFIX = re.compile(r"^(the\s+)?snack(?:ible|able|ibel)s?\b[-:\s]*", re.I)
_WS = re.compile(r"\s+")


def short_name(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    s = _PREFIX.sub("", s)
    s = _WEIGHT.sub("", s)
    s = _TAIL_TAG.sub("", s)
    s = _WS.sub(" ", s).strip(" -")
    return s.title() if s else ""


def propose(titles, existing: dict | None = None) -> list[tuple[str, str]]:
    """Return [(raw_title, suggested_short)] for titles not yet mapped. Skips
    duplicates and empty suggestions."""
    done = {str(k).strip().lower() for k in (existing or {}).keys()}
    seen, out = set(), []
    for t in titles or []:
        if t is None:
            continue
        raw = str(t).strip()
        if not raw or raw.lower() in done:
            continue
        s = short_name(raw)
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append((raw, s))
    return out
