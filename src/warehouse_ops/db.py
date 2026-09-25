"""
Database access for the MCP tools.

- Connections are opened READ-ONLY, so no tool can modify data by accident.
- Queries always use parameters (?), never string formatting, to prevent SQL injection.
- Rows come back as plain dicts, which the MCP server turns into JSON for Claude.
- Expected problems (bad site code, bad date) raise ToolError, whose message
  is shown to Claude so it can correct itself. Other exceptions are hidden.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "warehouse.db"
FMT = "%Y-%m-%d %H:%M:%S"


def db_path() -> Path:
    """Database location: WAREHOUSE_DB env var if set, else data/warehouse.db."""
    return Path(os.environ.get("WAREHOUSE_DB", DEFAULT_DB))


def connect() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Run: uv run python data/seed.py"
        )
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    """Run a SELECT and return all rows as dicts."""
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def utc_now() -> datetime:
    """Current time in UTC as a naive datetime (matches how timestamps are stored)."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, FMT)


def hours_between(start: str | datetime, end: str | datetime) -> float:
    s = parse_ts(start) if isinstance(start, str) else start
    e = parse_ts(end) if isinstance(end, str) else end
    return round((e - s).total_seconds() / 3600, 1)


def valid_sites() -> list[str]:
    return [r["site_id"] for r in query("SELECT site_id FROM sites ORDER BY site_id")]


def check_site(site_id: str | None) -> str | None:
    """Normalize a site id and raise a clear error if it doesn't exist."""
    if site_id is None:
        return None
    site_id = site_id.strip().upper()
    sites = valid_sites()
    if site_id not in sites:
        raise ToolError(f"Unknown site '{site_id}'. Valid sites: {', '.join(sites)}")
    return site_id
