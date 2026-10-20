"""Question matcher: rapidfuzz + TF-IDF over the question bank.

Reads bot/questions.md (numbered questions) and bot/paraphrases.txt (extra
phrasings). A line in questions.md may end with '=> Q<n>' (reuse the handler
of question n) or '=> flex' (send to the free-form engine), which is how new
questions are added without touching code.

No network, no LLM: purely local string similarity.
"""
import csv
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz, process

from .normalise import norm_text

BOT_DIR = Path(__file__).resolve().parent
ROOT = BOT_DIR.parent
LOG_DIR = ROOT / "logs"
MISS_LOG = LOG_DIR / "miss_log.csv"

ACCEPT = 0.60   # minimum combined score to accept a match
MARGIN = 0.02   # a different question must trail by at least this much

# Questions that run the same analysis: a near-tie inside a group is not ambiguous.
EQUIV = [{5, 6}, {8, 78}, {9, 39, 44}, {10, 32, 87}, {2, 65}, {1, 24}, {90, 91, 92, 93, 94}, {12, 13}, {7, 100}, {22, 23, 24}]


@dataclass
class Match:
    qn: int | None          # question number whose handler to run (None = no match)
    score: float
    text: str = ""          # the matched question text
    route: str = "bank"     # bank | flex | none
    second: float = 0.0


def load_bank(questions_path=None, para_path=None):
    """Returns (docs, routes): docs = [(qn, text)], routes = {qn: 'Q<n>'|'flex'}."""
    qp = Path(questions_path or BOT_DIR / "questions.md")
    pp = Path(para_path or BOT_DIR / "paraphrases.txt")
    docs, routes, texts = [], {}, {}
    for line in qp.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)$", line.strip())
        if not m:
            continue
        qn, body = int(m.group(1)), m.group(2)
        route = None
        r = re.search(r"\s*=>\s*(Q\d+|flex)\s*$", body)
        if r:
            route = r.group(1)
            body = body[:r.start()]
        body = re.sub(r"\s+Judgement question:.*$", "", body)
        body = re.sub(r"\s+\((Cost per order uses|City tiers come|A search does not match).*\)$", "", body)
        docs.append((qn, body))
        texts[qn] = body
        if route:
            routes[qn] = route
    if pp.exists():
        for line in pp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            n, phrase = line.split("|", 1)
            if n.strip().isdigit():
                docs.append((int(n.strip()), phrase.strip()))
    return docs, routes, texts


class TfidfLite:
    """Small TF-IDF (sublinear tf, l2-normalised, cosine) in plain Python.

    The question bank is a few hundred short phrases, so this is instant, and
    it avoids importing scikit-learn/scipy (about 75MB of RAM) on a 512MB host.
    """

    def __init__(self, analyzer="word", ngram_range=(1, 2)):
        self.analyzer, self.lo, self.hi = analyzer, ngram_range[0], ngram_range[1]
        self.idf = {}

    def _terms(self, text):
        if self.analyzer == "word":
            w = text.split()
            return [" ".join(w[i:i + n]) for n in range(self.lo, self.hi + 1) for i in range(len(w) - n + 1)]
        out = []
        for word in text.split():
            word = f" {word} "
            for n in range(self.lo, self.hi + 1):
                out += [word[i:i + n] for i in range(len(word) - n + 1)]
        return out

    def _vec(self, text, use_idf=True):
        c = Counter(self._terms(text))
        v = {t: (1 + math.log(f)) * (self.idf.get(t, 0.0) if use_idf else 1.0) for t, f in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items() if x}

    def fit(self, docs):
        df = Counter()
        for d in docs:
            df.update(set(self._terms(d)))
        n = len(docs)
        self.idf = {t: math.log((1 + n) / (1 + f)) + 1 for t, f in df.items()}
        self.matrix = [self._vec(d) for d in docs]
        return self

    def sims(self, text):
        q = self._vec(text)
        return [sum(w * d.get(t, 0.0) for t, w in q.items()) for d in self.matrix]


class Matcher:
    def __init__(self, questions_path=None, para_path=None):
        self.docs, self.routes, self.texts = load_bank(questions_path, para_path)
        self.norm = [norm_text(t) for _, t in self.docs]
        self.word = TfidfLite("word", (1, 2)).fit(self.norm)
        self.char = TfidfLite("char_wb", (3, 5)).fit(self.norm)

    def match(self, text: str) -> Match:
        t = norm_text(text)
        if not t:
            return Match(None, 0.0, route="none")
        sw = self.word.sims(t)
        sc = self.char.sims(t)
        fz = [fuzz.WRatio(t, d) / 100 for d in self.norm]
        scores = [0.6 * (0.4 * a + 0.6 * b) + 0.4 * c for a, b, c in zip(sw, sc, fz)]
        # best score per question number
        best = {}
        for (qn, _), s in zip(self.docs, scores):
            if s > best.get(qn, 0):
                best[qn] = s
        ranked = sorted(best.items(), key=lambda x: -x[1])
        (q1, s1) = ranked[0]
        group = next((g for g in EQUIV if q1 in g), {q1})
        others = [x for x in ranked[1:] if x[0] not in group]
        s2 = others[0][1] if others else 0.0
        if s1 < ACCEPT or s1 - s2 < MARGIN:
            return Match(None, s1, self.texts.get(q1, ""), "none", s2)
        route = self.routes.get(q1)
        if route == "flex":
            return Match(q1, s1, self.texts.get(q1, ""), "flex", s2)
        if route and route.startswith("Q"):
            q1 = int(route[1:])
        return Match(q1, s1, self.texts.get(q1, ""), "bank", s2)

    def suggestions(self, text: str, n: int = 3):
        t = norm_text(text)
        hits = process.extract(t, {i: d for i, d in enumerate(self.norm)}, scorer=fuzz.WRatio, limit=12)
        seen, out = set(), []
        for _, _, i in hits:
            qn = self.docs[i][0]
            if qn not in seen and qn in self.texts:
                seen.add(qn)
                out.append((qn, self.texts[qn]))
            if len(out) >= n:
                break
        return out


_M = None


def get_matcher() -> Matcher:
    global _M
    if _M is None:
        _M = Matcher()
    return _M


def log_miss(question: str, match: Match, handled_by: str, path=None):
    """Append to logs/miss_log.csv (gitignored). Never raises."""
    try:
        p = Path(path or MISS_LOG)
        p.parent.mkdir(parents=True, exist_ok=True)
        new = not p.exists()
        with open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "question", "best_question", "score", "handled_by"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), question, match.text, f"{match.score:.3f}", handled_by])
    except Exception:
        pass
