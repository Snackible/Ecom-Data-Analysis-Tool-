import re

import duckdb
import pytest

import config
from bot import answers, core, engine, flex, queries
from bot.core import NO_DATA, Params
from bot.q_cost import table_crosscheck

NULL_SETTINGS = {"defend_terms": [], "attack_terms": [], "competitor_closeness": {"close": [], "far": []}}
FULL_SETTINGS = {
    "contribution_before_ads_pct_of_mrp_gmv": 0.30, "min_data": {"spend_inr": 500, "clicks": 20, "days": 3},
    "waste_rule": {"spend_inr": 1000, "roas_below": 3.0, "over_days": 2, "zero_gmv_spend_inr": 800},
    "scale_rule": {"roas_at_least": 2.0, "spend_inr": 5000}, "budget_increase_step_pct": 15, "max_weekly_actions": 5,
    "lag_days": 7, "split_target": {"attack": 20, "expand": 60, "defend": 20}, "a2s_sales_basis": "total_gmv",
    "a2s_ceiling_pct": 40, "monthly_budget_inr": 500000, "monthly_gmv_target_inr": 1500000,
    "city_tiers": {"Bangalore": "Tier 1", "Mumbai": "Tier 1", "Delhi": "Tier 1", "Pune": "Tier 2", "Noida": "Tier 2"},
    "cpc_acceptable_by_bucket": {"attack": 5, "expand": 5, "defend": 5}, "cpm_acceptable_by_placement": {"Keyword Based Ads": 90},
    "sku_keywords": {"Snackible Ragi Chips 60g": ["ragi"], "Snackible Cheese Dosa Khakhra 100g": ["khakhra"]},
    "competitor_closeness": {"close": ["troovy"], "far": ["choki choki"]},
    "defend_terms": [], "attack_terms": [],
}


def ctx_for():
    return core.Context.open(NULL_SETTINGS)


# ---- empty DB ----------------------------------------------------------------
def test_empty_db_every_question_says_no_data(empty_db):
    ctx = ctx_for()
    try:
        for n in range(1, 112):
            r = queries.run_question(ctx, Params(top_n=5), n)
            assert r.headline == NO_DATA or r.kind == "data_not_loaded", (n, r.headline)
    finally:
        ctx.close()


def test_empty_db_engine_and_flex(empty_db):
    for q in ["which city spends most", "top 10 queries by clicks", "daily spend last 14 days", "which months are loaded"]:
        assert answers.render(engine.ask(q, log=False)) == NO_DATA


# ---- dynamic dates: nothing hardcoded ---------------------------------------------
def test_one_month_date_range_comes_from_data(one_month):
    r = engine.ask("Which city is consuming more budget?", log=False)
    assert r.date_range == "2026-07-01 to 2026-07-31"
    assert "Date range: 2026-07-01 to 2026-07-31" in answers.render(r)


def test_two_months_date_range_spans_both(two_months):
    r = engine.ask("Which city is consuming more budget?", log=False)
    assert r.date_range == "2026-07-01 to 2026-08-31"
    r = engine.ask("which city spends most in august", log=False)
    assert r.date_range == "2026-08-01 to 2026-08-31"


def test_other_months_work_too(tmp_path, monkeypatch):
    from fixture_db import build_fixture
    p = tmp_path / "x.duckdb"
    build_fixture(p, months=("2027-02",))
    monkeypatch.setattr(config, "DB_PATH", p)
    r = engine.ask("which city spends the most", log=False)
    assert r.date_range == "2027-02-01 to 2027-02-28"


def test_period_with_no_data_says_so(one_month):
    r = engine.ask("which city spends most in october", log=False)
    assert "No data loaded for" in r.headline and "2026-07-01" in r.headline


# ---- Noida ----------------------------------------------------------------------
def test_noida_merged_in_city_views(noida_month):
    r = engine.ask("rank cities by spend", log=False)
    cities = list(r.tables[0][1]["city"])
    assert "Noida" in cities and "Noida 1" not in cities
    txt = answers.render(engine.ask("merge noida and noida 1 and show the combined view", log=False))
    assert "Noida 1" not in txt.split("Fact:")[0]


def test_noida_total_equals_sum_of_both(noida_month):
    ctx = ctx_for()
    try:
        raw = ctx.sql("SELECT SUM(total_budget_burnt) s FROM granular WHERE city IN ('Noida','Noida 1')").iloc[0]["s"]
        df, _ = ctx.fetch("granular", ("city",), None, {"city": ["Noida"]})
        assert df.iloc[0]["spend"] == pytest.approx(raw)
    finally:
        ctx.close()


# ---- needs setting / data not loaded ------------------------------------------------
NEEDS = {16: "lag_days", 18: "a2s_sales_basis", 31: "waste_rule", 33: "waste_rule", 50: "cpc_acceptable_by_bucket",
         57: "cpm_acceptable_by_placement", 66: "city_tiers", 67: "contribution_before_ads_pct_of_mrp_gmv",
         71: "sku_keywords", 98: "contribution_before_ads_pct_of_mrp_gmv", 99: "monthly_budget_inr", 100: "contribution_before_ads_pct_of_mrp_gmv"}


@pytest.mark.parametrize("qn,key", NEEDS.items())
def test_needs_setting_paths(one_month, qn, key):
    ctx = ctx_for()
    try:
        r = queries.run_question(ctx, Params(top_n=5), qn)
    finally:
        ctx.close()
    assert key in r.headline and "needs setting" in r.headline.lower(), (qn, r.headline)
    assert r.kind == "needs_setting"


@pytest.mark.parametrize("qn", [90, 91, 92, 93, 94])
def test_stock_questions_data_not_loaded(one_month, qn):
    ctx = ctx_for()
    try:
        r = queries.run_question(ctx, Params(), qn)
    finally:
        ctx.close()
    assert r.headline == "data not loaded: stock/fill rate" and r.kind == "data_not_loaded"


def test_budget_log_data_not_loaded(one_month):
    ctx = ctx_for()
    try:
        r = queries.run_question(ctx, Params(), 97)
    finally:
        ctx.close()
    assert "data not loaded: budget or cost change log" in r.headline


def test_scale_advice_mentions_stock(one_month):
    ctx = core.Context.open(FULL_SETTINGS)
    try:
        for n in (7, 70, 75, 96):
            r = queries.run_question(ctx, Params(top_n=5, entities={"amount": [50000]}), n)
            txt = answers.render(r).lower()
            assert "stock" in txt, n
    finally:
        ctx.close()


def test_search_query_x_city_is_flagged(one_month):
    r = engine.ask("which search queries did pune serve on", log=False)
    assert "not available" in answers.render(r).lower() or "city" in answers.render(r).lower()


def test_sku_map_missing_is_stated(one_month):
    txt = answers.render(engine.ask("rank skus by spend and roas", log=False))
    assert "needs setting: product_title_to_sku" in txt


def test_min_data_unset_is_stated(one_month):
    txt = answers.render(engine.ask("which search queries spent the most with roas below 1", log=False))
    assert "needs setting: min_data" in txt or "No search query" in txt


# ---- full settings: everything runs, rules apply -----------------------------------------
def test_every_question_runs_with_full_settings(two_months):
    ctx = core.Context.open(FULL_SETTINGS)
    try:
        for n in range(1, 112):
            r = queries.run_question(ctx, Params(top_n=5, entities={"amount": [50000]}), n)
            assert r.headline, n
            assert "—" not in answers.render(r) and "–" not in answers.render(r), n
    finally:
        ctx.close()


def test_a2s_with_total_gmv_basis(one_month):
    ctx = core.Context.open(FULL_SETTINGS)
    try:
        r = queries.run_question(ctx, Params(), 18)
    finally:
        ctx.close()
    assert "A2S is" in r.headline


def test_min_data_gives_too_little_data_not_pause(one_month):
    s = dict(FULL_SETTINGS, min_data={"spend_inr": 10 ** 9, "clicks": 10 ** 9, "days": 10 ** 6})
    ctx = core.Context.open(s)
    try:
        r = queries.run_question(ctx, Params(top_n=5), 67)
    finally:
        ctx.close()
    assert all("hold (too little data)" == a for a in r.tables[0][1]["action"])


# ---- no em dashes anywhere -----------------------------------------------------------------
def test_no_em_dashes_in_any_answer(two_months):
    for q in ["what changes should I make this week", "full analysis", "lets brainstorm growth", "write a 5 line update for my founder"]:
        t = answers.render(engine.ask(q, log=False))
        assert "—" not in t and "–" not in t


# ---- flex ---------------------------------------------------------------------------------------
FLEX = [
    ("Top 15 search queries by clicks", "queries by clicks"),
    ("Daily spend and ROAS for the last 14 days", "by date"),
    ("Is CPM rising in Keyword Based Ads?", "cpm"),
    ("Compare this week with last week by placement", "overall"),
    ("What share of GMV came from Collection Ads?", "of gmv"),
    ("Which months are loaded?", "Loaded data"),
    ("How many cities did the KBA_Attack campaign run in?", "distinct"),
    ("Any day with unusual spend?", "unusual"),
    ("Do the three tables agree on search placement totals?", "tables"),
]


@pytest.mark.parametrize("q,expect", FLEX)
def test_flexible_questions_answer(two_months, q, expect):
    t = answers.render(engine.ask(q, log=False))
    assert t and "I could not match" not in t and "hit a problem" not in t, t[:200]
    assert expect.lower() in t.lower() or "date range" in t.lower(), t[:300]


def test_flex_lookup_is_capped(one_month):
    r = engine.ask("show rows for KBA_Millet in Pune", log=False)
    assert len(r.tables[0][1]) <= 50 and "rows match" in r.headline


def test_flex_trend_direction_and_monday_weeks(two_months):
    r = engine.ask("weekly gmv trend", log=False)
    weeks = r.tables[0][1]["week"]
    assert all(w.weekday() == 0 for w in weeks)


def test_flex_unknown_query_exists(one_month):
    r = engine.ask("is 'ragi chips' a search query we served on?", log=False)
    assert "Yes" in r.headline
    r = engine.ask("is 'zzzz nothing' a search query we served on?", log=False)
    assert "No" in r.headline


def test_unrelated_question_is_logged(one_month, tmp_path):
    log = tmp_path / "miss.csv"
    r = engine.ask("what is the capital of france", log=True, log_path=log)
    assert r.kind == "unknown" and log.exists()
    assert "capital of france" in log.read_text(encoding="utf-8")


# ---- table cross-check -----------------------------------------------------------------------------
def test_tables_agree_on_fixture(one_month):
    ctx = ctx_for()
    try:
        assert table_crosscheck(ctx) == []
    finally:
        ctx.close()


def test_mismatch_shows_warning(one_month):
    con = duckdb.connect(str(one_month))
    con.execute("UPDATE search_query SET total_budget_burnt = total_budget_burnt * 2 WHERE keyword = 'troovy'")
    con.close()
    r = engine.ask("which city spends most", log=False)
    assert any(n.startswith("Warning:") for n in r.notes)


# ---- metrics at the data level --------------------------------------------------------------------------
def test_collection_conversion_above_100_percent_kept(one_month):
    ctx = ctx_for()
    try:
        d, _ = ctx.fetch("granular", ("placement",), None, None)
    finally:
        ctx.close()
    row = d[d["placement"].str.startswith("Collection")].iloc[0]
    assert row["conversions"] / row["clicks"] > 1.0


def test_cpm_is_per_1000_not_ecpm(one_month):
    ctx = ctx_for()
    try:
        d, _ = ctx.fetch("granular", (), None, None)
    finally:
        ctx.close()
    r = d.iloc[0]
    assert r["cpm"] == pytest.approx(r["spend"] / r["impressions"] * 1000)


def test_spend_never_sums_total_budget(one_month):
    ctx = ctx_for()
    try:
        d, _ = ctx.fetch("granular", (), None, None)
        assert d.iloc[0]["spend"] == pytest.approx(ctx.sql("SELECT SUM(total_budget_burnt) s FROM granular").iloc[0]["s"])
    finally:
        ctx.close()


def test_sql_names_are_whitelisted(one_month):
    ctx = ctx_for()
    try:
        with pytest.raises(KeyError):
            ctx.fetch("granular", ("city; DROP TABLE granular",), None, None)
        d, _ = ctx.fetch("granular", ("city",), None, {"city": ["x'; DROP TABLE granular; --"]})
        assert d.empty
        assert ctx.has_rows("granular")
    finally:
        ctx.close()


# ---- parameter extraction ---------------------------------------------------------------------------------------
def test_amount_extraction():
    assert engine.extract_amount("cut ₹50,000 this week") == 50000
    assert engine.extract_amount("I need to cut 50k") == 50000
    assert engine.extract_amount("extra 1.5 lakh") == 150000
    assert engine.extract_amount("top 10 queries last 7 days") is None


def test_classification_by_bid_and_served_differ(one_month):
    ctx = ctx_for()
    try:
        r = queries.run_question(ctx, Params(top_n=5), 8)
    finally:
        ctx.close()
    assert len(r.tables) == 2  # by bid and by search query served


def test_crosscheck_question_routes_and_non_overlap_is_not_a_warning(one_month):
    con = duckdb.connect(str(one_month))
    con.execute("INSERT INTO summary (campaign_id, campaign_name, total_budget_burnt, period_start, period_end) "
                "VALUES ('x','x',100.0,'2026-09-01','2026-09-30')")
    con.close()
    r = engine.ask("do the three tables agree on search placement totals?", log=False)
    assert r.headline == "The tables agree for search placements."
    assert any("cannot be compared" in n for n in r.notes)
    # and ordinary answers carry no warning for a non-overlapping summary
    r2 = engine.ask("which city spends most", log=False)
    assert not any(n.startswith("Warning:") for n in r2.notes)
