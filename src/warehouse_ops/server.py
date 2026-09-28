"""
Warehouse Ops MCP server.

Run locally over stdio (how Claude Desktop and Claude Code launch it):
    uv run warehouse-ops-mcp
"""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from warehouse_ops.db import query
from warehouse_ops.prompts import briefings
from warehouse_ops.resources import layout, sops
from warehouse_ops.tools import docks, inventory, slotting, yard

mcp = MCPServer(
    name="warehouse-ops",
    title="Warehouse Ops",
    version="0.3.0",
    instructions=(
        "Tools for distribution center operations: yard trailers, dock doors, "
        "appointments, SKU velocity, slotting and inventory. Call list_sites first "
        "if you don't know the site codes. SOPs are available as resources under "
        "warehouse://sops/ and site layouts under warehouse://sites/{site_id}/layout. "
        "All timestamps are UTC. Data is synthetic, for demonstration."
    ),
)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def list_sites() -> dict:
    """List all distribution center sites with their codes, locations and time zones."""
    return {"sites": query("""
        SELECT s.site_id, s.name, s.city, s.state, s.timezone,
               (SELECT COUNT(*) FROM dock_doors d WHERE d.site_id = s.site_id) AS dock_doors
        FROM sites s ORDER BY s.site_id
    """)}


yard.register(mcp)
docks.register(mcp)
slotting.register(mcp)
inventory.register(mcp)
layout.register(mcp)
sops.register(mcp)
briefings.register(mcp)


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
