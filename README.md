# instamart-ads-dashboard

Dashboard over monthly Instamart/Swiggy ads exports (campaign performance:
impressions, spend, GMV, ROI, etc.) for Snackible.

**Live**: https://ecom-data-analysis-tool.onrender.com/ (password-gated, see
below - free-tier hosting, so it sleeps after 15 min idle and takes ~30-50s
to wake on the next visit).

## Project structure

```
config.py            Paths (env-var overridable) - single source of truth
ingest.py             Loads CSV/Excel exports from data/incoming/ into DuckDB
dashboard.py           Streamlit app: filters, KPIs, AI insights, charts, upload panel
data/incoming/        Drop this month's exports here (CLI workflow)
data/processed/        Files move here automatically after a successful load
db/ads.duckdb           The database - committed to git (see "Why the data file
                         is committed" below), not gitignored like a normal DB
render.yaml             Render deploy config (free tier, $0/month)
```

## Local setup

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
streamlit run dashboard.py
```

No password prompt locally - `DASHBOARD_PASSWORD` is only enforced when that
env var is set (i.e. on the Render deployment). Local `db/ads.duckdb`
already has real data in it since it's committed to the repo.

## Adding next month's data

Two ways to get new data in - pick whichever's convenient:

### A. Through the dashboard's upload panel (works locally or on the live site)

Open the "📤 Upload new CSV/Excel exports" panel at the top, drop the new
`IM_SUMMARY_*` / `IM_GRANULAR_*` file(s) (`.csv` or `.xlsx`/`.xls` both
work - Excel files are converted to the same layout under the hood), click
**Ingest uploaded files**.

- **On the live Render deployment**: if `GITHUB_TOKEN` is configured (see
  below), a successful upload also **auto-commits and pushes**
  `db/ads.duckdb` back to GitHub, so the data survives Render's free-tier
  filesystem being wiped on the next restart/redeploy. You'll see a
  confirmation message, and Render will auto-redeploy a couple minutes
  later with the new data baked in.
- **Locally**: the upload updates your local `db/ads.duckdb` immediately;
  commit and push it yourself when ready (see workflow B).

### B. Manually (CLI)

1. Drop the file(s) into `data/incoming/` - filename just needs to contain
   `SUMMARY` or `GRANULAR` (case-insensitive); `.csv` or `.xlsx`/`.xls`.
2. Run:

   ```bash
   python ingest.py
   ```

   Validates each file's columns against the expected schema (fails
   loudly, without touching the database, if a column is missing or
   renamed), loads the rows into DuckDB, moves the file into
   `data/processed/`. Re-running with the same file, or a corrected
   re-export covering the same date range, replaces that period's rows
   rather than duplicating them - safe to re-run.
3. To update the live site: `git add db/ads.duckdb && git commit -m "..." && git push`
   - Render auto-redeploys on push to `main`.

## Why the data file is committed to git

Render's free tier has no persistent disk - anything written to the
filesystem at runtime (including uploads) is wiped on the next
redeploy/restart. Since `db/ads.duckdb` is small (~18MB, not the raw
~120MB CSVs), it ships as part of the deploy instead: `.gitignore` does
**not** exclude it (unlike a typical project's DB file), and every push
that includes an updated `db/ads.duckdb` becomes the live data on the next
Render deploy.

## Enabling auto-commit-from-Render (optional)

Without this, the upload panel still works locally and on the live site
for viewing - it just won't persist past the next Render restart unless
you manually commit. To make live uploads persist automatically:

1. GitHub → this repo → **Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token**.
2. Scope it to **only this repository** (`Snackible/Ecom-Data-Analysis-Tool-`),
   permission **Contents: Read and write**. Don't use a classic token with
   access to all repos - this token lives on a hosted server.
3. Render dashboard → this service → **Environment** → add `GITHUB_TOKEN`
   with that value.
4. That's it - the next upload-through-the-dashboard on the live site will
   commit and push automatically.

Note: since Render auto-deploys on every push to `main`, an auto-commit
triggers a rebuild + brief restart of the live app a couple minutes later.
Fine for occasional monthly updates; if uploads become frequent, this
tradeoff is worth reconsidering.

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
  row count, timestamp): `SELECT * FROM ingested_files ORDER BY loaded_at DESC;`
- Excel uploads (`.xlsx`/`.xls`) are converted to the same plain-CSV layout
  DuckDB expects before validation - if a sheet doesn't actually match the
  expected preamble+header+rows structure, it fails loudly with a clear
  schema error rather than silently misreading it.

## Dashboard features

- **Filters**: customizable date range with optional "Compare two periods"
  mode (any two arbitrary ranges, not just auto-previous-period); Campaigns;
  Keywords (scoped to whichever campaigns are selected); Cities.
- **KPIs** (top-left): ROI, GMV, Spend, Impressions, eCPM, Clicks, and a
  combined Clicks→Add-to-cart→Conversion rate stat. Show current-vs-compare
  deltas when compare mode is on.
- **AI Insights column** (beside the KPIs): top 10 outlier products ranked
  by ROI deviation from the blended average, restricted to products with
  at least median spend. This is a deterministic formula, not an LLM call -
  see project chat history for why that tradeoff was made.
- **Conversion funnel**, **delayed-impact attribution** (7-day direct vs.
  full attribution), **correlation heatmap** across core metrics, **daily
  GMV/spend trend**, **GMV by city**, **performance by ad format**, **top
  keywords by GMV (with city breakdown)**, **campaign performance table**,
  **underperformer watchlist**, **spend-vs-GMV scatter with trendline**.
- **Dark/light theme toggle**, bold high-contrast palette.
- Password-gated when `DASHBOARD_PASSWORD` is set (hosted deployment only).
