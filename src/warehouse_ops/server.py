"""
Warehouse Ops MCP server.

Local mode (Claude Desktop / Claude Code launch it over stdio). Writes ON by default:
    uv run warehouse-ops-mcp

Remote mode (HTTP, for cloud hosting and claude.ai). Writes OFF by default:
    uv run warehouse-ops-mcp --transport http --port 8000
    -> MCP endpoint at http://localhost:8000/mcp

Override the write default with WAREHOUSE_ALLOW_WRITES=true or false.
"""

import argparse
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from warehouse_ops.db import query, writes_enabled
from warehouse_ops.prompts import briefings
from warehouse_ops.resources import layout, sops
from warehouse_ops.tools import actions, docks, inventory, slotting, yard

VERSION = "0.4.0"


def list_sites() -> dict:
    """List all distribution center sites with their codes, locations and time zones."""
    return {"sites": query("""
        SELECT s.site_id, s.name, s.city, s.state, s.timezone,
               (SELECT COUNT(*) FROM dock_doors d WHERE d.site_id = s.site_id) AS dock_doors
        FROM sites s ORDER BY s.site_id
    """)}


def create_server(allow_writes: bool) -> MCPServer:
    """Build the server. Action tools are only registered when writes are allowed."""
    mode = ("Action tools can flag trailers for pull; confirm with the user before any write."
            if allow_writes else "This server is read-only.")
    server = MCPServer(
        name="warehouse-ops",
        title="Warehouse Ops",
        version=VERSION,
        instructions=(
            "Tools for distribution center operations: yard trailers, dock doors, "
            "appointments, SKU velocity, slotting and inventory. Call list_sites first "
            "if you don't know the site codes. SOPs are available as resources under "
            "warehouse://sops/ and site layouts under warehouse://sites/{site_id}/layout. "
            f"All timestamps are UTC. Data is synthetic, for demonstration. {mode}"
        ),
    )
    server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(list_sites)
    yard.register(server)
    docks.register(server)
    slotting.register(server)
    inventory.register(server)
    actions.register(server, allow_writes=allow_writes)
    layout.register(server)
    sops.register(server)
    briefings.register(server)
    return server


# Default instance (local mode), used by `mcp dev` and the tests
mcp = create_server(allow_writes=writes_enabled(default=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Warehouse Ops MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1",
                        help="HTTP bind address (use 0.0.0.0 in a container)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = parser.parse_args()

    if args.transport == "stdio":
        create_server(allow_writes=writes_enabled(default=True)).run()
    else:
        server = create_server(allow_writes=writes_enabled(default=False))
        server.run("streamable-http", host=args.host, port=args.port, stateless_http=True)


if __name__ == "__main__":
    main()
