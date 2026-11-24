from __future__ import annotations

from typing import Any, Iterable

from .protocol import Tool


class ToolRegistry:
    """Own tool registration, schemas and name-based execution dispatch."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        name = str(getattr(tool, "name", "")).strip()
        if not name:
            raise ValueError("Tool name must not be empty")
        if name in self._tools:
            raise ValueError(f"Tool is already registered: {name}")
        schema_name = tool.schema.get("function", {}).get("name")
        if schema_name != name:
            raise ValueError(f"Tool schema name does not match tool name: {name}")
        self._tools[name] = tool

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema for tool in self._tools.values()]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Tool is not registered: {name}")
        return tool

    def execute(self, name: str, arguments: dict[str, Any] | str) -> dict[str, Any]:
        tool = self.get(name)
        if tool is None:
            return {"ok": False, "error": f"未知工具：{name}"}
        try:
            return tool.execute(arguments)
        except Exception as error:
            return {
                "ok": False,
                "error": f"工具执行失败：{type(error).__name__}: {error}",
            }
