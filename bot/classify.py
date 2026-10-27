"""Attack / defend / expand classification, by keyword bid and by query served."""
import re

DEFEND_BUILTIN = ["snackible", "snackibel", "snakible", "snackable", "snackables", "dipsters"]
ATTACK_BUILTIN = [
    "too yum", "troovy", "let's try", "lets try", "choki choki", "open secret",
    "taali", "noice", "nova nova", "dark fantasy", "jim jam", "oreo", "choco pie",
    "milk bikis", "waffy", "mad angles", "tedhe medhe", "uncle chips", "bingo",
    "lays", "kurkure", "haldiram", "pringles", "cornitos", "act ii",
    "the whole truth", "yoga bar", "farmley", "happilo",
]


def _prep(text) -> str:
    s = str(text or "").lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", s).strip()


def _compile(terms):
    uniq = sorted({_prep(t) for t in terms if _prep(t)}, key=len, reverse=True)
    if not uniq:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in uniq) + r")(?![a-z0-9])")


class Classifier:
    def __init__(self, extra_defend=None, extra_attack=None):
        self.defend_terms = list(DEFEND_BUILTIN) + list(extra_defend or [])
        self.attack_terms = list(ATTACK_BUILTIN) + list(extra_attack or [])
        self._d = _compile(self.defend_terms)
        self._a = _compile(self.attack_terms)

    @classmethod
    def from_settings(cls, settings: dict):
        return cls(settings.get("defend_terms") or [], settings.get("attack_terms") or [])

    def bucket(self, text) -> str:
        """defend if a Snackible brand term is present, else attack if a
        competitor term is present, else expand."""
        s = _prep(text)
        if not s:
            return "expand"
        if self._d and self._d.search(s):
            return "defend"
        if self._a and self._a.search(s):
            return "attack"
        return "expand"

    def competitor(self, text):
        """Which competitor term matched (for closeness tiers)."""
        m = self._a.search(_prep(text)) if self._a else None
        return m.group(0) if m else None

    def has_brand(self, text) -> bool:
        return bool(self._d and self._d.search(_prep(text)))


def contains_keyword(query, keyword) -> bool:
    """True when the served query contains the bid keyword (word-level, so
    'chips' is in 'banana chips'). Used for broad-match spill detection."""
    q, k = _prep(query), _prep(keyword)
    if not k:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", q) is not None
