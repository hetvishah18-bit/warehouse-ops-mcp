"""
Slotting tools: SKU velocity, demand trends, and re-slotting recommendations.

Core idea (velocity-based slotting):
  - Rank SKUs by pick lines (how often a picker walks to them).
  - Rank pick locations by quality: short travel distance, golden zone
    (waist-to-shoulder height) preferred.
  - The fastest SKUs belong in the best locations. When they don't,
    pickers walk extra distance on every pick.
"""

from datetime import timedelta

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from warehouse_ops.db import FMT, check_site, parse_ts, query, query_one, utc_now

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

NON_GOLDEN_PENALTY_FT = 60      # a non-golden slot "costs" like 60 extra feet (bending/reaching)
WALK_SPEED_FT_PER_MIN = 264     # about 3 mph
FAST_MOVER_SHARE = 0.20         # top 20% of SKUs by pick lines = fast movers


# ---------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------
def _check_days(days: int) -> int:
    if not 7 <= days <= 90:
        raise ToolError("days must be between 7 and 90")
    return days


def _location_score(loc: dict) -> float:
    """Lower is better: travel distance plus a penalty for non-golden levels."""
    return loc["travel_distance_ft"] + (0 if loc["is_golden_zone"] else NON_GOLDEN_PENALTY_FT)


def _velocity(site_id: str, start, end) -> dict[str, dict]:
    """Pick lines and units per SKU between two datetimes."""
    rows = query("""
        SELECT sku, COUNT(*) AS lines, SUM(quantity) AS units
        FROM pick_lines
        WHERE site_id = ? AND picked_at >= ? AND picked_at < ?
        GROUP BY sku
    """, (site_id, start.strftime(FMT), end.strftime(FMT)))
    return {r["sku"]: r for r in rows}


def _slotting_model(site_id: str, days: int) -> dict:
    """
    Build the velocity vs location picture for a site. Every SKU with a pick
    slot gets a velocity rank and a location rank (1 = best).
    """
    now = utc_now()
    vel = _velocity(site_id, now - timedelta(days=days), now)

    slots = query("""
        SELECT i.sku, s.description, s.category, s.cube_cuft,
               l.location_id, l.zone, l.aisle, l.bay, l.level,
               l.travel_distance_ft, l.is_golden_zone
        FROM inventory i
        JOIN locations l ON l.location_id = i.location_id
        JOIN skus s ON s.sku = i.sku
        WHERE i.site_id = ? AND l.location_type = 'pick'
    """, (site_id,))

    for s in slots:
        v = vel.get(s["sku"], {"lines": 0, "units": 0})
        s["pick_lines"] = v["lines"]
        s["units"] = v["units"] or 0
        s["lines_per_day"] = round(v["lines"] / days, 2)
        s["location_score"] = _location_score(s)

    by_velocity = sorted(slots, key=lambda s: (-s["pick_lines"], s["sku"]))
    for rank, s in enumerate(by_velocity, start=1):
        s["velocity_rank"] = rank
    by_location = sorted(slots, key=lambda s: (s["location_score"], s["location_id"]))
    for rank, s in enumerate(by_location, start=1):
        s["location_rank"] = rank
    # The location a SKU "deserves" is the one whose rank equals its velocity rank
    for s in slots:
        s["ideal_location_score"] = by_location[s["velocity_rank"] - 1]["location_score"]

    return {"now": now, "slots": slots, "n": len(slots),
            "fast_cutoff": max(1, int(len(slots) * FAST_MOVER_SHARE)),
            "total_lines": sum(s["pick_lines"] for s in slots)}


def _feet_per_day(lines_per_day: float, distance_ft: float) -> float:
    """Round-trip walking distance per day for picks to one slot."""
    return 2 * lines_per_day * distance_ft


def _slot_summary(s: dict) -> dict:
    return {k: s[k] for k in ("sku", "description", "category", "location_id",
                              "travel_distance_ft", "is_golden_zone", "pick_lines",
                              "lines_per_day", "velocity_rank", "location_rank")}


# ---------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------
def get_sku_velocity(sku: str, site_id: str | None = None, days: int = 30) -> dict:
    """
    How fast a SKU is moving: pick lines, units, lines per day, its velocity
    rank at each site, and the trend versus the previous period of equal length.

    Args:
        sku: SKU code, e.g. 'TRF-00024'. Use find_sku if you only have a description.
        site_id: 'CHI1' or 'SOC1'. Omit to show every site.
        days: Analysis window in days (7-90, default 30).
    """
    sku = sku.strip().upper()
    days = _check_days(days)
    site_id = check_site(site_id)
    if query_one("SELECT 1 FROM skus WHERE sku = ?", (sku,)) is None:
        raise ToolError(f"SKU '{sku}' not found. Try find_sku to search by description.")

    now = utc_now()
    sites = [site_id] if site_id else [r["site_id"] for r in query("SELECT site_id FROM sites")]
    results = []
    for site in sites:
        model = _slotting_model(site, days)
        slot = next((s for s in model["slots"] if s["sku"] == sku), None)
        prior = _velocity(site, now - timedelta(days=2 * days), now - timedelta(days=days))
        prior_lines = prior.get(sku, {}).get("lines", 0)
        cur_lines = slot["pick_lines"] if slot else 0
        trend = (round(100 * (cur_lines - prior_lines) / prior_lines, 1)
                 if prior_lines else None)
        results.append({
            "site_id": site,
            "pick_lines": cur_lines,
            "units": slot["units"] if slot else 0,
            "lines_per_day": slot["lines_per_day"] if slot else 0,
            "velocity_rank": slot["velocity_rank"] if slot else None,
            "skus_ranked": model["n"],
            "is_fast_mover": bool(slot and slot["velocity_rank"] <= model["fast_cutoff"]),
            "prior_period_pick_lines": prior_lines,
            "trend_pct_vs_prior_period": trend,
            "pick_location": slot["location_id"] if slot else None,
        })
    return {"sku": sku, "days": days, "by_site": results}


def get_slotting_candidates(site_id: str, days: int = 30, limit: int = 10) -> dict:
    """
    Re-slotting recommendations: fast-moving SKUs sitting in poor locations
    (far away or outside the golden zone), each paired with a slow mover
    occupying a prime slot, so the two can swap.

    Estimates walking distance and time saved per day for each swap, which
    is the business case for doing the move.

    Args:
        site_id: 'CHI1' or 'SOC1'.
        days: Velocity window in days (7-90, default 30).
        limit: Maximum number of swap recommendations (default 10).
    """
    site_id = check_site(site_id)
    days = _check_days(days)
    model = _slotting_model(site_id, days)
    slots, fast_cutoff = model["slots"], model["fast_cutoff"]

    # Fast movers whose current slot is much worse than the slot they deserve
    move_in = [s for s in slots
               if s["velocity_rank"] <= fast_cutoff
               and s["location_score"] - s["ideal_location_score"] > 30]
    move_in.sort(key=lambda s: s["lines_per_day"] * (s["location_score"] - s["ideal_location_score"]),
                 reverse=True)

    # Slow movers (bottom half) sitting in prime slots (best 20% of locations)
    move_out = [s for s in slots
                if s["location_rank"] <= fast_cutoff and s["velocity_rank"] > model["n"] // 2]
    move_out.sort(key=lambda s: s["location_score"])

    swaps = []
    for fast, slow in zip(move_in, move_out):
        fast_saving = _feet_per_day(fast["lines_per_day"],
                                    fast["travel_distance_ft"] - slow["travel_distance_ft"])
        slow_cost = _feet_per_day(slow["lines_per_day"],
                                  fast["travel_distance_ft"] - slow["travel_distance_ft"])
        net_ft = round(fast_saving - slow_cost)
        reasons = []
        if slow["travel_distance_ft"] < fast["travel_distance_ft"]:
            reasons.append(f"{fast['travel_distance_ft'] - slow['travel_distance_ft']:.0f} ft closer to pick start")
        if slow["is_golden_zone"] and not fast["is_golden_zone"]:
            reasons.append("moves into the golden zone (less bending/reaching)")
        swaps.append({
            "move_fast_mover": _slot_summary(fast),
            "into_slot_held_by": _slot_summary(slow),
            "why": "; ".join(reasons) or "better location score",
            "net_walk_ft_saved_per_day": net_ft,
            "est_walk_minutes_saved_per_week": round(net_ft * 7 / WALK_SPEED_FT_PER_MIN),
        })
        if len(swaps) >= limit:
            break

    fast = [s for s in slots if s["velocity_rank"] <= fast_cutoff]
    fast_lines = sum(s["pick_lines"] for s in fast)
    fast_in_golden = sum(s["pick_lines"] for s in fast if s["is_golden_zone"])

    return {
        "site_id": site_id,
        "days": days,
        "summary": {
            "skus_with_pick_slots": model["n"],
            "fast_movers": len(fast),
            "fast_mover_share_of_pick_lines_pct": round(100 * fast_lines / model["total_lines"], 1)
                if model["total_lines"] else 0,
            "fast_mover_picks_in_golden_zone_pct": round(100 * fast_in_golden / fast_lines, 1)
                if fast_lines else 0,
            "misplaced_fast_movers": len(move_in),
            "slow_movers_in_prime_slots": len(move_out),
            "walk_ft_saved_per_day_for_listed_swaps": sum(s["net_walk_ft_saved_per_day"] for s in swaps),
        },
        "recommended_swaps": swaps,
        "note": "Walking estimates assume one round trip per pick line from pick start.",
    }


def explain_slot(sku: str, site_id: str, days: int = 30) -> dict:
    """
    Explain whether one SKU is in the right pick location: its velocity rank
    versus its location's quality rank, with a plain-language verdict.

    Args:
        sku: SKU code, e.g. 'CTL-00035'.
        site_id: 'CHI1' or 'SOC1'.
        days: Velocity window in days (7-90, default 30).
    """
    sku = sku.strip().upper()
    site_id = check_site(site_id)
    days = _check_days(days)
    model = _slotting_model(site_id, days)
    slot = next((s for s in model["slots"] if s["sku"] == sku), None)
    if slot is None:
        raise ToolError(f"SKU '{sku}' has no pick slot at {site_id}. Try find_sku.")

    n, fast_cutoff = model["n"], model["fast_cutoff"]
    vr, lr = slot["velocity_rank"], slot["location_rank"]
    if vr <= fast_cutoff and slot["location_score"] - slot["ideal_location_score"] > 30:
        verdict = "under-slotted"
        detail = ("Fast mover in a poor location. Moving it closer / into the golden zone "
                  "would cut picker travel. See get_slotting_candidates for a swap partner.")
    elif lr <= fast_cutoff and vr > n // 2:
        verdict = "over-slotted"
        detail = "Slow mover occupying a prime slot that a faster SKU could use."
    else:
        verdict = "reasonably slotted"
        detail = "Location quality roughly matches how often it is picked."

    return {
        "site_id": site_id, "days": days,
        "slot": _slot_summary(slot),
        "out_of": n,
        "level_note": "golden zone" if slot["is_golden_zone"]
                      else ("floor level" if slot["level"] == 1 else "top shelf"),
        "verdict": verdict,
        "detail": detail,
    }


def get_demand_trends(site_id: str, days: int = 30, min_lines: int = 10, limit: int = 10) -> dict:
    """
    SKUs whose demand changed sharply: "rising stars" picked much more often
    in the recent window than before, and "fading" SKUs picked much less.
    Rising stars may need a better slot; fading ones may be wasting a prime slot.

    Compares the pick-lines-per-day rate in the last `days` days to the rate
    over all earlier history.

    Args:
        site_id: 'CHI1' or 'SOC1'.
        days: Recent window in days (7-60, default 30).
        min_lines: Ignore SKUs with fewer pick lines than this in both periods (default 10).
        limit: Maximum SKUs per list (default 10).
    """
    site_id = check_site(site_id)
    if not 7 <= days <= 60:
        raise ToolError("days must be between 7 and 60")

    now = utc_now()
    first = query_one("SELECT MIN(picked_at) AS t FROM pick_lines WHERE site_id = ?", (site_id,))
    if not first or not first["t"]:
        raise ToolError("No pick history for this site.")
    history_start = parse_ts(first["t"])
    cutoff = now - timedelta(days=days)
    prior_days = (cutoff - history_start).total_seconds() / 86400
    if prior_days < 7:
        raise ToolError("Not enough earlier history to compare. Use a smaller 'days' value.")

    recent = _velocity(site_id, cutoff, now)
    prior = _velocity(site_id, history_start, cutoff)
    model = {s["sku"]: s for s in _slotting_model(site_id, days)["slots"]}

    rows = []
    for sku in set(recent) | set(prior):
        r_lines = recent.get(sku, {}).get("lines", 0)
        p_lines = prior.get(sku, {}).get("lines", 0)
        if max(r_lines, p_lines) < min_lines:
            continue
        r_rate, p_rate = r_lines / days, p_lines / prior_days
        change = round(100 * (r_rate - p_rate) / p_rate, 1) if p_rate else None
        slot = model.get(sku, {})
        rows.append({
            "sku": sku, "description": slot.get("description"),
            "recent_lines_per_day": round(r_rate, 2), "prior_lines_per_day": round(p_rate, 2),
            "change_pct": change, "velocity_rank_now": slot.get("velocity_rank"),
            "pick_location": slot.get("location_id"),
            "travel_distance_ft": slot.get("travel_distance_ft"),
            "is_golden_zone": slot.get("is_golden_zone"),
        })

    rising = sorted([r for r in rows if r["change_pct"] is None or r["change_pct"] >= 50],
                    key=lambda r: r["recent_lines_per_day"] - r["prior_lines_per_day"], reverse=True)
    fading = sorted([r for r in rows if r["change_pct"] is not None and r["change_pct"] <= -50],
                    key=lambda r: r["recent_lines_per_day"] - r["prior_lines_per_day"])
    return {
        "site_id": site_id,
        "recent_window_days": days,
        "prior_window_days": round(prior_days),
        "rising": rising[:limit],
        "fading": fading[:limit],
    }


def register(mcp) -> None:
    for fn in (get_sku_velocity, get_slotting_candidates, explain_slot, get_demand_trends):
        mcp.tool(annotations=READ_ONLY)(fn)
