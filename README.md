# Warehouse Ops MCP

An MCP server that lets Claude answer distribution center questions about yard status, dock doors, appointments, slotting and inventory, using a realistic synthetic dataset.

> Work in progress. Full write-up coming in Phase 5.

## Quick start

```bash
uv sync
uv run python data/seed.py     # build data/warehouse.db
uv run python data/verify.py   # sanity-check the data
uv run pytest                  # run the tests
```

## Tools

| Area | Tool | Answers |
|---|---|---|
| Sites | `list_sites` | What sites exist? |
| Yard | `get_yard_status` | What's in the yard right now? |
| Yard | `get_long_dwell_trailers` | Which trailers have been here too long? |
| Yard | `get_trailer_history` | Where has this trailer been? |
| Docks | `get_door_status` | Which doors are open, occupied, or down? |
| Docks | `get_dock_utilization` | How busy were the doors on a given day? |
| Docks | `get_upcoming_appointments` | What's arriving next? Any door double-booked? |
| Slotting | `get_sku_velocity` | How fast is this SKU moving, and is it trending? |
| Slotting | `get_slotting_candidates` | Which fast movers are in bad slots, and what should they swap with? |
| Slotting | `explain_slot` | Is this SKU in the right location? |
| Slotting | `get_demand_trends` | Which SKUs are rising or fading? |
| Inventory | `find_sku` | Find a SKU by code or description |
| Inventory | `get_sku_inventory` | Where is the stock, and how many days of supply? |
| Inventory | `get_replenishment_needs` | Which pick faces are about to run out? |
| Inventory | `get_location_contents` | What's in this location? |
| Actions | `list_trailer_flags` | What's on the yard team's worklist? |
| Actions | `flag_trailer_for_pull` | Flag a trailer to be moved (write, local mode only) |
| Actions | `resolve_trailer_flag` | Close a flag once handled (write, local mode only) |

## Running modes

| Mode | Command | Writes |
|---|---|---|
| Local (Claude Desktop / Code) | `uv run warehouse-ops-mcp` | On |
| Remote (HTTP) | `uv run warehouse-ops-mcp --transport http` then connect to `http://localhost:8000/mcp` | Off |

Override with `WAREHOUSE_ALLOW_WRITES=true` or `false`. Write tools are validated, never delete data, and record who did what and when.

## Resources

- `warehouse://sites/{site_id}/layout`: zones, dock doors, yard
- `warehouse://sops/yard-management`: dwell thresholds and door priority rules
- `warehouse://sops/slotting`: slotting principles and move limits

## Prompts

- `daily_yard_briefing(site_id)`: shift briefing with prioritized actions
- `slotting_review(site_id, days)`: monthly re-slot plan with walking time saved
