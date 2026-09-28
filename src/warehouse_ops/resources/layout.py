"""Site layout resource, generated from the database."""

from mcp.server.mcpserver.exceptions import ResourceError

from warehouse_ops.db import query, query_one


def site_layout(site_id: str) -> str:
    """Physical layout of a site: pick zones, reserve storage, dock doors and yard."""
    site_id = site_id.strip().upper()
    site = query_one("SELECT * FROM sites WHERE site_id = ?", (site_id,))
    if site is None:
        raise ResourceError(f"Unknown site '{site_id}'.")

    zones = query("""
        SELECT zone, location_type, COUNT(*) AS locations,
               MAX(aisle) AS aisles, MAX(bay) AS bays, MAX(level) AS levels,
               MIN(travel_distance_ft) AS min_ft, MAX(travel_distance_ft) AS max_ft
        FROM locations WHERE site_id = ? GROUP BY zone, location_type ORDER BY zone
    """, (site_id,))
    doors = query("""
        SELECT door_type, MIN(door_number) AS first, MAX(door_number) AS last, COUNT(*) AS n
        FROM dock_doors WHERE site_id = ? GROUP BY door_type ORDER BY first
    """, (site_id,))
    oos = query("""SELECT door_id FROM dock_doors
                   WHERE site_id = ? AND status = 'out_of_service'""", (site_id,))
    spots = query_one("""
        SELECT COUNT(DISTINCT to_location) AS n FROM yard_moves
        WHERE to_location LIKE 'Y-%' AND trailer_id LIKE ? || '-%'
    """, (site_id,))

    lines = [f"# {site['name']} ({site_id})",
             f"{site['city']}, {site['state']} | Time zone: {site['timezone']} "
             f"(all tool timestamps are UTC)", "", "## Storage zones",
             "| Zone | Type | Locations | Aisles x Bays x Levels | Travel from pick start |",
             "|---|---|---|---|---|"]
    for z in zones:
        lines.append(f"| {z['zone']} | {z['location_type']} | {z['locations']} | "
                     f"{z['aisles']} x {z['bays']} x {z['levels']} | "
                     f"{z['min_ft']:.0f} to {z['max_ft']:.0f} ft |")
    lines += ["", "Location IDs look like SITE-ZoneAisle-Bay-Level, e.g. "
              f"{site_id}-A03-07-2. Golden zone = pick levels 2 and 3.", "",
              "## Dock doors"]
    for d in doors:
        lines.append(f"- {d['door_type'].title()}: doors {d['first']} to {d['last']} ({d['n']} doors)")
    if oos:
        lines.append(f"- Out of service: {', '.join(o['door_id'] for o in oos)}")
    lines += ["", "## Yard", f"- {spots['n']} distinct yard spots used in the recorded history "
              "(IDs Y-001 upward)"]
    return "\n".join(lines)


def register(mcp) -> None:
    mcp.resource("warehouse://sites/{site_id}/layout", mime_type="text/markdown",
                 title="Site layout")(site_layout)
