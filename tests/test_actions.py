"""Tests for the Phase 4 action (write) tools."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from warehouse_ops.tools import actions, yard


def _on_site_trailer(offset: int = 0) -> str:
    return yard.get_yard_status(limit=50)["trailers"][offset]["trailer_id"]


def test_flag_list_resolve_roundtrip():
    tid = _on_site_trailer(0)
    flag = actions.flag_trailer_for_pull(tid, "Over SOP dwell limit, needs a door",
                                         priority="urgent", flagged_by="Hetvi")
    assert flag["status"] == "flagged"

    open_flags = actions.list_trailer_flags()["flags"]
    assert any(f["flag_id"] == flag["flag_id"] for f in open_flags)

    done = actions.resolve_trailer_flag(flag["flag_id"], "Moved to door 4", resolved_by="Hetvi")
    assert done["status"] == "resolved"
    assert all(f["flag_id"] != flag["flag_id"] for f in actions.list_trailer_flags()["flags"])
    history = actions.list_trailer_flags(include_resolved=True)["flags"]
    resolved = next(f for f in history if f["flag_id"] == flag["flag_id"])
    assert resolved["resolved_by"] == "Hetvi" and resolved["resolution_note"] == "Moved to door 4"


def test_no_duplicate_open_flags():
    tid = _on_site_trailer(1)
    actions.flag_trailer_for_pull(tid, "First flag for this trailer")
    with pytest.raises(ToolError, match="already has open flag"):
        actions.flag_trailer_for_pull(tid, "Second flag for this trailer")


def test_reason_must_be_meaningful():
    with pytest.raises(ToolError, match="10-300"):
        actions.flag_trailer_for_pull(_on_site_trailer(2), "move it")


def test_departed_trailers_cannot_be_flagged():
    from warehouse_ops.db import query_one
    gone = query_one("SELECT trailer_id FROM trailers WHERE status = 'departed' LIMIT 1")
    with pytest.raises(ToolError, match="departed"):
        actions.flag_trailer_for_pull(gone["trailer_id"], "Should not be allowed at all")


def test_cannot_resolve_twice():
    tid = _on_site_trailer(3)
    flag = actions.flag_trailer_for_pull(tid, "Empty trailer, call carrier")
    actions.resolve_trailer_flag(flag["flag_id"], "Carrier picked up")
    with pytest.raises(ToolError, match="already resolved"):
        actions.resolve_trailer_flag(flag["flag_id"], "Carrier picked up again")
