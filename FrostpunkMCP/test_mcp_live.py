"""Opt-in integration test against the existing save-blocked test city."""
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable,
        args=[str(Path(__file__).with_name("frostpunk_mcp.py"))])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == {"get_state", "pause", "advance", "start_research", "get_city", "get_abilities",
                             "set_ability_enabled", "set_building_enabled", "get_workforce", "set_workplace", "get_tasks"}, names

            async def call(name, args=None):
                result = await session.call_tool(name, args or {})
                assert not result.isError, result
                data = result.structuredContent
                assert isinstance(data, dict), result
                return data

            initial = await call("get_state", {"include_catalog": True})
            assert initial["ready"] and initial["save_requests_blocked"]
            assert initial["catalog_count"] == len(initial["catalog"])
            assert {'Coal', 'Wood', 'Steel', 'Raw Food', 'Steam Cores'} <= initial['resources'].keys()
            assert all(type(value) is int for value in initial['resources'].values())
            assert (await call("pause"))["paused"]
            city=await call('get_city')
            workshop=next(x for x in city['entities'] if x['name'].startswith('EngineersLab'))
            staff=await call('get_workforce',{'building_id':workshop['id']})
            assert staff['count']==len(staff['assigned'])
            invalid_id=await session.call_tool('set_workplace',{'agent_id':'invalid','building_id':workshop['id']})
            assert invalid_id.isError
            before = await call("get_state")
            invalid = await session.call_tool("advance", {"seconds": 11})
            assert invalid.isError
            assert (await call("get_state"))["clock_raw"] == before["clock_raw"]
            advanced = await call("advance", {"seconds": 0.25})
            assert advanced["after"]["paused"]
            assert advanced["advanced"], "simulation did not advance (loading or unfocused?)"
            if initial["registered_research_buildings"] == 0 and initial["active_technology"] is None:
                before_research = await call('get_state')
                refused = await call("start_research", {"technology": "E 1_Beacon"})
                assert refused["accepted"] is False
                assert refused["state"]["active_technology"] is None
                assert refused['state']['resources'] == before_research['resources']
            final = await call("get_state")
            assert final["paused"]
            print(json.dumps({"passed": True, "tools": sorted(names),
                              "pid": final["pid"], "paused": final["paused"],
                              "resources": final['resources'],
                              "clock_advanced": advanced["advanced"]}))


if __name__ == "__main__":
    asyncio.run(main())
