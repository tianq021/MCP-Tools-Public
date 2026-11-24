"""史莱姆牧场 2 MCP server（stdio）。

职责边界（与 1 代相同）：
    MCP client (DSH)  ──stdio──▶  本进程  ──HTTP(127.0.0.1:18762+token)──▶  游戏内 MelonLoader 插件
本进程只做 3 件事：读 tools.json 注册工具 / 转发调用 / 把错误变成结构化文本。
业务逻辑全部在游戏内 mod（加工具时改 C# handler + tools.json 各一处）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from typing import Annotated

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bridge_client import BridgeError, call, health  # noqa: E402
from chat_history import read_history
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.fastmcp.tools import ToolManager
from mcp.server.lowlevel import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import ToolAnnotations  # noqa: E402
from pydantic import Field  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_PATH = os.path.join(HERE, "tools.json")

PY_TYPES = {"int": int, "float": float, "bool": bool, "str": str}
ACTION_ARGS = (
    {"name": "dry_run", "type": "bool", "default": True,
     "description": "默认预演。显式设为 false 后才会修改当前存档，且必须提供 request_id。"},
    {"name": "request_id", "type": "str", "default": "", "max_length": 128,
     "description": "执行时填写本次动作的唯一标识。重试同一动作保留该值及原参数。预演可留空。"},
)


def log(message: str) -> None:
    """日志必须走 stderr；stdout 是 MCP 协议通道。"""
    print(f"[slime_rancher2_mcp] {message}", file=sys.stderr, flush=True)


def load_tools() -> list[dict]:
    with open(TOOLS_PATH, "r", encoding="utf-8") as f:
        spec = json.load(f)
    return spec.get("tools", [])


def _error_envelope(code: str, message: str, tool: str, request_id: str | None = None) -> dict:
    return {
        "ok": False,
        "tool": tool,
        "request_id": request_id,
        "error": {"code": code, "message": message},
    }


def _make_runner(spec: dict):
    """从工具目录生成 MCP 可检查的签名和参数约束。"""
    name = spec["name"]
    args = list(spec.get("args", []))
    write = spec.get("kind") == "write"
    if write:
        declared = {arg["name"] for arg in args}
        args = [*args, *[arg for arg in ACTION_ARGS if arg["name"] not in declared]]
    params = []
    for arg in args:
        constraints = {key: arg[key] for key in ("ge", "le", "min_length", "max_length") if key in arg}
        annotation = Annotated[
            PY_TYPES[arg["type"]], Field(description=arg.get("description"), strict=True, **constraints)
        ]
        params.append(inspect.Parameter(
            arg["name"], inspect.Parameter.KEYWORD_ONLY, annotation=annotation,
            default=inspect.Parameter.empty if arg.get("required") else arg["default"],
        ))

    def runner(**payload):
        request_id = str(payload.pop("request_id", "") or "").strip() or None
        if write and not payload.get("dry_run", True) and request_id is None:
            return _error_envelope("REQUEST_ID_REQUIRED", "执行需要一个唯一 request_id。", name)
        return _run_tool(name, payload, request_id)

    runner.__name__ = name
    runner.__signature__ = inspect.Signature(params, return_annotation=dict)
    return runner


def _run_tool(name: str, payload: dict, request_id: str | None = None) -> dict:
    try:
        if name == "sr2_health":
            return health()
        if name == "sr2_chat_history":
            return {"ok": True, "data": read_history(**payload)}
        return call(name, payload, request_id)
    except BridgeError as exc:
        log(f"{name} bridge error: {exc}")
        return _error_envelope("BRIDGE_UNAVAILABLE", str(exc), name, request_id)
    except Exception as exc:  # 防御：任何意外都返回结构化错误，不让 MCP 调用崩掉
        log(f"{name} unexpected error: {exc!r}")
        return _error_envelope("INTERNAL_ERROR", str(exc), name, request_id)


class CatalogServer(FastMCP):
    session = None

    async def list_tools(self):
        self.session = self.get_context().session
        return await super().list_tools()

    async def run_stdio_async(self):
        async def monitor():
            while True:
                await asyncio.sleep(1)
                if self.session is None:
                    continue
                try:
                    revision = os.stat(TOOLS_PATH).st_mtime_ns
                    if revision != self.revision:
                        manager = make_catalog(load_tools())
                        self._tool_manager = manager
                        await self.session.send_tool_list_changed()
                        self.revision = revision
                except Exception as exc:
                    log(f"tool catalog refresh failed: {exc}")

        async with stdio_server() as (reader, writer):
            options = self._mcp_server.create_initialization_options(NotificationOptions(tools_changed=True))
            watcher = asyncio.create_task(monitor())
            try:
                await self._mcp_server.run(reader, writer, options)
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)


def build_server() -> FastMCP:
    server = CatalogServer(
        "slime_rancher2",
        instructions=(
            "史莱姆牧场 2（Unity 6 / IL2CPP + MelonLoader 桥接）的查询与操作工具。"
            "先调用 sr2_health 确认桥接、游戏目录与当前状态；数据来源以工具返回为准，不要臆造。"
            "写操作默认预演，执行时必须提供独立 request_id，重试保留原 ID 和参数。"
        ),
    )
    server.revision = os.stat(TOOLS_PATH).st_mtime_ns
    server._tool_manager = make_catalog(load_tools())
    return server


def make_catalog(specs: list[dict]) -> ToolManager:
    manager = ToolManager()
    names = [spec["name"] for spec in specs]
    if not names or len(names) != len(set(names)):
        raise ValueError("empty or duplicate tool names")
    for spec in specs:
        fn = _make_runner(spec)
        write = spec.get("kind") == "write"
        manager.add_tool(
            fn,
            name=spec["name"],
            title=spec.get("title"),
            description=spec.get("description", ""),
            annotations=ToolAnnotations(
                readOnlyHint=not write, destructiveHint=write,
                idempotentHint=not write, openWorldHint=False,
            ),
        )
    log(f"registered {len(specs)} tools: {', '.join(s['name'] for s in specs)}")
    return manager


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
