import pytest

from bot import mcp_tools


def test_run_sql_selects_and_caps(two_months):
    out = mcp_tools.run_sql("SELECT city, sum(total_budget_burnt) AS spend FROM granular GROUP BY 1 ORDER BY 2 DESC")
    assert out.splitlines()[0] == "city,spend" and len(out.splitlines()) > 1


@pytest.mark.parametrize("bad", [
    "DROP TABLE granular", "SELECT 1; DROP TABLE granular", "COPY granular TO 'x.csv'",
    "SELECT * FROM read_csv('x.csv')", "ATTACH 'a.db'", "", "PRAGMA database_list", "UPDATE granular SET city='x'",
])
def test_run_sql_refuses_writes_and_file_access(two_months, bad):
    out = mcp_tools.run_sql(bad)
    assert "Only plain SELECT" in out or "exactly one" in out


def test_run_sql_cannot_write_even_if_filter_missed(two_months):
    assert "failed" in mcp_tools.run_sql("SELECT * FROM granular WHERE 1=(SELECT count(*) FROM glob('*'))").lower() \
        or "Only plain SELECT" in mcp_tools.run_sql("SELECT * FROM granular WHERE 1=(SELECT count(*) FROM glob('*'))")


def test_describe_data_lists_tables(two_months):
    out = mcp_tools.describe_data()
    assert "granular:" in out and "rows" in out and "total_budget_burnt" in out


def test_compact_answer_is_short_and_dated(two_months):
    text, meta = mcp_tools.compact_answer("which city is consuming more budget")
    assert "Date range" in text and len(text) <= mcp_tools.MAX_CHARS + 60 and meta
    assert "—" not in text
