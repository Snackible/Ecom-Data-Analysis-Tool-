# instamart-ads-dashboard

Local dashboard over monthly Instamart/Swiggy ads exports (campaign
performance: impressions, spend, GMV, ROI, etc.) for Snackible.

## Project structure

```
config.py            Paths (env-var overridable) - single source of truth
ingest.py             Loads CSVs from data/incoming/ into DuckDB
dashboard.py           Streamlit app reading from DuckDB
data/incoming/        Drop this month's two CSV exports here
data/processed/        Files move here automatically after a successful load
db/ads.duckdb           The database (gitignored)
```

Storage is entirely local - `db/ads.duckdb` is a single file on disk, no
server, no cloud. `streamlit run dashboard.py` opens the dashboard in your
browser at `http://localhost:8501`.

## Local setup

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

## Adding next month's files

1. From the Instamart ads dashboard, export the two reports (summary and
   granular) for the new period.
2. Drop both CSV files into `data/incoming/` - the filename just needs to
   contain `SUMMARY` or `GRANULAR` somewhere in it (case-insensitive); the
   platform's default export names already satisfy this.
3. Run:

   ```bash
   python ingest.py
   ```

   This validates each file's columns against the expected schema (and
   fails loudly, without touching the database, if a column is missing or
   renamed), loads the rows into DuckDB, and moves the file into
   `data/processed/` so it won't be re-processed next time.

   Re-running with the same file, or a corrected re-export covering the
   same date range, replaces that period's rows rather than duplicating
   them - safe to re-run.
4. Refresh the dashboard (or start it if it isn't running):

   ```bash
   streamlit run dashboard.py
   ```

## Data notes

- Both exports share `CAMPAIGN_ID`. `granular` is the daily/city/keyword
  level detail; `summary` is a per-campaign rollup over the report's date
  range (tracked via `period_start`/`period_end` columns, since the summary
  export has no per-row date).
- `eCPC` is kept as raw text - Instamart's export currently always reports
  it as the literal string `NA`.
- `TOTAL_CTR` and `A2C_RATE` are stored as numeric percentage points (the
  `%` sign is stripped on load), e.g. `4.71` means 4.71%, not `0.0471`.
- `ingested_files` table logs every file loaded (name, type, period,
  row count, timestamp) - useful for checking what's been loaded so far:
  `SELECT * FROM ingested_files ORDER BY loaded_at DESC;`
