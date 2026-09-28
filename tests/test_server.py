"""End-to-end tests through a real MCP client connected in-process."""

import pytest
from mcp import Client

from warehouse_ops.server import mcp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_everything_is_registered():
    async with Client(mcp) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert {"list_sites", "get_yard_status", "get_slotting_candidates",
                "get_replenishment_needs"} <= tools
        assert len(tools) == 15
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert prompts == {"daily_yard_briefing", "slotting_review"}


async def test_resources_are_readable():
    async with Client(mcp) as client:
        layout = await client.read_resource("warehouse://sites/CHI1/layout")
        assert "Dock doors" in layout.contents[0].text
        sop = await client.read_resource("warehouse://sops/yard-management")
        assert "Dwell thresholds" in sop.contents[0].text


async def test_tool_errors_reach_the_model():
    async with Client(mcp) as client:
        result = await client.call_tool("get_door_status", {"site_id": "NYC9"})
        assert result.is_error
        assert "Valid sites" in result.content[0].text


async def test_prompt_mentions_the_site():
    async with Client(mcp) as client:
        prompt = await client.get_prompt("slotting_review", {"site_id": "soc1"})
        assert "SOC1" in prompt.messages[0].content.text
