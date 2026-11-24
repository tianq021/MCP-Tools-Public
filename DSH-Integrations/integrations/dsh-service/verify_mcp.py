"""Exercise the real stdio MCP protocol against an already-running DSH."""
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("server.py")
    params = StdioServerParameters(command=sys.executable, args=["-B", str(server)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert "ensure_running" in initialized.instructions
            tools = (await session.list_tools()).tools
            assert {tool.name for tool in tools} == {"status", "ensure_running"}
            summary = {"server": initialized.serverInfo.name, "tools": [t.name for t in tools]}
            for name in ("status", "ensure_running"):
                result = await session.call_tool(name, {})
                assert not result.isError, name + " failed"
                value = json.loads(next(item.text for item in result.content if item.type == "text"))
                assert value["ready"], name + " is not ready"
                if name == "ensure_running":
                    assert value["action"] == "already_running"
                    assert value["startup_requested"] is False
                summary[name] = value
            print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=60))
