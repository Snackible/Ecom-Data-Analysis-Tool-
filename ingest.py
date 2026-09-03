"""
Loads Instamart ads CSV exports (IM_SUMMARY_*.csv / IM_GRANULAR_*.csv) from
data/incoming/ into a local DuckDB file.

Each export starts with a 6-line metadata block (Selected Filters / From
Date / To Date / Ads Type / Campaign Name or ID / blank line) before the
real header row - that block is parsed for the report's date range, then
skipped for the actual table data.

Usage:
    python ingest.py

Drop new files into data/incoming/ (any filename containing "SUMMARY" or
"GRANULAR", case-insensitive) and re-run - already-loaded files are moved
to data/processed/ so re-running only picks up what's new. Loading is
idempotent by report period: re-ingesting a file (or a corrected re-export
covering the same date range) replaces that period's rows instead of
duplicating them.
"""
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb

import config

SUMMARY_COLUMNS = [
    "CAMPAIGN_ID", "CAMPAIGN_NAME", "CAMPAIGN_START_DATE", "CAMPAIGN_END_DATE",
    "CAMPAIGN_STATUS", "BIDDING_TYPE", "BUDGET_TYPE", "AD_PROPERTY_COUNT",
    "CITY_COUNT", "KEYWORD_COUNT", "PRODUCT_COUNT", "BRAND_NAME", "eCPM", "eCPC",
    "TOTAL_IMPRESSIONS", "TOTAL_BUDGET", "TOTAL_BUDGET_BURNT", "TOTAL_CLICKS",
    "TOTAL_CTR", "TOTAL_A2C", "A2C_RATE", "TOTAL_GMV", "TOTAL_CONVERSIONS",
    "TOTAL_ROI", "TOTAL_DIRECT_GMV_7_DAYS", "TOTAL_DIRECT_ROI_7_DAYS",
    "TOTAL_DIRECT_GMV_14_DAYS", "TOTAL_DIRECT_ROI_14_DAYS",
]

GRANULAR_COLUMNS = [
    "METRICS_DATE", "CAMPAIGN_ID", "CAMPAIGN_NAME", "CAMPAIGN_START_DATE",
    "CAMPAIGN_END_DATE", "CAMPAIGN_STATUS", "BIDDING_TYPE", "BUDGET_TYPE",
    "AD_PROPERTY", "KEYWORD", "MATCH_TYPE", "L1_CATEGORY", "L2_CATEGORY",
    "PRODUCT_NAME", "CITY", "TARGETING", "BRAND_NAME", "eCPM", "eCPC",
    "TOTAL_IMPRESSIONS", "TOTAL_BUDGET", "TOTAL_BUDGET_BURNT", "TOTAL_CLICKS",
    "BRANDED_SEARCHES_CLICKS", "TOTAL_CTR", "TOTAL_A2C", "A2C_RATE", "TOTAL_GMV",
    "TOTAL_CONVERSIONS", "TOTAL_ROI", "TOTAL_DIRECT_GMV_7_DAYS",
    "TOTAL_DIRECT_ROI_7_DAYS", "TOTAL_DIRECT_GMV_14_DAYS",
    "TOTAL_DIRECT_ROI_14_DAYS", "AD_RANK",
]

PREAMBLE_LINES = 6


class SchemaError(Exception):
    """Raised when a CSV's columns don't match what the dashboard expects."""


def detect_file_type(path: Path) -> str:
    name = path.name.upper()
    if "SUMMARY" in name:
        return "summary"
    if "GRANULAR" in name:
        return "granular"
    raise SchemaError(
        f"{path.name}: can't tell if this is a summary or granular export - "
        f'expected the filename to contain "SUMMARY" or "GRANULAR" '
        f"(e.g. IM_SUMMARY_....csv / IM_GRANULAR_....csv)."
    )


def parse_preamble(path: Path) -> tuple[date, date]:
    """Read the report's From Date / To Date out of the 6-line metadata
    block at the top of the file (format DD/MM/YYYY)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = [next(reader) for _ in range(PREAMBLE_LINES)]

    fields = {row[0]: row[1] for row in rows if row and row[0]}
    try:
        from_date = datetime.strptime(fields["From Date"], "%d/%m/%Y").date()
        to_date = datetime.strptime(fields["To Date"], "%d/%m/%Y").date()
    except (KeyError, ValueError) as exc:
        raise SchemaError(
            f"{path.name}: couldn't read 'From Date'/'To Date' out of the "
            f"first {PREAMBLE_LINES} lines - the export format may have "
            f"changed. Details: {exc}"
        ) from exc
    return from_date, to_date


def validate_columns(con: duckdb.DuckDBPyConnection, staging_table: str,
                      expected: list[str], source_name: str) -> None:
    actual = [row[0] for row in con.execute(f"DESCRIBE {staging_table}").fetchall()]
    missing = [c for c in expected if c not in actual]
    unexpected = [c for c in actual if c not in expected]
    if missing:
        raise SchemaError(
            f"{source_name}: missing expected column(s) {missing}. "
            f"Found columns: {actual}. The export format may have changed - "
            f"update SUMMARY_COLUMNS/GRANULAR_COLUMNS in ingest.py if this "
            f"is an intentional, permanent rename."
        )
    if unexpected:
        print(f"  note: {source_name} has unrecognized extra column(s) {unexpected} - ignored")


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS summary (
            campaign_id VARCHAR, campaign_name VARCHAR,
            campaign_start_date DATE, campaign_end_date DATE,
            campaign_status VARCHAR, bidding_type VARCHAR, budget_type VARCHAR,
            ad_property_count BIGINT, city_count BIGINT, keyword_count BIGINT,
            product_count BIGINT, brand_name VARCHAR, ecpm DOUBLE, ecpc VARCHAR,
            total_impressions BIGINT, total_budget DOUBLE, total_budget_burnt DOUBLE,
            total_clicks BIGINT, total_ctr DOUBLE, total_a2c BIGINT, a2c_rate DOUBLE,
            total_gmv DOUBLE, total_conversions BIGINT, total_roi DOUBLE,
            total_direct_gmv_7d DOUBLE, total_direct_roi_7d DOUBLE,
            total_direct_gmv_14d DOUBLE, total_direct_roi_14d DOUBLE,
            period_start DATE, period_end DATE, source_file VARCHAR,
            loaded_at TIMESTAMP
        );
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS granular (
            metrics_date DATE, campaign_id VARCHAR, campaign_name VARCHAR,
            campaign_start_date DATE, campaign_end_date DATE,
            campaign_status VARCHAR, bidding_type VARCHAR, budget_type VARCHAR,
            ad_property VARCHAR, keyword VARCHAR, match_type VARCHAR,
            l1_category VARCHAR, l2_category VARCHAR, product_name VARCHAR,
            city VARCHAR, targeting VARCHAR, brand_name VARCHAR, ecpm DOUBLE,
            ecpc VARCHAR, total_impressions BIGINT, total_budget DOUBLE,
            total_budget_burnt DOUBLE, total_clicks BIGINT,
            branded_searches_clicks BIGINT, total_ctr DOUBLE, total_a2c BIGINT,
            a2c_rate DOUBLE, total_gmv DOUBLE, total_conversions BIGINT,
            total_roi DOUBLE, total_direct_gmv_7d DOUBLE, total_direct_roi_7d DOUBLE,
            total_direct_gmv_14d DOUBLE, total_direct_roi_14d DOUBLE,
            ad_rank DOUBLE, source_file VARCHAR, loaded_at TIMESTAMP
        );
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ingested_files (
            file_name VARCHAR PRIMARY KEY, file_type VARCHAR,
            period_start DATE, period_end DATE, row_count BIGINT,
            loaded_at TIMESTAMP
        );
    """)


def load_summary(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    period_start, period_end = parse_preamble(path)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE stg AS
        SELECT * FROM read_csv('{path.as_posix()}', skip={PREAMBLE_LINES},
                                header=True, all_varchar=True);
    """)
    validate_columns(con, "stg", SUMMARY_COLUMNS, path.name)

    con.execute("DELETE FROM summary WHERE period_start = ? AND period_end = ?",
                [period_start, period_end])
    con.execute(f"""
        INSERT INTO summary
        SELECT
            CAMPAIGN_ID, CAMPAIGN_NAME,
            TRY_CAST(CAMPAIGN_START_DATE AS DATE),
            TRY_CAST(NULLIF(CAMPAIGN_END_DATE, '') AS DATE),
            CAMPAIGN_STATUS, BIDDING_TYPE, BUDGET_TYPE,
            TRY_CAST(AD_PROPERTY_COUNT AS BIGINT), TRY_CAST(CITY_COUNT AS BIGINT),
            TRY_CAST(KEYWORD_COUNT AS BIGINT), TRY_CAST(PRODUCT_COUNT AS BIGINT),
            BRAND_NAME, TRY_CAST(eCPM AS DOUBLE), eCPC,
            TRY_CAST(TOTAL_IMPRESSIONS AS BIGINT), TRY_CAST(TOTAL_BUDGET AS DOUBLE),
            TRY_CAST(TOTAL_BUDGET_BURNT AS DOUBLE), TRY_CAST(TOTAL_CLICKS AS BIGINT),
            TRY_CAST(REPLACE(TOTAL_CTR, '%', '') AS DOUBLE),
            TRY_CAST(TOTAL_A2C AS BIGINT),
            TRY_CAST(REPLACE(A2C_RATE, '%', '') AS DOUBLE),
            TRY_CAST(TOTAL_GMV AS DOUBLE), TRY_CAST(TOTAL_CONVERSIONS AS BIGINT),
            TRY_CAST(TOTAL_ROI AS DOUBLE),
            TRY_CAST(TOTAL_DIRECT_GMV_7_DAYS AS DOUBLE),
            TRY_CAST(TOTAL_DIRECT_ROI_7_DAYS AS DOUBLE),
            TRY_CAST(TOTAL_DIRECT_GMV_14_DAYS AS DOUBLE),
            TRY_CAST(TOTAL_DIRECT_ROI_14_DAYS AS DOUBLE),
            ?, ?, ?, now()
        FROM stg
    """, [period_start, period_end, path.name])

    return con.execute("SELECT count(*) FROM stg").fetchone()[0]


def load_granular(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE stg AS
        SELECT * FROM read_csv('{path.as_posix()}', skip={PREAMBLE_LINES},
                                header=True, all_varchar=True);
    """)
    validate_columns(con, "stg", GRANULAR_COLUMNS, path.name)

    row_count = con.execute("SELECT count(*) FROM stg").fetchone()[0]
    if row_count == 0:
        print(f"  note: {path.name} has no data rows - nothing to load")
        return 0

    period_start, period_end = con.execute(
        "SELECT min(TRY_CAST(METRICS_DATE AS DATE)), max(TRY_CAST(METRICS_DATE AS DATE)) FROM stg"
    ).fetchone()

    con.execute("DELETE FROM granular WHERE metrics_date BETWEEN ? AND ?",
                [period_start, period_end])
    con.execute(f"""
        INSERT INTO granular
        SELECT
            TRY_CAST(METRICS_DATE AS DATE), CAMPAIGN_ID, CAMPAIGN_NAME,
            TRY_CAST(CAMPAIGN_START_DATE AS DATE),
            TRY_CAST(NULLIF(CAMPAIGN_END_DATE, '') AS DATE),
            CAMPAIGN_STATUS, BIDDING_TYPE, BUDGET_TYPE, AD_PROPERTY,
            NULLIF(KEYWORD, ''), MATCH_TYPE, NULLIF(L1_CATEGORY, ''),
            NULLIF(L2_CATEGORY, ''), NULLIF(PRODUCT_NAME, ''), CITY,
            NULLIF(TARGETING, ''), BRAND_NAME, TRY_CAST(eCPM AS DOUBLE), eCPC,
            TRY_CAST(TOTAL_IMPRESSIONS AS BIGINT), TRY_CAST(TOTAL_BUDGET AS DOUBLE),
            TRY_CAST(TOTAL_BUDGET_BURNT AS DOUBLE), TRY_CAST(TOTAL_CLICKS AS BIGINT),
            TRY_CAST(BRANDED_SEARCHES_CLICKS AS BIGINT),
            TRY_CAST(REPLACE(TOTAL_CTR, '%', '') AS DOUBLE),
            TRY_CAST(TOTAL_A2C AS BIGINT),
            TRY_CAST(REPLACE(A2C_RATE, '%', '') AS DOUBLE),
            TRY_CAST(TOTAL_GMV AS DOUBLE), TRY_CAST(TOTAL_CONVERSIONS AS BIGINT),
            TRY_CAST(TOTAL_ROI AS DOUBLE),
            TRY_CAST(TOTAL_DIRECT_GMV_7_DAYS AS DOUBLE),
            TRY_CAST(TOTAL_DIRECT_ROI_7_DAYS AS DOUBLE),
            TRY_CAST(TOTAL_DIRECT_GMV_14_DAYS AS DOUBLE),
            TRY_CAST(TOTAL_DIRECT_ROI_14_DAYS AS DOUBLE),
            CASE WHEN AD_RANK = 'NA' THEN NULL ELSE TRY_CAST(AD_RANK AS DOUBLE) END,
            ?, now()
        FROM stg
    """, [path.name])

    return row_count


def ingest_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    file_type = detect_file_type(path)
    print(f"Loading {path.name} ({file_type}) ...")

    if file_type == "summary":
        row_count = load_summary(con, path)
        period_start, period_end = parse_preamble(path)
    else:
        row_count = load_granular(con, path)
        if row_count == 0:
            period_start = period_end = None
        else:
            period_start, period_end = con.execute(
                "SELECT min(metrics_date), max(metrics_date) FROM granular "
                "WHERE source_file = ?", [path.name]
            ).fetchone()

    con.execute("""
        INSERT INTO ingested_files VALUES (?, ?, ?, ?, ?, now())
        ON CONFLICT (file_name) DO UPDATE SET
            period_start = excluded.period_start, period_end = excluded.period_end,
            row_count = excluded.row_count, loaded_at = excluded.loaded_at
    """, [path.name, file_type, period_start, period_end, row_count])

    print(f"  {row_count} rows -> {file_type} table (period {period_start} .. {period_end})")

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    # .replace() (not .rename()) - on Windows, rename() raises FileExistsError
    # if the destination already exists (e.g. re-dropping the same filename);
    # replace() overwrites, matching POSIX rename() behavior.
    path.replace(config.PROCESSED_DIR / path.name)


def main() -> None:
    config.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(config.INCOMING_DIR.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {config.INCOMING_DIR}")
        return

    con = duckdb.connect(str(config.DB_PATH))
    ensure_schema(con)

    failures = []
    for path in csv_files:
        try:
            ingest_file(con, path)
        except SchemaError as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            failures.append(path.name)

    con.close()

    if failures:
        print(f"\n{len(failures)} file(s) failed and were left in {config.INCOMING_DIR}: {failures}")
        sys.exit(1)


if __name__ == "__main__":
    main()
