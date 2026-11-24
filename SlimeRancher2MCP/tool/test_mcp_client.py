"""MCP stdio 客户端自测：启动 mcp\\server.py，列工具并逐个调用。

用法：
    python -B tool\\test_mcp_client.py
游戏未启动时所有工具应返回结构化 BRIDGE_UNAVAILABLE（不是崩溃）。
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


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", SERVER],
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools({len(names)}): {', '.join(names)}")
            expected = {"sr2_health", "sr2_gameinfo", "sr2_saves", "sr2_echo", "sr2_minimap", "sr2_player_position",
                        "sr2_teleport", "sr2_real_time", "sr2_market"}
            missing = expected - set(names)
            print("missing:", sorted(missing) if missing else "none")

            failures = 0
            for name in names:
                args: dict = {}
                if name == "sr2_saves":
                    args = {"limit": 3}
                if name == "sr2_echo":
                    args = {"text": "stdio自测"}
                if name == "sr2_teleport":
                    args = {"x": 1.0, "y": 2.0, "z": 3.0, "dry_run": True}
                result = await session.call_tool(name, args)
                text = "".join(
                    block.text for block in result.content if getattr(block, "type", "") == "text"
                )
                payload = json.loads(text) if text.startswith("{") else {"raw": text}
                code = (payload.get("error") or {}).get("code")
                ok = payload.get("ok")
                print(f"  {name:14s} ok={ok} code={code}")
                if code not in (None, "BRIDGE_UNAVAILABLE"):
                    failures += 1
            print("\nRESULT:", "PASS" if failures == 0 and not missing else "FAIL")
            return 0 if failures == 0 and not missing else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
