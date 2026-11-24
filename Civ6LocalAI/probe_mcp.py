"""Validate the installed MCP tools without connecting to Civ6 or running a model."""
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def main():
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(command=r"E:\Civ6\civ6-mcp\.venv\Scripts\python.exe",
                                  args=["-B", "-X", "utf8", str(ROOT / "civ6_gateway.py")],
                                  cwd=str(ROOT), env=env)
    (ROOT / "evidence").mkdir(exist_ok=True)
    with (ROOT / "evidence" / "mcp-probe.stderr.log").open("w", encoding="utf-8") as err:
        async with stdio_client(params, errlog=err) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                names = sorted(t.name for t in tools)
                assert names == ["civ6_call", "civ6_describe_tool", "civ6_list_tools"]
                catalog = await session.call_tool("civ6_list_tools", {})
                assert not catalog.isError
                detail = await session.call_tool("civ6_describe_tool", {"name": "get_game_overview"})
                assert not detail.isError
                assert "inputSchema" in " ".join(c.text for c in detail.content if c.type == "text")
                blocked = await session.call_tool("civ6_call", {"name": "kill_game", "arguments": {}})
                assert blocked.isError
                schema_chars = len(json.dumps([t.model_dump() for t in tools], ensure_ascii=False))
                result = {"handshake": "passed", "tools": names, "schema_chars": schema_chars,
                          "catalog": "passed", "schema_lookup": "passed", "lifecycle_command_rejected": "passed",
                          "game_connection_tested": False, "model_inference_tested": False}
                (ROOT / "evidence" / "mcp-probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(result, ensure_ascii=False))


asyncio.run(main())
