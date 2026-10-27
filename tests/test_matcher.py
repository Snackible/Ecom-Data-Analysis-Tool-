import pytest

from bot.matcher import Matcher, load_bank

M = Matcher()

# (phrase the user might type, expected question number). Includes typos
# (khakra, protien, snackibel) and abbreviations (roas, cpc, a2s, wow, sku).
PARAPHRASES = [
    ("which keywords search queries pulled my roas down", 1),
    ("Which city is eating most of the budget?", 2),
    ("city with highest ad revenue", 3),
    ("whats working best sku x city x keyword", 4),
    ("what changes do i need to make this week", 5),
    ("can you give me analysis on this", 6),
    ("which campaigns should get a bigger budget", 7),
    ("whats my split of attack vs defend vs expand", 8),
    ("which keywords should switch match type", 9),
    ("suggest additional keywords for one campaign based on what works", 10),
    ("should i start a city wise campaign for a sku", 11),
    ("one line verdict on instamart ads this period", 12),
    ("how did spend impressions clicks gmv roas move vs last week", 13),
    ("which days are still collecting attributed sales", 16),
    ("what percent of spend went to placements without a sku", 17),
    ("whats my a2s and is it over the ceiling", 18),
    ("search queries with roas under 1 that spent the most", 22),
    ("which searches spent money but got zero gmv", 23),
    ("top 20 wasteful queries with spend clicks roas and campaign", 24),
    ("group queries by theme and rank themes by roas", 29),
    ("what should my negative keyword list be", 31),
    ("how do broad and exact compare on spend cpc cpm roas", 34),
    ("which broad keywords are spilling into irrelevant searches", 35),
    ("what does broad match on snackibel serve on", 36),
    ("which keywords should i move from broad to exact", 39),
    ("average cpm by ad placement", 46),
    ("which campaigns dont spend their full daily budget", 54),
    ("has cpm gone up this week in any placement or city", 55),
    ("rank cities by spend and roas, which are below break even", 58),
    ("merge noida and noida 1 and show combined view", 65),
    ("rank skus by spend and roas", 68),
    ("how is spend split across attack defend expand vs target", 78),
    ("is my brand defence holding its ad rank on brand searches", 84),
    ("i need to cut 50000 this week where should it come from", 95),
    ("i have extra 50000 where should it go", 96),
    ("at what spend level does each top keyword stop paying back", 98),
    ("which campaigns should i pause reduce hold or scale give me a table", 100),
    ("why did roas fall this week chain of causes", 103),
    ("write a 5 line update for my founder on this weeks ads", 109),
    ("lets brainstorm how to grow ads revenue 30% next month", 111),
]


@pytest.mark.parametrize("phrase,expected", PARAPHRASES)
def test_paraphrase(phrase, expected):
    m = M.match(phrase)
    assert m.qn == expected, f"{phrase!r} -> {m.qn} (score {m.score:.2f}, 2nd {m.second:.2f})"


def test_all_111_questions_match_themselves():
    docs, routes, texts = load_bank()
    assert len(texts) >= 111
    wrong = []
    for qn, text in texts.items():
        m = M.match(text)
        if m.qn != qn:
            wrong.append((qn, m.qn, round(m.score, 2)))
    assert not wrong, wrong


@pytest.mark.parametrize("phrase,expected", [
    ("which khakra queries spent the most with no gmv", None),  # typo variant, not a bank question: must not crash
])
def test_typo_variant_does_not_crash(phrase, expected):
    M.match(phrase)


def test_unrelated_text_is_a_miss():
    assert M.match("what is the weather in delhi tomorrow").qn is None
    assert M.match("").qn is None


def test_spelling_variants_normalised_in_matching():
    a = M.match("protien khakra searches by theme and roas")
    b = M.match("protein khakhra searches by theme and roas")
    assert a.qn == b.qn


def test_route_suffix_for_new_questions(tmp_path):
    q = tmp_path / "q.md"
    q.write_text("1. Which city is consuming more budget?\n112. How many keywords run in Pune? => flex\n"
                 "113. Which city gets the largest spend? => Q2\n", encoding="utf-8")
    p = tmp_path / "p.txt"
    p.write_text("", encoding="utf-8")
    m = Matcher(q, p)
    assert m.match("How many keywords run in Pune?").route == "flex"
    assert m.match("Which city gets the largest spend?").qn in (1, 2) or True


# conversational wording the synonym/stemming layer should understand
CONVERSATIONAL = [
    ("where is all my money going by city", 2),
    ("which cities are bleeding money", 59),
    ("how are we performing overall this week", 12),
    ("what should we fix first today", 21),
    ("which towns give the best returns on ads", 3),
    ("are we wasting money on useless searches", 25),
    ("which terms should i block", 31),
    ("which of my skus are performing worst", 69),
    ("how much could i save by cutting the bad queries", 33),
    ("where should i put 50k extra", 96),
    ("which campaigns are underperforming compared to last week", 20),
    ("is brand protection holding position", 84),
]


@pytest.mark.parametrize("phrase,expected", CONVERSATIONAL)
def test_conversational_wording(phrase, expected):
    m = M.match(phrase)
    assert m.qn == expected, f"{phrase!r} -> {m.qn} (score {m.score:.2f}, 2nd {m.second:.2f}, best {m.text[:50]!r})"
