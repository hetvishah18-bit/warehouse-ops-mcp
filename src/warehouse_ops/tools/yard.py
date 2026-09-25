"""
Yard tools: what's in the yard, what's been sitting too long, and where a trailer has been.
"""

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from warehouse_ops.db import check_site, hours_between, query, query_one, utc_now

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

ON_SITE_SQL = """
    SELECT t.trailer_id, t.site_id, t.trailer_number, c.name AS carrier, c.scac,
           t.direction, t.load_status, t.status, t.current_location, t.checked_in_at,
           (SELECT MAX(m.moved_at) FROM yard_moves m WHERE m.trailer_id = t.trailer_id)
               AS last_move_at
    FROM trailers t
    JOIN carriers c USING (carrier_id)
    WHERE t.status != 'departed'
      AND (? IS NULL OR t.site_id = ?)
"""


def _with_dwell(rows: list[dict]) -> list[dict]:
    """Add dwell (hours since check-in) and idle (hours since last move) to each trailer."""
    now = utc_now()
    for r in rows:
        r["dwell_hours"] = hours_between(r["checked_in_at"], now)
        r["idle_hours"] = hours_between(r["last_move_at"] or r["checked_in_at"], now)
    return rows


def get_yard_status(site_id: str | None = None, limit: int = 25) -> dict:
    """
    Snapshot of every trailer currently on site (in the yard or at a dock door).

    Use this for questions like "what's in the yard right now?" or
    "how many loaded inbound trailers are waiting?".

    Args:
        site_id: Site code such as 'CHI1' or 'SOC1'. Omit to include all sites.
        limit: Maximum trailers to list individually, longest dwell first (the
            summary counts always cover every trailer).

    Returns a summary (counts by site, status, direction and load) plus the
    trailer list with dwell_hours (time since check-in) and idle_hours
    (time since the trailer last moved).
    """
    site_id = check_site(site_id)
    trailers = _with_dwell(query(ON_SITE_SQL, (site_id, site_id)))
    trailers.sort(key=lambda r: r["dwell_hours"], reverse=True)

    summary: dict[str, dict] = {}
    for t in trailers:
        s = summary.setdefault(t["site_id"], {
            "total_on_site": 0, "in_yard": 0, "at_door": 0,
            "inbound_loaded_waiting": 0, "outbound_loaded_awaiting_pickup": 0,
            "empties": 0, "over_48h": 0,
        })
        s["total_on_site"] += 1
        s[t["status"]] += 1
        if t["status"] == "in_yard" and t["direction"] == "inbound" and t["load_status"] == "loaded":
            s["inbound_loaded_waiting"] += 1
        if t["status"] == "in_yard" and t["direction"] == "outbound" and t["load_status"] == "loaded":
            s["outbound_loaded_awaiting_pickup"] += 1
        if t["load_status"] == "empty":
            s["empties"] += 1
        if t["dwell_hours"] > 48:
            s["over_48h"] += 1

    return {
        "as_of_utc": utc_now().isoformat(sep=" "),
        "summary_by_site": summary,
        "trailers": trailers[: max(1, limit)],
        "trailers_listed": min(len(trailers), max(1, limit)),
        "trailers_total": len(trailers),
    }


def get_long_dwell_trailers(site_id: str | None = None, min_hours: float = 48) -> dict:
    """
    Trailers that have been on site longer than a threshold. These are the
    "forgotten trailers" that tie up yard space and rack up detention charges.

    Args:
        site_id: Site code such as 'CHI1' or 'SOC1'. Omit to include all sites.
        min_hours: Dwell threshold in hours (default 48).

    Returns trailers sorted by dwell time (longest first), each with carrier,
    location, load status, dwell_hours and idle_hours, plus a suggested
    action based on the trailer's direction and load.
    """
    site_id = check_site(site_id)
    if min_hours < 0:
        raise ToolError("min_hours must be zero or positive")

    trailers = [t for t in _with_dwell(query(ON_SITE_SQL, (site_id, site_id)))
                if t["dwell_hours"] >= min_hours]
    trailers.sort(key=lambda r: r["dwell_hours"], reverse=True)

    for t in trailers:
        if t["direction"] == "inbound" and t["load_status"] == "loaded":
            t["suggested_action"] = "Loaded inbound still waiting: prioritize a door to unload."
        elif t["direction"] == "inbound" and t["load_status"] == "empty":
            t["suggested_action"] = "Empty: contact carrier for pickup to free the yard spot."
        elif t["direction"] == "outbound" and t["load_status"] == "loaded":
            t["suggested_action"] = "Loaded outbound: confirm carrier pickup time."
        elif t["direction"] == "outbound" and t["load_status"] == "empty":
            t["suggested_action"] = "Empty outbound: schedule a door to load or release it."
        else:
            t["suggested_action"] = "At a door: check progress with the dock lead."

    return {
        "as_of_utc": utc_now().isoformat(sep=" "),
        "min_hours": min_hours,
        "count": len(trailers),
        "trailers": trailers,
    }


def get_trailer_history(trailer: str) -> dict:
    """
    Full history for one trailer visit: details, every yard move, and its
    dock appointment (if any).

    Args:
        trailer: Either the trailer_id (e.g. 'CHI1-T001598') or the number
            painted on the trailer (e.g. 'NSTD48213'). If a trailer number has
            visited more than once, the most recent visit is returned.
    """
    key = trailer.strip().upper()
    t = query_one("""
        SELECT t.*, c.name AS carrier, c.scac
        FROM trailers t JOIN carriers c USING (carrier_id)
        WHERE t.trailer_id = ? OR t.trailer_number = ?
        ORDER BY t.checked_in_at DESC LIMIT 1
    """, (key, key))
    if t is None:
        raise ToolError(f"No trailer found matching '{trailer}'.")

    moves = query("""
        SELECT moved_at, move_type, from_location, to_location
        FROM yard_moves WHERE trailer_id = ? ORDER BY moved_at
    """, (t["trailer_id"],))
    appointment = query_one("""
        SELECT appt_id, door_id, scheduled_start, scheduled_end, status
        FROM appointments WHERE trailer_id = ?
    """, (t["trailer_id"],))

    end = t["departed_at"] or utc_now()
    t["dwell_hours"] = hours_between(t["checked_in_at"], end)
    return {"trailer": t, "moves": moves, "appointment": appointment}


def register(mcp) -> None:
    mcp.tool(annotations=READ_ONLY)(get_yard_status)
    mcp.tool(annotations=READ_ONLY)(get_long_dwell_trailers)
    mcp.tool(annotations=READ_ONLY)(get_trailer_history)
