"""
Dock tools: door status, door utilization, and upcoming appointments.
"""

from datetime import datetime, timedelta

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from warehouse_ops.db import FMT, check_site, hours_between, query, utc_now

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def get_door_status(site_id: str) -> dict:
    """
    Current status of every dock door at a site, including which trailer is
    at each occupied door and how long it has been there.

    Args:
        site_id: Site code such as 'CHI1' or 'SOC1'.

    Returns counts of open / occupied / out-of-service doors by door type,
    plus a row per door.
    """
    site_id = check_site(site_id)
    doors = query("""
        SELECT d.door_id, d.door_number, d.door_type, d.status,
               t.trailer_id, t.trailer_number, c.scac AS carrier_scac, t.load_status,
               (SELECT MAX(m.moved_at) FROM yard_moves m
                 WHERE m.trailer_id = t.trailer_id AND m.move_type = 'to_door') AS at_door_since
        FROM dock_doors d
        LEFT JOIN trailers t ON t.current_location = d.door_id AND t.status = 'at_door'
        LEFT JOIN carriers c ON c.carrier_id = t.carrier_id
        WHERE d.site_id = ?
        ORDER BY d.door_number
    """, (site_id,))

    now = utc_now()
    counts: dict[str, dict[str, int]] = {}
    for d in doors:
        c = counts.setdefault(d["door_type"], {"open": 0, "occupied": 0, "out_of_service": 0})
        c[d["status"]] += 1
        d["hours_at_door"] = hours_between(d["at_door_since"], now) if d["at_door_since"] else None

    return {"site_id": site_id, "as_of_utc": now.isoformat(sep=" "),
            "counts_by_door_type": counts, "doors": doors}


def get_dock_utilization(site_id: str, date: str | None = None,
                         door_type: str | None = None) -> dict:
    """
    How busy the dock doors were on a given day: hours each door was occupied
    and utilization % of the day. Useful for spotting idle doors, overloaded
    doors, and overall dock capacity.

    Args:
        site_id: Site code such as 'CHI1' or 'SOC1'.
        date: Day to analyze as 'YYYY-MM-DD' (UTC). Defaults to yesterday,
            the most recent full day. For today, only elapsed hours count.
        door_type: 'inbound' or 'outbound'. Omit for both.

    Returns site-level utilization, trailers handled, and a per-door
    breakdown sorted from busiest to least busy.
    """
    site_id = check_site(site_id)
    if door_type is not None and door_type not in ("inbound", "outbound"):
        raise ToolError("door_type must be 'inbound', 'outbound', or omitted")

    now = utc_now()
    if date is None:
        day = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0)
    else:
        try:
            day = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise ToolError("date must be in YYYY-MM-DD format") from None
    if day > now:
        raise ToolError("date is in the future")
    window_start = day
    window_end = min(day + timedelta(days=1), now)
    window_hours = (window_end - window_start).total_seconds() / 3600

    doors = query("""
        SELECT door_id, door_number, door_type, status FROM dock_doors
        WHERE site_id = ? AND (? IS NULL OR door_type = ?)
        ORDER BY door_number
    """, (site_id, door_type, door_type))
    door_ids = {d["door_id"] for d in doors}

    visits = query("""
        SELECT a.trailer_id, a.to_location AS door_id,
               a.moved_at AS start, b.moved_at AS end
        FROM yard_moves a
        LEFT JOIN yard_moves b
               ON b.trailer_id = a.trailer_id AND b.move_type = 'from_door'
        WHERE a.move_type = 'to_door'
          AND a.to_location LIKE ? || '-D%'
          AND a.moved_at < ?
          AND (b.moved_at IS NULL OR b.moved_at > ?)
    """, (site_id, window_end.strftime(FMT), window_start.strftime(FMT)))

    per_door = {d["door_id"]: {**d, "occupied_hours": 0.0, "trailers_handled": 0} for d in doors}
    for v in visits:
        if v["door_id"] not in door_ids:
            continue
        start = max(datetime.strptime(v["start"], FMT), window_start)
        end = min(datetime.strptime(v["end"], FMT) if v["end"] else now, window_end)
        if end > start:
            per_door[v["door_id"]]["occupied_hours"] += (end - start).total_seconds() / 3600
            per_door[v["door_id"]]["trailers_handled"] += 1

    rows = list(per_door.values())
    for r in rows:
        r["occupied_hours"] = round(r["occupied_hours"], 1)
        r["utilization_pct"] = round(100 * r["occupied_hours"] / window_hours, 1)
    rows.sort(key=lambda r: r["occupied_hours"], reverse=True)

    in_service = [r for r in rows if r["status"] != "out_of_service"]
    total_capacity = window_hours * len(in_service)
    total_used = sum(r["occupied_hours"] for r in in_service)

    return {
        "site_id": site_id,
        "date_utc": window_start.strftime("%Y-%m-%d"),
        "hours_in_window": round(window_hours, 1),
        "door_type": door_type or "all",
        "doors_in_service": len(in_service),
        "overall_utilization_pct": round(100 * total_used / total_capacity, 1) if total_capacity else 0,
        "trailers_handled": sum(r["trailers_handled"] for r in rows),
        "idle_doors": [r["door_id"] for r in in_service if r["occupied_hours"] == 0],
        "doors": rows,
    }


def get_upcoming_appointments(site_id: str, hours: int = 24) -> dict:
    """
    Dock appointments scheduled in the next N hours at a site, with carrier and
    assigned door. Also flags doors that are double-booked in that window.

    Args:
        site_id: Site code such as 'CHI1' or 'SOC1'.
        hours: How far ahead to look (1-168, default 24).
    """
    site_id = check_site(site_id)
    if not 1 <= hours <= 168:
        raise ToolError("hours must be between 1 and 168")

    now = utc_now()
    end = now + timedelta(hours=hours)
    appts = query("""
        SELECT a.appt_id, a.scheduled_start, a.scheduled_end, a.door_id, a.direction,
               a.status, c.name AS carrier, c.scac
        FROM appointments a JOIN carriers c USING (carrier_id)
        WHERE a.site_id = ? AND a.status IN ('scheduled', 'arrived')
          AND a.scheduled_start BETWEEN ? AND ?
        ORDER BY a.scheduled_start
    """, (site_id, now.strftime(FMT), end.strftime(FMT)))

    # Double-booking check: same door, overlapping time windows
    conflicts = []
    by_door: dict[str, list[dict]] = {}
    for a in appts:
        by_door.setdefault(a["door_id"], []).append(a)
    for door, items in by_door.items():
        for prev, nxt in zip(items, items[1:]):
            if nxt["scheduled_start"] < prev["scheduled_end"]:
                conflicts.append({"door_id": door, "appt_ids": [prev["appt_id"], nxt["appt_id"]],
                                  "overlap_starts": nxt["scheduled_start"]})

    return {
        "site_id": site_id,
        "window_utc": [now.strftime(FMT), end.strftime(FMT)],
        "count": len(appts),
        "double_booked_doors": conflicts,
        "appointments": appts,
    }


def register(mcp) -> None:
    mcp.tool(annotations=READ_ONLY)(get_door_status)
    mcp.tool(annotations=READ_ONLY)(get_dock_utilization)
    mcp.tool(annotations=READ_ONLY)(get_upcoming_appointments)
