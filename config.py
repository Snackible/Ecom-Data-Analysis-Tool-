"""
Central configuration for the Instamart ads dashboard.

Paths are env-var overridable so a monthly CSV drop never requires a code
change - see README.md "Adding next month's files".
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

INCOMING_DIR = Path(os.environ.get("INCOMING_DIR", BASE_DIR / "data" / "incoming"))
PROCESSED_DIR = Path(os.environ.get("PROCESSED_DIR", BASE_DIR / "data" / "processed"))
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "db" / "ads.duckdb"))

# DuckDB detects the *host's* total RAM, not a container's cgroup limit, and
# sizes its internal memory budget off that - on Render's free tier (512MB
# actual limit) this caused real OOM kills during ingest. Capped well under
# 512MB to leave headroom for Python/Streamlit/pandas's own overhead, which
# this pragma doesn't cover. Override via env var if the host has more RAM.
DUCKDB_MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "200MB")
DUCKDB_THREADS = os.environ.get("DUCKDB_THREADS", "2")


def connect_db(read_only: bool = False):
    """Single entry point for opening the DuckDB file - always caps memory/
    threads so behavior is identical everywhere a connection is opened."""
    import duckdb
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    con.execute(f"SET memory_limit='{DUCKDB_MEMORY_LIMIT}'")
    con.execute(f"SET threads={DUCKDB_THREADS}")
    return con
