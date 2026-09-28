"""
Action tools: the only tools that change data.

Flagging a trailer for pull puts it on the yard jockey's worklist. Guardrails:
  - Input validation (trailer must be on site, reason must be meaningful)
  - No duplicate open flags for the same trailer
  - Every flag and resolution is recorded with who and when (audit trail)
  - Only registered when writes are enabled (see server.py)
"""

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from warehouse_ops.db import FMT, check_site, execute, query, query_one, utc_now

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                        idempotent_hint=False, open_world_hint=False)


def _find_on_site_trailer(trailer: str) -> dict:
    key = trailer.strip().upper()
    t = query_one("""
        SELECT t.trailer_id, t.site_id, t.trailer_number, t.direction, t.load_status,
               t.status, t.current_location, c.scac
        FROM trailers t JOIN carriers c USING (carrier_id)
        WHERE t.trailer_id = ? OR t.trailer_number = ?
        ORDER BY t.checked_in_at DESC LIMIT 1
    """, (key, key))
    if t is None:
        raise ToolError(f"No trailer found matching '{trailer}'.")
    if t["status"] == "departed":
        raise ToolError(f"Trailer {t['trailer_id']} has already departed; it cannot be flagged.")
    return t


def flag_trailer_for_pull(trailer: str, reason: str, priority: str = "normal",
                          flagged_by: str = "claude") -> dict:
    """
    Flag a trailer so the yard team pulls it: to a door (loaded inbound),
    or for carrier pickup (empties). Adds it to the open-flags worklist.

    This WRITES data. Only call it when the user asks to flag a trailer,
    and confirm the trailer and reason with them first.

    Args:
        trailer: trailer_id (e.g. 'CHI1-T001734') or trailer number (e.g. 'BRVL27349').
        reason: Why it needs to move, 10-300 characters
            (e.g. 'Loaded inbound, 166 h dwell, over SOP limit').
        priority: 'normal' or 'urgent'.
        flagged_by: Name of the person requesting the flag.
    """
    t = _find_on_site_trailer(trailer)
    reason = reason.strip()
    if not 10 <= len(reason) <= 300:
        raise ToolError("reason must be 10-300 characters and explain why the trailer should move.")
    if priority not in ("normal", "urgent"):
        raise ToolError("priority must be 'normal' or 'urgent'.")
    flagged_by = flagged_by.strip()[:60] or "claude"

    existing = query_one("""SELECT flag_id, flagged_at FROM trailer_flags
                            WHERE trailer_id = ? AND resolved_at IS NULL""", (t["trailer_id"],))
    if existing:
        raise ToolError(f"Trailer {t['trailer_id']} already has open flag #{existing['flag_id']} "
                        f"(since {existing['flagged_at']} UTC). Resolve it before flagging again.")

    now = utc_now().strftime(FMT)
    flag_id = execute("""
        INSERT INTO trailer_flags (trailer_id, site_id, reason, priority, flagged_by, flagged_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (t["trailer_id"], t["site_id"], reason, priority, flagged_by, now))

    return {
        "status": "flagged",
        "flag_id": flag_id,
        "flagged_at_utc": now,
        "trailer": t,
        "priority": priority,
        "reason": reason,
        "next_step": ("Yard jockey moves it to a door" if t["load_status"] == "loaded"
                      and t["direction"] == "inbound" else "Yard lead contacts the carrier"),
    }


def list_trailer_flags(site_id: str | None = None, include_resolved: bool = False) -> dict:
    """
    The yard team's worklist: trailers flagged for pull, urgent first.

    Args:
        site_id: 'CHI1' or 'SOC1'. Omit for all sites.
        include_resolved: Also show resolved flags (default: open flags only).
    """
    site_id = check_site(site_id)
    flags = query("""
        SELECT f.*, t.trailer_number, t.current_location, t.load_status, t.status AS trailer_status
        FROM trailer_flags f JOIN trailers t USING (trailer_id)
        WHERE (? IS NULL OR f.site_id = ?)
          AND (? OR f.resolved_at IS NULL)
        ORDER BY (f.resolved_at IS NOT NULL), (f.priority = 'urgent') DESC, f.flagged_at
    """, (site_id, site_id, 1 if include_resolved else 0))
    return {"count": len(flags),
            "open": sum(1 for f in flags if f["resolved_at"] is None),
            "flags": flags}


def resolve_trailer_flag(flag_id: int, resolution_note: str, resolved_by: str = "claude") -> dict:
    """
    Close a flag once the trailer has been handled (moved to a door, picked up, etc.).

    This WRITES data. Confirm with the user before calling it.

    Args:
        flag_id: The flag number from list_trailer_flags.
        resolution_note: What was done, 5-300 characters (e.g. 'Moved to door 4').
        resolved_by: Name of the person resolving it.
    """
    flag = query_one("SELECT * FROM trailer_flags WHERE flag_id = ?", (flag_id,))
    if flag is None:
        raise ToolError(f"Flag #{flag_id} not found.")
    if flag["resolved_at"]:
        raise ToolError(f"Flag #{flag_id} was already resolved at {flag['resolved_at']} UTC.")
    note = resolution_note.strip()
    if not 5 <= len(note) <= 300:
        raise ToolError("resolution_note must be 5-300 characters.")

    now = utc_now().strftime(FMT)
    execute("""UPDATE trailer_flags SET resolved_at = ?, resolved_by = ?, resolution_note = ?
               WHERE flag_id = ? AND resolved_at IS NULL""",
            (now, resolved_by.strip()[:60] or "claude", note, flag_id))
    return {"status": "resolved", "flag_id": flag_id, "resolved_at_utc": now,
            "trailer_id": flag["trailer_id"], "resolution_note": note}


def register(mcp, allow_writes: bool) -> None:
    mcp.tool(annotations=READ_ONLY)(list_trailer_flags)
    if allow_writes:
        mcp.tool(annotations=WRITE)(flag_trailer_for_pull)
        mcp.tool(annotations=WRITE)(resolve_trailer_flag)
