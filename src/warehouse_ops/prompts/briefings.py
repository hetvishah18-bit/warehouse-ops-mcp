"""Prompt templates: one-click workflows that tell Claude which tools to chain."""


def daily_yard_briefing(site_id: str) -> str:
    """Morning yard and dock briefing for a site: what needs action this shift."""
    site = site_id.strip().upper()
    return f"""Prepare the daily yard and dock briefing for site {site}.

Steps:
1. Read the resource warehouse://sops/yard-management for dwell thresholds and door priority rules.
2. Call get_yard_status for {site}.
3. Call get_long_dwell_trailers for {site} (48 hours).
4. Call get_door_status for {site}.
5. Call get_dock_utilization for {site} (yesterday).
6. Call get_upcoming_appointments for {site} (next 24 hours).

Write the briefing for a shift supervisor:
- Headline: 3-4 key numbers (trailers on site, over 48 h, doors available, appointments today).
- Urgent actions: a numbered list ordered by the SOP's door priority rules. Name trailers,
  carriers and yard spots, and state the specific action for each.
- Dock outlook: yesterday's utilization, idle doors, any double-booked doors to fix.
- Keep it under 250 words. Convert UTC times to the site's local time zone (see list_sites).
"""


def slotting_review(site_id: str, days: int = 30) -> str:
    """Monthly slotting review: top re-slot moves, demand shifts and replenishment risks."""
    site = site_id.strip().upper()
    return f"""Run a slotting review for site {site} using the last {days} days of picks.

Steps:
1. Read the resource warehouse://sops/slotting for slotting rules and the max moves per shift.
2. Call get_slotting_candidates for {site} with days={days}.
3. Call get_demand_trends for {site} with days={days}.
4. Call get_replenishment_needs for {site}.

Write the review for an operations manager:
- Summary: how well fast movers are slotted today (golden-zone share, misplaced fast movers).
- Recommended moves: the top swaps (respect the SOP's per-shift limit), each with SKU,
  from/to location, and walking time saved per week. Give the total savings.
- Demand shifts: rising SKUs that deserve a better slot, fading SKUs holding prime slots.
- Replenishment risks: pick faces under 2 days of supply, flagging any with no reserve.
- End with a short, prioritized action plan.
"""


def register(mcp) -> None:
    mcp.prompt(title="Daily yard briefing")(daily_yard_briefing)
    mcp.prompt(title="Slotting review")(slotting_review)
