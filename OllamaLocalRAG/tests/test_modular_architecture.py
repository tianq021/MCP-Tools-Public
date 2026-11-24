from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ollama_rag.config import AppSettings  # noqa: E402
from ollama_rag.documents import TextChunk as CanonicalTextChunk  # noqa: E402
from ollama_rag.ingestion import TextChunk as LegacyTextChunk  # noqa: E402
from ollama_rag.models import OllamaEmbeddingClient as CanonicalEmbedding  # noqa: E402
from ollama_rag.retrieval import OllamaEmbeddingClient as LegacyEmbedding  # noqa: E402
from ollama_rag.tools import ToolRegistry  # noqa: E402


class EchoTool:
    name = "echo"
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": "Return the supplied value.",
            "parameters": {"type": "object"},
        },
    }

    def execute(self, arguments: dict[str, Any] | str) -> dict[str, Any]:
        return {"ok": True, "arguments": arguments}


class ModularArchitectureTests(unittest.TestCase):
    def test_settings_own_runtime_paths(self) -> None:
        settings = AppSettings.from_project(PROJECT_ROOT)
        self.assertEqual(
            settings.full_vector_database,
            PROJECT_ROOT / "storage" / "vector_db" / "full" / "full_vectors.sqlite3",
        )
        self.assertEqual(
            settings.raw_documents,
            PROJECT_ROOT / "data" / "raw" / "downloads" / "H_Downloads",
        )

    def test_registry_dispatches_tools_by_name(self) -> None:
        registry = ToolRegistry([EchoTool()])
        self.assertEqual(registry.names, ("echo",))
        self.assertEqual(registry.schemas[0]["function"]["name"], "echo")
        self.assertTrue(registry.execute("echo", {"value": 1})["ok"])
        self.assertFalse(registry.execute("missing", {})["ok"])

    def test_legacy_imports_are_compatibility_aliases(self) -> None:
        self.assertIs(LegacyTextChunk, CanonicalTextChunk)
        self.assertIs(LegacyEmbedding, CanonicalEmbedding)


if __name__ == "__main__":
    unittest.main()
