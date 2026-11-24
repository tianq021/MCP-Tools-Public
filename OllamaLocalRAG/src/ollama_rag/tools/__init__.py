"""Tools that can be exposed to a local Ollama chat model."""

from .knowledge_base import (
    KNOWLEDGE_BASE_TOOL,
    TOOL_NAME,
    KnowledgeBaseTool,
    find_best_vector_database,
)
from .protocol import Tool
from .registry import ToolRegistry

__all__ = [
    "KNOWLEDGE_BASE_TOOL",
    "TOOL_NAME",
    "KnowledgeBaseTool",
    "Tool",
    "ToolRegistry",
    "find_best_vector_database",
]
