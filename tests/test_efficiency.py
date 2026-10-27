"""Tests for the efficiency add-ons: fetch memo, entity shorthand, alerts,
compound summary, SKU auto-learn and settings save/load."""
import pandas as pd

from bot import alerts, engine, multi, settings as settings_mod, sku_learn
from bot.core import Context
from bot.params import Catalog, extract_entities


# ---- fetch memo --------------------------------------------------------------
def test_fetch_memo_hits_cache_on_second_call(two_months):
    ctx = Context.open()
    try:
        a, _ = ctx.fetch("granular", ("city",))
        b, _ = ctx.fetch("granular", ("city",))
        # equal frames; the cache returns a copy so independent writes stay safe
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
        assert ctx._fetch_cache  # was populated
    finally:
        ctx.close()


# ---- entity shorthand --------------------------------------------------------
def test_shorthand_prefix_matches_campaign():
    cat = Catalog(campaigns=["KBA_Attack_Feb26", "KBA_Defend_Feb26", "AutoSuggest_Mar26"])
    ents = extract_entities("how is KBA doing", cat)
    assert "KBA_Attack_Feb26" in ents["campaigns"] and "KBA_Defend_Feb26" in ents["campaigns"]
    assert "AutoSuggest_Mar26" not in ents["campaigns"]


def test_shorthand_substring_matches_product():
    cat = Catalog(products=["Snackible Ragi Chips 60g", "Snackible Protein Puffs 100g"])
    ents = extract_entities("show me ragi", cat)
    assert "Snackible Ragi Chips 60g" in ents["products"]


def test_shorthand_does_not_fire_when_full_name_matches():
    cat = Catalog(campaigns=["KBA_Attack_Feb26", "KBA_Defend_Feb26"])
    ents = extract_entities("tell me about KBA_Attack_Feb26", cat)
    assert ents["campaigns"] == ["KBA_Attack_Feb26"]


# ---- compound summary --------------------------------------------------------
def test_compound_summary_rolls_up_headlines(two_months):
    rs = engine.ask_many("rank cities by spend; and rank skus by spend", log=False)
    out = multi.compound_summary(rs)
    assert len(rs) >= 2 and out.startswith("Across your questions")


def test_compound_summary_empty_for_single_result():
    class R:
        headline = "x"
    assert multi.compound_summary([R()]) == ""


# ---- alerts ------------------------------------------------------------------
def test_alerts_is_a_list(two_months):
    ctx = Context.open()
    try:
        out = alerts.build(ctx)
        assert isinstance(out, list)
        for level, msg in out:
            assert level in ("warn", "info") and msg
    finally:
        ctx.close()


# ---- SKU auto-learn ----------------------------------------------------------
def test_short_name_strips_brand_and_weight():
    assert sku_learn.short_name("Snackible Ragi Chips 60g") == "Ragi Chips"
    assert sku_learn.short_name("Snackible Cheese Dosa Khakhra 100g, Roasted") == "Cheese Dosa Khakhra"
    assert sku_learn.short_name("") == ""


def test_propose_skips_titles_already_mapped():
    titles = ["Snackible Ragi Chips 60g", "Snackible Protein Puffs 100g"]
    out = sku_learn.propose(titles, existing={"Snackible Ragi Chips 60g": "Ragi"})
    assert len(out) == 1 and out[0][0] == "Snackible Protein Puffs 100g"


# ---- settings save/load round trip ------------------------------------------
def test_settings_save_round_trip(tmp_path):
    p = tmp_path / "settings.yaml"
    settings_mod.save({"foo": {"bar": 1}, "baz": [1, 2, 3]}, path=p)
    loaded = settings_mod.load(p)
    assert loaded["foo"]["bar"] == 1 and loaded["baz"] == [1, 2, 3]
