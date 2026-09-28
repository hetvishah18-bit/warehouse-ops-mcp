"""
Inventory tools: find SKUs, see where stock sits, and spot pick faces that
need replenishment from reserve before they run out.
"""

from datetime import timedelta

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from warehouse_ops.db import FMT, check_site, query, query_one, utc_now

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
DEMAND_WINDOW_DAYS = 14   # average daily usage is measured over the last 2 weeks


def _daily_units(site_id: str) -> dict[str, float]:
    since = (utc_now() - timedelta(days=DEMAND_WINDOW_DAYS)).strftime(FMT)
    rows = query("""
        SELECT sku, SUM(quantity) AS units FROM pick_lines
        WHERE site_id = ? AND picked_at >= ? GROUP BY sku
    """, (site_id, since))
    return {r["sku"]: r["units"] / DEMAND_WINDOW_DAYS for r in rows}


def find_sku(search: str, limit: int = 10) -> dict:
    """
    Search SKUs by code, description or category, e.g. 'troffer',
    'exit sign', 'DRV-000', or '4000K'.

    Args:
        search: Text to look for (case-insensitive, partial matches allowed).
        limit: Maximum results (default 10, max 50).
    """
    term = search.strip()
    if len(term) < 2:
        raise ToolError("search must be at least 2 characters")
    limit = max(1, min(limit, 50))
    like = f"%{term}%"
    rows = query("""
        SELECT sku, description, category, length_in, width_in, height_in, weight_lb
        FROM skus
        WHERE sku LIKE ? OR description LIKE ? OR category LIKE ?
        ORDER BY sku LIMIT ?
    """, (like, like, like, limit + 1))
    return {"search": term, "results": rows[:limit], "more_available": len(rows) > limit}


def get_sku_inventory(sku: str, site_id: str | None = None) -> dict:
    """
    Where a SKU's stock is: every pick and reserve location with quantity,
    average daily usage, and days of supply in the pick face and overall.

    Args:
        sku: SKU code, e.g. 'TRF-00024'.
        site_id: 'CHI1' or 'SOC1'. Omit for all sites.
    """
    sku = sku.strip().upper()
    site_id = check_site(site_id)
    info = query_one("SELECT sku, description, category FROM skus WHERE sku = ?", (sku,))
    if info is None:
        raise ToolError(f"SKU '{sku}' not found. Try find_sku.")

    rows = query("""
        SELECT i.site_id, i.location_id, l.location_type, i.quantity
        FROM inventory i JOIN locations l USING (location_id)
        WHERE i.sku = ? AND (? IS NULL OR i.site_id = ?)
        ORDER BY i.site_id, l.location_type
    """, (sku, site_id, site_id))

    by_site: dict[str, dict] = {}
    for r in rows:
        s = by_site.setdefault(r["site_id"], {"site_id": r["site_id"], "locations": [],
                                              "pick_face_qty": 0, "reserve_qty": 0})
        s["locations"].append({k: r[k] for k in ("location_id", "location_type", "quantity")})
        s["pick_face_qty" if r["location_type"] == "pick" else "reserve_qty"] += r["quantity"]

    for site, s in by_site.items():
        daily = _daily_units(site).get(sku, 0.0)
        s["avg_daily_units"] = round(daily, 1)
        s["total_qty"] = s["pick_face_qty"] + s["reserve_qty"]
        s["pick_face_days_of_supply"] = round(s["pick_face_qty"] / daily, 1) if daily else None
        s["total_days_of_supply"] = round(s["total_qty"] / daily, 1) if daily else None

    return {**info, "demand_window_days": DEMAND_WINDOW_DAYS, "by_site": list(by_site.values())}


def get_replenishment_needs(site_id: str, max_days_of_supply: float = 2.0, limit: int = 20) -> dict:
    """
    Pick faces at risk of running out: SKUs whose pick-location quantity
    covers fewer than `max_days_of_supply` days of average demand. Shows the
    reserve location to pull from, or flags SKUs with no reserve stock.

    Args:
        site_id: 'CHI1' or 'SOC1'.
        max_days_of_supply: Flag pick faces below this many days (default 2).
        limit: Maximum SKUs to return, most urgent first (default 20).
    """
    site_id = check_site(site_id)
    if max_days_of_supply <= 0:
        raise ToolError("max_days_of_supply must be positive")
    daily = _daily_units(site_id)

    rows = query("""
        SELECT i.sku, s.description, l.location_type, i.location_id, i.quantity
        FROM inventory i
        JOIN locations l USING (location_id)
        JOIN skus s ON s.sku = i.sku
        WHERE i.site_id = ?
    """, (site_id,))
    pick, reserve = {}, {}
    for r in rows:
        if r["location_type"] == "pick":
            pick[r["sku"]] = r
        else:
            reserve.setdefault(r["sku"], []).append(r)

    needs = []
    for sku, p in pick.items():
        d = daily.get(sku, 0)
        if d <= 0:
            continue
        dos = p["quantity"] / d
        if dos < max_days_of_supply:
            res = reserve.get(sku, [])
            needs.append({
                "sku": sku, "description": p["description"],
                "pick_location": p["location_id"], "pick_face_qty": p["quantity"],
                "avg_daily_units": round(d, 1), "days_of_supply": round(dos, 1),
                "reserve_locations": [{"location_id": x["location_id"], "quantity": x["quantity"]}
                                      for x in res],
                "action": ("Replenish from reserve" if res
                           else "No reserve stock: check open POs / inbound trailers"),
            })
    needs.sort(key=lambda n: n["days_of_supply"])
    return {
        "site_id": site_id,
        "threshold_days": max_days_of_supply,
        "demand_window_days": DEMAND_WINDOW_DAYS,
        "count": len(needs),
        "no_reserve_count": sum(1 for n in needs if not n["reserve_locations"]),
        "skus": needs[:limit],
    }


def get_location_contents(location_id: str) -> dict:
    """
    What is stored in one location, plus the location's details (zone, aisle,
    bay, level, travel distance, golden zone).

    Args:
        location_id: e.g. 'CHI1-B01-04-4' (site-zone+aisle-bay-level).
    """
    loc_id = location_id.strip().upper()
    loc = query_one("SELECT * FROM locations WHERE location_id = ?", (loc_id,))
    if loc is None:
        raise ToolError(f"Location '{loc_id}' not found. Format is SITE-ZoneAisle-Bay-Level, "
                        "e.g. CHI1-A03-07-2.")
    items = query("""
        SELECT i.sku, s.description, i.quantity
        FROM inventory i JOIN skus s USING (sku) WHERE i.location_id = ?
    """, (loc_id,))
    return {"location": loc, "contents": items, "is_empty": not items}


def register(mcp) -> None:
    for fn in (find_sku, get_sku_inventory, get_replenishment_needs, get_location_contents):
        mcp.tool(annotations=READ_ONLY)(fn)
