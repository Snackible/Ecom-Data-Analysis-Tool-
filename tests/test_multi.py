"""Tests for compound-question splitting, dependent follow-ups and keyword intents."""
from bot import engine, intents, multi


def test_split_compound_simple():
    out = multi.split_compound("which city spends most and which skus are weak")
    assert len(out) == 2
    assert "city" in out[0].lower() and "sku" in out[1].lower()


def test_split_compound_three_parts():
    out = multi.split_compound("which city spends most; which skus are weak; and what to cut")
    assert 2 <= len(out) <= 3


def test_split_compound_leaves_singletons_alone():
    assert multi.split_compound("which city is consuming more budget") == ["which city is consuming more budget"]
    assert multi.split_compound("hi") == ["hi"]


def test_split_compound_keeps_quoted_phrases():
    out = multi.split_compound('search queries containing "healthy snacks" and their roas')
    # "and" inside the phrase scope follows "healthy snacks and their roas" -- single question
    assert all('"healthy snacks"' in p for p in out)


def test_is_why_and_expand():
    assert multi.is_why("why?") and multi.is_why("why is that") and multi.is_why("explain")
    assert multi.is_expand("break it down") and multi.is_expand("tell me more")
    assert not multi.is_why("which city spends most")


def test_metric_swap_rewrites_prev_question():
    out = multi.metric_swap("and roas?", "rank cities by spend")
    assert out and "roas" in out and "spend" not in out
    out = multi.metric_swap("what about gmv", "top queries by spend")
    assert out and "gmv" in out


def test_metric_swap_returns_none_when_not_a_swap():
    assert multi.metric_swap("which city spends most", "rank cities by spend") is None


def test_intent_negatives():
    assert intents.detect("what to add to my negative list") == 31
    assert intents.detect("this weeks negatives") == 31


def test_intent_waste_queries():
    assert intents.detect("which search queries are bleeding money") == 24
    assert intents.detect("useless searches we are paying for") == 24


def test_intent_zero_gmv():
    assert intents.detect("search queries with zero gmv") == 23


def test_intent_city_tier():
    assert intents.detect("roas by city tiers") == 66


def test_intent_misses_return_none():
    assert intents.detect("hello how are you") is None
    assert intents.detect("") is None


# ---- integration against the real engine -------------------------------------
def test_ask_many_splits_and_returns_two_results(two_months):
    rs = engine.ask_many("which city is consuming more budget and which skus are performing worst", log=False)
    assert len(rs) == 2
    assert all(r.kind in ("ok", "needs_setting", "data_not_loaded") for r in rs)


def test_why_follow_up_reruns_previous(two_months):
    first = engine.ask("which city is consuming more budget", log=False)
    assert first.meta
    second = engine.ask("why?", log=False, prev=first.meta)
    # Reusing the prev text means it answers the same question and runs the deep dive again
    assert second.kind == "ok"
    assert any("SIMILARITY" in t or "WHY" in t or "WHAT" in t for t, _ in second.sections)


def test_metric_swap_follow_up(two_months):
    first = engine.ask("rank cities by spend", log=False)
    assert first.meta
    second = engine.ask("and roas?", log=False, prev=first.meta)
    # swapped text goes down the normal pipeline; still ok, with tables
    assert second.kind == "ok" and second.tables
