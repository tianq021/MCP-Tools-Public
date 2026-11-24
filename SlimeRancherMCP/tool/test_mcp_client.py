"""MCP stdio 客户端自测：启动 mcp\\server.py，列工具、调 sr_ping / sr_health。

用法：
    python tool\\test_mcp_client.py
游戏未启动时 sr_ping 应返回 BRIDGE_UNAVAILABLE（结构化错误），sr_health 同理。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "mcp", "server.py")


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[SERVER],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools({len(names)}): {', '.join(names)}")
            for name in names:
                schema = json.dumps(
                    next(t.inputSchema for t in tools.tools if t.name == name),
                    ensure_ascii=False,
                )
                print(f"  {name} schema={schema}")

            for tool, args in (("sr_ping", {}), ("sr_health", {}), ("sr_state", {})):
                result = await session.call_tool(tool, args)
                text = "".join(
                    block.text for block in result.content if getattr(block, "type", "") == "text"
                )
                print(f"\n--- {tool} ---\n{text[:800]}")


if __name__ == "__main__":
    asyncio.run(main())
