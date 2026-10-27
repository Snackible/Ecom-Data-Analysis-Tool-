"""Fast keyword-based intent routing. Runs before the fuzzy matcher.

An ecom manager types in shorthand ("negatives this week", "waste", "noida?", "cut 50k").
This file maps high-signal keyword patterns to a specific question handler or
flex route, so those common asks are answered without any fuzzy guessing.

Returns None when nothing fires, so the regular matcher takes over.
"""
import re

# (compiled regex, question number or 'flex')
# Order matters: the first match wins, so put the most specific patterns first.
PATTERNS = [
    # Search-query waste (the manager's most-asked bucket)
    (r"\b(negatives?|negative keywords?|neg(?:ative)? list|block list|exclude(?:d)? (?:searches|queries|keywords))\b", 31),
    (r"\b(zero gmv|zero revenue|no revenue|no gmv|got no gmv)\b.*\b(quer|search)", 23),
    (r"\b(quer|search)\w*\b.*\b(zero gmv|no gmv|no revenue)\b", 23),
    (r"\b(wasteful|wasting|waste|bleeding|burning|leaking|pointless|useless|irrelevant)\b.*\b(quer|search|term)", 24),
    (r"\b(quer|search|term)\w*\b.*\b(wasteful|wasting|waste|bleeding|burning|leaking|pointless|useless|irrelevant)\b", 24),
    (r"\b(quer|search|term)\w*\b.*\broas\b.*\b(below|under|less than)\s*1\b", 22),
    (r"\broas (below|under|less than)\s*1\b.*\b(quer|search|term)", 22),
    (r"\bhigh (ctr|click.through)\b.*\b(poor|low|weak)\s*conver", 26),
    (r"\b(convert well|high conv|strong conv)\w*\b.*\b(low|less|little) (budget|spend)\b", 27),
    (r"\bnew (quer|search|term)s?\b", 28),
    (r"\b(theme|group|cluster)\b.*\b(quer|search|term)", 29),
    (r"\b(spelling|variant|khakra|khakhra)\b.*\b(combine|merge|group)\b", 30),
    (r"\b(exact|exactmatch)\b.*\b(quer|keyword)\b.*\b(deserve|own|promote)\b", 32),

    # Match type
    (r"\b(broad)\b.*\b(vs|versus|compare)\b.*\bexact\b", 34),
    (r"\b(broad)\b.*\b(spill|spilling|spillover|leak)\b", 35),
    (r"\b(broad)\b.*\b(competitor|rival)\b.*\b(serve|search|quer)", 37),
    (r"\b(move|shift|switch)\b.*\bbroad\b.*\bexact\b", 39),
    (r"\b(move|shift|switch)\b.*\bexact\b.*\bbroad\b", 39),
    (r"\bmatch type\b.*\b(fix|change|list)\b", 44),
    (r"\b(fix|change)\b.*\bmatch type\b", 44),

    # Cost
    (r"\bcpm\b.*\bby\s+placement\b", 46),
    (r"\bplacement\b.*\bcpm\b", 46),
    (r"\bcpc\b.*\b(high|expensive|too much|overpay)", 50),
    (r"\b(highest|most expensive|costly)\b.*\bcpm\b", 47),
    (r"\bcpm\b.*\b(up|higher|increased|rose|risen)\b.*\b(this week|wow|week on week)\b", 55),
    (r"\b(lower|cut|reduce)\s+bids?\b", 57),

    # City (the manager often just names a city)
    (r"\b(city|cities)\b.*\b(tier|tiers)\b", 66),
    (r"\b(city|cities)\b.*\b(cut|hold|scale)\b", 67),
    (r"\b(high spend|heavy spend|too much spend)\b.*\b(cit|place|location)", 59),
    (r"\b(high|strong) roas\b.*\b(low|less) spend\b.*\b(cit|place|location)", 60),
    (r"\b(underfunded|under spending|under spend|deserve more budget|more budget)\b.*\b(cit|place|location)", 60),

    # SKU / product
    (r"\b(sku|product)s?\b.*\b(heavy spend|weak roas|underperform|bleeding|waste)", 69),
    (r"\b(scale|more budget|grow)\b.*\b(sku|product)s?\b.*\b(strong|high) roas\b", 70),
    (r"\b(sku|product)s?\b.*\b(cannibal|compet|overlap)", 74),

    # Attack / defend / expand
    (r"\b(attack|defend|expand)\b.*\b(split|share|budget|allocation)", 8),
    (r"\b(brand|snackible)\b.*\b(defend|defence|defense|protection)\b.*\b(rank|position|ad rank|top slot)", 84),
    (r"\b(competitor|rival|attack)\b.*\b(keep|cut|prune|remove)\b", 81),

    # Health check / recommendations
    (r"\b(one line|headline|verdict|summary)\b.*\b(instamart|period|ads|account|overall)", 12),
    (r"\b(what|which) (?:3|three)?\s*(?:things?|items?)\b.*\b(changed|change|move)\b.*\b(most|the most)\b.*\b(week|this week|wow)", 14),
    (r"\broas\s+(fall|falling|drop|dropping|down)\b", 15),
    (r"\b(attribution|lag|still collect)", 16),
    (r"\b(no sku|without sku|collection ads|auto suggest)\b.*\b(share|percent|%)", 17),
    (r"\ba2s\b", 18),
    (r"\b(wasted|bleeding|most spent least return)\b.*\bcampaign", 19),
    (r"\b(improved|rising|up this week)\b.*\bcampaign", 20),
    (r"\b(one thing|single thing|first thing|most important)\b.*\b(today|now|first|do)", 21),

    # Budget moves
    (r"\b(cut|save|trim)\b.*\b(?:₹|rs|inr|rupees?)?\s*\d[\d,]*\s*(k|lakh|lac|crore)?\b", 95),
    (r"\b(extra|add|more|put)\b.*\b(?:₹|rs|inr|rupees?)?\s*\d[\d,]*\s*(k|lakh|lac|crore)?\b.*\b(spend|budget)", 96),
    (r"\b(incremental roas|payback|low mid high|phase)\b", 98),
    (r"\b(phase|phasing|rest of month)\b", 99),
    (r"\b(pause|reduce|hold|scale)\b.*\btable\b", 100),

    # Review
    (r"\b(risk|risks)\b.*\b(recommend|follow|change)", 101),
    (r"\b(how will i know|how do i know|measure|track)\b.*\b(work|success|effect)", 102),
    (r"\b(chain|causal|cause)\b.*\broas\b.*\b(fall|drop|down)", 103),
    (r"\b(one.?off|structural|lasting)\b.*\b(dip|drop|fall)", 104),
    (r"\b(looks good|misleading|too good)", 105),
    (r"\b(least sure|uncertain|low confidence)", 106),
    (r"\b(what data|more data|extra data)\b.*\b(change|recommend)", 107),
    (r"\b(challenge|push back|argue)\b.*\b(raise|spend|budget)", 108),
    (r"\b(founder|ceo|boss)\b.*\b(update|note|lines?)", 109),
    (r"\b(principle|lesson|takeaway)\b", 110),
    (r"\b(brainstorm|think with me|think this through|grow revenue|growth plan)", 111),
]
_PATTERNS = [(re.compile(p, re.I), qn) for p, qn in PATTERNS]


def detect(text: str):
    """Return a question number for a confident keyword-pattern match, else None."""
    s = (text or "").strip().lower()
    if not s:
        return None
    for rx, qn in _PATTERNS:
        if rx.search(s):
            return qn
    return None
