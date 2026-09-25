"""Shared test setup: build a fresh database once per test run."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def seeded_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("data") / "test.db"
    subprocess.run([sys.executable, str(ROOT / "data" / "seed.py"), "--db", str(db),
                    "--days", "30", "--seed", "1"], check=True, capture_output=True)
    mp = pytest.MonkeyPatch()
    mp.setenv("WAREHOUSE_DB", str(db))
    yield db
    mp.undo()
