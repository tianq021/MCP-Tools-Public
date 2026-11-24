"""用官方 MCP 客户端验证 bl2 服务端能正常握手、列工具、调工具。

用法：python scripts\\test_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PYTHON = r"C:\Program Files\Python310\python.exe"
CWD = r"E:\Borderlands2MCP"


async def main() -> int:
    params = StdioServerParameters(
        command=PYTHON,
        args=["-B", "-m", "bl2toolkit.mcp_server"],
        cwd=CWD,
        env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"工具数量: {len(names)}")
            print("工具列表:", ", ".join(names))

            async def call(name, args):
                result = await session.call_tool(name, args)
                text = ""
                for item in result.content:
                    if hasattr(item, "text"):
                        text += item.text
                return text

            print("\n--- bl2_status ---")
            print((await call("bl2_status", {}))[:400])

            print("\n--- bl2_describe_item_code ---")
            print(await call("bl2_describe_item_code", {
                "code": "BL2(hwAAAABc0wCE5wRABgGF6sNohWEQowQKQgyEGB4wEGIcxNjEkgnl)"}))

            print("\n--- bl2_search_gun_library(蜜蜂) ---")
            print((await call("bl2_search_gun_library", {"query": "蜜蜂", "limit": 2}))[:400])

            print("\n--- bl2_set_skill_points (dry run, 副本) ---")
            print(await call("bl2_set_skill_points", {
                "slot": r".local\tmp\work.sav", "points": 150, "dry_run": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
