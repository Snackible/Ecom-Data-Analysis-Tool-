import pandas as pd

from bot import answers, engine, narrate
from bot.core import Result


def _df():
    return pd.DataFrame({
        "query": ["snacks", "chips", "superyou", "diet coke"],
        "spend": [78772.0, 71807.0, 40783.0, 23963.0], "gmv": [48976.0, 27269.0, 13250.0, 3502.0],
        "clicks": [3818, 1657, 1440, 407], "roas": [0.62, 0.38, 0.32, 0.15],
    })


def test_table_becomes_sentences_with_figures():
    t = narrate.describe_table("Queries", _df())
    assert "**snacks**" in t and "₹78,772" in t and "ROAS 0.62" in t
    assert "blended ROAS" in t           # totals recomputed from the rows
    assert "below 1.00" in t              # spread sentence
    assert "|" not in t                   # prose, not a table


def test_notes_are_grouped_and_labels_kept():
    out = "\n".join(narrate.narrate_notes([
        "Action: add negatives", "Fact: ROAS is at MRP", "needs setting: min_data (why)", "data not loaded: stock/fill rate",
        "Warning: tables differ"]))
    assert "What I'd do:" in out and "Fact: ROAS is at MRP" in out
    assert "needs setting: min_data" in out and "data not loaded: stock/fill rate" in out
    assert out.index("Warning") < out.index("What I'd do")


def test_time_series_and_compare_tables():
    ts = pd.DataFrame({"date": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                       "spend": [100.0, 200.0, 150.0], "gmv": [300.0, 400.0, 450.0]})
    t = narrate.describe_table("x", ts)
    assert "peaking on 2026-07-02" in t and "over 3 days" in t
    cmp_ = pd.DataFrame({"metric": ["spend", "roas"], "current": [200.0, 2.0], "previous": [100.0, 2.5], "change": [1.0, -0.2]})
    t = narrate.describe_table("x", cmp_)
    assert "up 100.0%" in t and "down 20.0%" in t


def test_answer_leads_with_prose_then_numbers(two_months):
    txt = answers.render(engine.ask("Which city is consuming more budget?", log=False))
    prose, sep, tables = txt.partition("Supporting numbers")
    assert sep and "|" in tables and "|" not in prose
    assert prose.index("Date range") < prose.index("takes the most budget")
    assert "At the top is" in prose


def test_no_em_dashes_in_prose(two_months):
    for q in ["what changes should I make this week", "how did spend change vs last week", "weekly gmv trend"]:
        p, t = answers.render_parts(engine.ask(q, log=False))
        assert "—" not in p + t and "–" not in p + t


def test_follow_up_changes_only_what_is_named(two_months):
    first = engine.ask("rank cities by spend", log=False)
    assert first.meta and first.meta["qn"] == 58
    second = engine.ask("and for top 3 instead", log=False, prev=first.meta)
    assert any("follow-up" in n for n in second.notes)
    assert second.tables and len(second.tables[0][1]) <= 3
    third = engine.ask("what about august", log=False, prev=first.meta)
    assert third.date_range == "2026-08-01 to 2026-08-31"


def test_follow_up_with_entity(two_months):
    first = engine.ask("which search queries spent the most", log=False)
    second = engine.ask("and for the KBA_Attack campaign?", log=False, prev=first.meta)
    assert any("follow-up" in n for n in second.notes)
    assert second.kind == "ok"


def test_unrelated_message_without_context_is_not_a_follow_up(two_months):
    r = engine.ask("and what is the capital of france", log=False, prev=None)
    assert r.kind == "unknown"


def test_prose_for_empty_and_gap_results():
    r = Result(headline="data not loaded: stock/fill rate", kind="data_not_loaded", notes=["Fact: x"])
    assert "data not loaded: stock/fill rate" in narrate.narrate(r)


def test_named_city_gets_its_own_headline(two_months):
    r = engine.ask("how much budget is Pune consuming", log=False)
    assert r.headline.startswith("Pune is number")
    assert "of 4 cities by spend" in r.headline


def test_manager_read_flags_leak_and_winners():
    df = pd.DataFrame({"query": list("abcdef"), "spend": [900.0, 800.0, 100.0, 90.0, 80.0, 70.0],
                       "gmv": [300.0, 400.0, 600.0, 500.0, 400.0, 350.0], "roas": [0.33, 0.5, 6.0, 5.56, 5.0, 5.0]})
    out = "\n".join(narrate.manager_read(Result(tables=[("t", df)])))
    assert "2 of 6 rows" in out and "Action: start the clean-up with **a**" in out
    assert "candidates for more budget" in out and "stock and fill rate" in out


def test_manager_read_names_the_driver_and_halves():
    cmp_ = pd.DataFrame({"metric": ["roas", "cpm", "ctr"], "current": [2.0, 150.0, .02], "previous": [2.5, 100.0, .021], "change": [-0.2, 0.5, -0.05]})
    assert "biggest mover" in "\n".join(narrate.manager_read(Result(tables=[("t", cmp_)]))) and "CPM" in "\n".join(narrate.manager_read(Result(tables=[("t", cmp_)])))
    ts = pd.DataFrame({"date": pd.date_range("2026-07-01", periods=8), "spend": [100.0] * 4 + [200.0] * 4, "gmv": [300.0] * 4 + [400.0] * 4})
    assert "diminishing returns" in "\n".join(narrate.manager_read(Result(tables=[("t", ts)])))
