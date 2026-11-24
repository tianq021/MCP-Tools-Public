from __future__ import annotations

from typing import Any, Protocol


class Tool(Protocol):
    """Contract implemented by every model-callable local tool."""

    name: str
    schema: dict[str, Any]

    def execute(self, arguments: dict[str, Any] | str) -> dict[str, Any]: ...

