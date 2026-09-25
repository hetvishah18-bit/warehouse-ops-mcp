# Warehouse Ops MCP

An MCP server that lets Claude answer distribution center questions about yard status, dock doors, appointments, and (coming soon) slotting and inventory, using a realistic synthetic dataset.

> Work in progress. Full write-up coming in Phase 5.

## Quick start

```bash
uv sync
uv run python data/seed.py     # build data/warehouse.db
uv run python data/verify.py   # sanity-check the data
uv run pytest                  # run the tests
```

## Tools (Phase 2)

| Tool | Answers |
|---|---|
| `list_sites` | What sites exist? |
| `get_yard_status` | What's in the yard right now? |
| `get_long_dwell_trailers` | Which trailers have been here too long? |
| `get_trailer_history` | Where has this trailer been? |
| `get_door_status` | Which doors are open, occupied, or down? |
| `get_dock_utilization` | How busy were the doors on a given day? |
| `get_upcoming_appointments` | What's arriving next, and is any door double-booked? |
