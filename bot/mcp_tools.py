"""Logic behind the MCP tools: a compact answer for Claude, a schema listing and
a locked-down read-only SQL runner. No network, no LLM; Claude only pays for
reading these (short) replies.
"""
import re

import config
from . import answers, engine, fmt

MAX_CHARS = 3500       # cap on one ask reply, keeps the reader's token cost small
MAX_ROWS = 200         # cap on SQL rows returned
TABLES = ("granular", "search_query", "summary")
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|import|install|load|pragma|call|set|"
    r"read_csv\w*|read_parquet|read_json\w*|glob|write_\w+)\b", re.I)


def compact_answer(question: str, prev: dict | None = None) -> tuple[str, dict | None]:
    """(markdown, meta). Date range, headline, the lead paragraph, top notes, the
    first table (10 rows) and the recommended next step. The full deep dive stays
    in the dashboard; this keeps Claude's reading cost low."""
    results = engine.ask_many(question, prev=prev, log=False)
    parts, meta = [], prev
    for i, r in enumerate(results):
        s = answers.render_struct(r)
        block = []
        if len(results) > 1:
            block.append(f"### Part {i + 1} of {len(results)}")
        if s["date"]:
            block.append(f"Date range: {s['date']}")
        if s["head"]:
            block.append(f"**{s['head']}**")
        if s["lead"]:
            block.append(s["lead"])
        nxt = next((body for lbl, _, body in s["deep"] if lbl == "Do next"), "")
        if nxt:
            block.append("**Do next**\n" + nxt)
        if s["notes"]:
            block.append("\n".join(s["notes"].splitlines()[:8]))
        if r.tables:
            title, df = r.tables[0]
            block.append(f"**{title}**\n" + answers._md_table(df.head(10)))
        parts.append("\n\n".join(block))
        if r.meta:
            meta = r.meta
    out = "\n\n---\n\n".join(parts)
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS].rsplit("\n", 1)[0] + "\n\n(trimmed; ask a narrower question for more)"
    return fmt.clean(out), meta


def describe_data() -> str:
    """Tables, row counts, date coverage and column names."""
    con = config.connect_db(read_only=True)
    try:
        have = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        lines = []
        for t in TABLES:
            if t not in have:
                lines.append(f"- {t}: not loaded")
                continue
            n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            cols = [r[0] for r in con.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='{t}'").fetchall()]
            dcol = "metrics_date" if "metrics_date" in cols else None
            rng = con.execute(f"SELECT min({dcol}), max({dcol}) FROM {t}").fetchone() if dcol else (None, None)
            span = f", {rng[0]} to {rng[1]}" if rng[0] else ""
            lines.append(f"- {t}: {n:,} rows{span}\n  columns: {', '.join(cols)}")
        lines.append("Spend = total_budget_burnt, GMV = total_gmv (at MRP), ROAS = GMV / spend. Noida and Noida 1 are one city.")
        return "\n".join(lines)
    finally:
        con.close()


def run_sql(sql: str) -> str:
    """One read-only SELECT. Opened read-only with external file access switched
    off, a keyword filter as a second guard, and a hard row cap."""
    s = (sql or "").strip().rstrip(";").strip()
    if not s or ";" in s:
        return "Send exactly one SELECT statement."
    if not re.match(r"^(select|with)\b", s, re.I) or _FORBIDDEN.search(s):
        return "Only plain SELECT / WITH queries over granular, search_query and summary are allowed."
    con = config.connect_db(read_only=True)
    try:
        con.execute("SET enable_external_access=false")
        df = con.execute(f"SELECT * FROM ({s}) LIMIT {MAX_ROWS}").fetchdf()
    except Exception as e:
        return f"Query failed: {type(e).__name__}: {str(e)[:200]}"
    finally:
        con.close()
    if df.empty:
        return "No rows."
    note = f"\n\n(first {MAX_ROWS} rows)" if len(df) >= MAX_ROWS else ""
    return df.to_csv(index=False) + note

