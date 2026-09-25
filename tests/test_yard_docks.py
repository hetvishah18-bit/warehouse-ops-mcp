"""Tests for the Phase 2 yard and dock tools."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from warehouse_ops.tools import docks, yard


def test_yard_status_summary_matches_list():
    result = yard.get_yard_status(site_id="CHI1", limit=1000)
    assert result["trailers_total"] == len(result["trailers"])
    assert result["summary_by_site"]["CHI1"]["total_on_site"] == result["trailers_total"]
    assert all(t["status"] != "departed" for t in result["trailers"])


def test_site_code_is_case_insensitive():
    assert yard.get_yard_status(site_id="chi1")["summary_by_site"].keys() == {"CHI1"}


def test_unknown_site_gives_helpful_error():
    with pytest.raises(ToolError, match="Valid sites"):
        yard.get_yard_status(site_id="NYC9")


def test_long_dwell_respects_threshold_and_order():
    result = yard.get_long_dwell_trailers(min_hours=24)
    dwell = [t["dwell_hours"] for t in result["trailers"]]
    assert all(d >= 24 for d in dwell)
    assert dwell == sorted(dwell, reverse=True)
    assert all("suggested_action" in t for t in result["trailers"])


def test_trailer_history_moves_are_in_order():
    any_trailer = yard.get_yard_status(limit=1)["trailers"][0]
    history = yard.get_trailer_history(any_trailer["trailer_id"])
    times = [m["moved_at"] for m in history["moves"]]
    assert times == sorted(times)
    assert history["moves"][0]["move_type"] == "gate_in"


def test_door_status_counts_all_doors():
    result = docks.get_door_status("SOC1")
    total = sum(sum(c.values()) for c in result["counts_by_door_type"].values())
    assert total == len(result["doors"]) == 30


def test_dock_utilization_is_between_0_and_100():
    result = docks.get_dock_utilization("CHI1", door_type="inbound")
    assert 0 <= result["overall_utilization_pct"] <= 100
    assert all(0 <= d["utilization_pct"] <= 100 for d in result["doors"])
    assert all(d["door_type"] == "inbound" for d in result["doors"])


def test_dock_utilization_rejects_bad_date():
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        docks.get_dock_utilization("CHI1", date="Sept 3")


def test_upcoming_appointments_window():
    result = docks.get_upcoming_appointments("CHI1", hours=48)
    start, end = result["window_utc"]
    assert all(start <= a["scheduled_start"] <= end for a in result["appointments"])
