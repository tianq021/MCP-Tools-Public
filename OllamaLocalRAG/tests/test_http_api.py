from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ollama_rag.api.http_server import create_server  # noqa: E402


class FakeEmbeddingClient:
    model = "embedding-model"

    def version(self) -> str:
        return "test"

    def available_models(self) -> set[str]:
        return {self.model}


class FakeTools:
    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments.get("query"), str) or not arguments["query"].strip():
            return {"ok": False, "error": "query 必须是非空字符串。"}
        return {
            "ok": True,
            "query": arguments["query"],
            "result_count": 1,
            "results": [{"source_name": "通知.docx", "text": "测试证据"}],
        }


class FakeService:
    embedding_client = FakeEmbeddingClient()
    tools = FakeTools()

    def status(self) -> dict[str, Any]:
        return {
            "database": "test.sqlite3",
            "chunk_count": 1,
            "embedding_model": self.embedding_client.model,
            "tools": ["search_knowledge_base"],
        }


class HTTPAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(FakeService(), port=0)  # type: ignore[arg-type]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    @classmethod
    def request_json(
        cls,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            cls.base_url + path,
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with cls.opener.open(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_health_reports_ready_service(self) -> None:
        status, payload = self.request_json("/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["chunk_count"], 1)

    def test_search_dispatches_registered_tool(self) -> None:
        status, payload = self.request_json("/search", {"query": "开学时间"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result_count"], 1)

    def test_search_rejects_invalid_arguments(self) -> None:
        status, payload = self.request_json("/search", {"query": ""})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error_code"], "INVALID_ARGS")


if __name__ == "__main__":
    unittest.main()
