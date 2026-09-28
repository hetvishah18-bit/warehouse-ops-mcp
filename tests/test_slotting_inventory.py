"""Tests for the Phase 3 slotting and inventory tools."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from warehouse_ops.tools import inventory, slotting


def test_slotting_candidates_move_fast_into_better_slots():
    result = slotting.get_slotting_candidates("CHI1", days=30, limit=5)
    assert result["summary"]["skus_with_pick_slots"] > 0
    for swap in result["recommended_swaps"]:
        fast, slow = swap["move_fast_mover"], swap["into_slot_held_by"]
        assert fast["velocity_rank"] < slow["velocity_rank"]
        assert fast["location_rank"] > slow["location_rank"]
        assert swap["net_walk_ft_saved_per_day"] > 0


def test_slotting_rejects_bad_days():
    with pytest.raises(ToolError, match="between 7 and 90"):
        slotting.get_slotting_candidates("CHI1", days=365)


def test_explain_slot_gives_a_verdict():
    top = slotting.get_slotting_candidates("CHI1")["recommended_swaps"]
    sku = top[0]["move_fast_mover"]["sku"] if top else "TRF-00001"
    result = slotting.explain_slot(sku, "CHI1")
    assert result["verdict"] in {"under-slotted", "over-slotted", "reasonably slotted"}


def test_sku_velocity_unknown_sku():
    with pytest.raises(ToolError, match="find_sku"):
        slotting.get_sku_velocity("NOPE-123")


def test_demand_trends_directions():
    result = slotting.get_demand_trends("CHI1", days=14, min_lines=3)
    assert all(r["change_pct"] is None or r["change_pct"] >= 50 for r in result["rising"])
    assert all(r["change_pct"] <= -50 for r in result["fading"])


def test_find_sku_is_case_insensitive():
    upper = inventory.find_sku("TROFFER", limit=50)["results"]
    lower = inventory.find_sku("troffer", limit=50)["results"]
    assert upper == lower and len(upper) > 0


def test_sku_inventory_totals_add_up():
    sku = inventory.find_sku("TRF")["results"][0]["sku"]
    for site in inventory.get_sku_inventory(sku)["by_site"]:
        assert site["total_qty"] == sum(l["quantity"] for l in site["locations"])


def test_replenishment_below_threshold_and_sorted():
    result = inventory.get_replenishment_needs("CHI1", max_days_of_supply=3)
    dos = [s["days_of_supply"] for s in result["skus"]]
    assert all(d < 3 for d in dos)
    assert dos == sorted(dos)


def test_location_contents_bad_id():
    with pytest.raises(ToolError, match="Format is"):
        inventory.get_location_contents("CHI1-ZZZ")
