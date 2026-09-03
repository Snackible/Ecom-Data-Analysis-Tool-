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
