from datetime import date, datetime

import pytest

from bot import classify, metrics, normalise, params, settings


# ---- metrics ---------------------------------------------------------------
def test_roas_ctr_cpc_cpm():
    assert metrics.roas(300, 100) == 3
    assert metrics.ctr(5, 100) == 0.05
    assert metrics.cpc(100, 20) == 5
    assert metrics.cpm(50, 1000) == 50  # per 1,000 impressions
    assert metrics.cpm(50, 2000) == 25


def test_zero_denominators_are_none():
    assert metrics.roas(10, 0) is None
    assert metrics.ctr(1, 0) is None
    assert metrics.cpc(10, 0) is None
    assert metrics.cpm(10, 0) is None
    assert metrics.growth(5, 0) is None
    assert metrics.growth(None, 5) is None


def test_na_is_missing_not_zero():
    assert metrics.roas(float("nan"), 10) is None
    assert metrics.roas(None, 10) is None


def test_conversion_over_100_percent_is_kept():
    assert metrics.conv_rate(9, 5) == pytest.approx(1.8)


def test_marginal_roas_and_break_even():
    assert metrics.marginal_roas(100, 50, 160, 70) == 3
    assert metrics.marginal_roas(100, 50, 160, 50) is None
    assert metrics.break_even_roas(0.25) == 4
    assert metrics.break_even_roas(None) is None


def test_add_ratios_recomputes_from_totals():
    import pandas as pd
    df = pd.DataFrame({"spend": [100, 300], "impressions": [1000, 1000], "clicks": [10, 0],
                       "a2c": [1, 0], "gmv": [200, 300], "conversions": [2, 0]})
    out = metrics.add_ratios(df)
    assert out.loc[0, "roas"] == 2
    assert out.loc[1, "cpc"] is None


# ---- normalise -------------------------------------------------------------
def test_noida_merge():
    assert normalise.norm_city("Noida 1") == "Noida"
    assert normalise.norm_city(" noida ") == "Noida"
    assert normalise.norm_city("Pune") == "Pune"


def test_other_duplicate_cities_flagged_not_merged():
    d = normalise.duplicate_city_candidates(["Pune", "pune ", "Noida", "Noida 1", "Delhi", "Delhi 2"])
    flat = [sorted(x) for x in d]
    assert ["Delhi", "Delhi 2"] in flat and any("Pune" in g for g in flat)
    assert not any("Noida" in g for g in flat)
    assert normalise.norm_city("Delhi 2") == "Delhi 2"


def test_query_and_theme_normalisation():
    assert normalise.norm_query("  Ragi   CHIPS ") == "ragi chips"
    assert normalise.theme_key("khakra") == normalise.theme_key("kakra") == "khakhra"
    assert normalise.theme_key("Protien bar") == "protein bar"
    assert normalise.norm_query("khakra") != normalise.norm_query("khakhra")  # query-level stays separate


def test_norm_text_abbreviations():
    assert "week on week" in normalise.norm_text("ROAS WoW?")
    assert normalise.norm_text("snackibel") == "snackible"


def test_clean_enum_values():
    assert normalise.clean_match_type("KEYWORD_MATCH_TYPE_BROAD") == "Broad"
    assert normalise.clean_bidding("BIDDING_STRATEGY_TYPE_CPM") == "CPM"


def test_short_sku():
    assert normalise.short_sku("x" * 60, None) == "x" * 39 + "~"
    assert normalise.short_sku("Long Title", {"long title": "Short"}) == "Short"
    assert normalise.short_sku(None, None) == "(no product)"


# ---- classify --------------------------------------------------------------
def test_buckets():
    c = classify.Classifier()
    assert c.bucket("snackible ragi chips") == "defend"
    assert c.bucket("Snackibel") == "defend"
    assert c.bucket("dipsters") == "defend"
    assert c.bucket("choki choki") == "attack"
    assert c.bucket("Let’s Try chips") == "attack"
    assert c.bucket("lets try") == "attack"
    assert c.bucket("ragi chips") == "expand"
    assert c.bucket("") == "expand"


def test_bucket_word_boundaries():
    c = classify.Classifier()
    assert c.bucket("player") == "expand"  # contains 'lay' only
    assert c.bucket("flays") == "expand"
    assert c.bucket("lays chips") == "attack"


def test_settings_terms_extend_builtin():
    c = classify.Classifier(extra_attack=["acme"], extra_defend=["mybrand"])
    assert c.bucket("acme snacks") == "attack"
    assert c.bucket("mybrand") == "defend"


def test_contains_keyword():
    assert classify.contains_keyword("banana chips", "chips")
    assert not classify.contains_keyword("wafers", "choki choki")


# ---- settings --------------------------------------------------------------
def test_settings_default_all_null():
    s = settings.load()
    assert settings.get(s, "lag_days") is None
    assert settings.get(s, "waste_rule.spend_inr") is None
    with pytest.raises(settings.NeedsSetting) as e:
        settings.get(s, "lag_days", required=True)
    assert e.value.message() == "needs setting: lag_days"


# ---- params ----------------------------------------------------------------
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=params.IST)  # a Wednesday


def test_week_starts_monday():
    assert params.week_start(date(2026, 10, 21)) == date(2026, 10, 19)
    assert params.week_start(date(2026, 10, 25)) == date(2026, 10, 19)


def test_last_week_and_this_week():
    r = params.parse_dates("spend last week", max_date=date(2026, 12, 31), now=NOW)
    p = r["periods"][0]
    assert (p.start, p.end) == (date(2026, 10, 12), date(2026, 10, 18))
    r = params.parse_dates("spend this week", max_date=date(2026, 12, 31), now=NOW)
    assert r["periods"][0].start == date(2026, 10, 19)


def test_relative_dates_anchor_to_latest_data_when_stale():
    r = params.parse_dates("last 7 days", max_date=date(2026, 7, 31), now=NOW)
    p = r["periods"][0]
    assert p.end == date(2026, 7, 31) and p.start == date(2026, 7, 25)
    assert "latest loaded date" in p.note


def test_named_months_and_compare():
    r = params.parse_dates("compare august with july", max_date=date(2026, 8, 31), now=NOW)
    assert [p.start for p in r["periods"]] == [date(2026, 8, 1), date(2026, 7, 1)]
    assert r["periods"][0].end == date(2026, 8, 31)
    assert r["compare"]


def test_explicit_dates():
    r = params.parse_dates("rows on 3 sept", max_date=date(2026, 9, 30), now=NOW)
    assert r["periods"][0].start == date(2026, 9, 3)
    r = params.parse_dates("from 2026-09-01 to 2026-09-07", now=NOW)
    assert (r["periods"][0].start, r["periods"][0].end) == (date(2026, 9, 1), date(2026, 9, 7))


def test_previous_period():
    p = params.Period(date(2026, 9, 8), date(2026, 9, 14), "x")
    q = params.previous_period(p)
    assert (q.start, q.end) == (date(2026, 9, 1), date(2026, 9, 7))


def test_top_n():
    assert params.extract_top_n("top 15 queries") == 15
    assert params.extract_top_n("10 lowest ctr") == 10
    assert params.extract_top_n("show queries") == 10


def test_entity_extraction_with_typos():
    cat = params.Catalog(cities=["Bangalore", "Mumbai", "Pune"], campaigns=["KBA_Attack", "Collection_IM"],
                         keywords=["ragi chips", "troovy"], queries={"ragi chips"})
    e = params.extract_entities("how is pune doing in the kba attack campaign", cat)
    assert e["cities"] == ["Pune"] and "KBA_Attack" in e["campaigns"]
    e = params.extract_entities("mumbay spend", cat)
    assert e["cities"] == ["Mumbai"]
    e = params.extract_entities("which search queries did 'ragi chips' serve on", cat)
    assert e["queries"] == ["ragi chips"]


def test_detect_file_type_falls_back_to_header(tmp_path):
    import ingest
    hdr = ",".join(ingest.SEARCH_QUERY_COLUMNS)
    f = tmp_path / "aug search_12325_1793268134870.csv"
    f.write_text("a\n" * 6 + hdr + "\n", encoding="utf-8")
    assert ingest.detect_file_type(f) == "search_query"
    g = tmp_path / "aug stuff.csv"
    g.write_text("a\n" * 6 + ",".join(ingest.GRANULAR_COLUMNS) + "\n", encoding="utf-8")
    assert ingest.detect_file_type(g) == "granular"
    s = tmp_path / "aug totals.csv"
    s.write_text("a\n" * 6 + ",".join(ingest.SUMMARY_COLUMNS) + "\n", encoding="utf-8")
    assert ingest.detect_file_type(s) == "summary"
    junk = tmp_path / "notes.csv"
    junk.write_text("hello,world\n", encoding="utf-8")
    import pytest
    with pytest.raises(ingest.SchemaError):
        ingest.detect_file_type(junk)


def test_extract_zip_keeps_only_exports_and_flat_names(tmp_path):
    import zipfile, pytest, ingest
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a/b/one.csv", "h\n1\n")
        zf.writestr("../evil.csv", "h\n2\n")
        zf.writestr("notes.txt", "no")
        zf.writestr("__MACOSX/._one.csv", "x")
    out_dir = tmp_path / "out"; out_dir.mkdir()
    got = ingest.extract_zip(z, out_dir)
    assert sorted(p.name for p in got) == ["evil.csv", "one.csv"]
    assert all(p.parent == out_dir for p in got) and not (tmp_path / "evil.csv").exists()
    empty = tmp_path / "e.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("n.txt", "x")
    with pytest.raises(ingest.SchemaError):
        ingest.extract_zip(empty, out_dir)
    bad = tmp_path / "b.zip"; bad.write_bytes(b"nope")
    with pytest.raises(ingest.SchemaError):
        ingest.extract_zip(bad, out_dir)
