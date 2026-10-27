import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from fixture_db import build_fixture  # noqa: E402


def _use(monkeypatch, path):
    monkeypatch.setattr(config, "DB_PATH", path)


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    p = tmp_path / "empty.duckdb"
    build_fixture(p, empty=True)
    _use(monkeypatch, p)
    return p


@pytest.fixture
def one_month(tmp_path, monkeypatch):
    p = tmp_path / "one.duckdb"
    build_fixture(p, months=("2026-07",))
    _use(monkeypatch, p)
    return p


@pytest.fixture
def two_months(tmp_path, monkeypatch):
    p = tmp_path / "two.duckdb"
    build_fixture(p, months=("2026-07", "2026-08"))
    _use(monkeypatch, p)
    return p


@pytest.fixture
def noida_month(tmp_path, monkeypatch):
    p = tmp_path / "noida.duckdb"
    build_fixture(p, months=("2026-09",), noida=True)
    _use(monkeypatch, p)
    return p
