from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ollama_rag.agents import RAGAgent  # noqa: E402
from ollama_rag.tools import ToolRegistry  # noqa: E402


class FakeKnowledgeBase:
    name = "search_knowledge_base"
    schema = {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object"}},
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any] | str] = []

    def execute(self, arguments: dict[str, Any] | str) -> dict[str, Any]:
        self.calls.append(arguments)
        return {
            "ok": True,
            "results": [
                {
                    "source_name": "通知.docx",
                    "source_path": "data/通知.docx",
                    "locator": "paragraph 3",
                    "score": 0.8,
                    "text": "测试证据",
                }
            ],
        }


class FakeChatClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, Any]]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append([dict(message) for message in messages])
        return self.responses.pop(0)


class FakeEchoTool:
    name = "echo"
    schema = {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object"}},
    }

    def execute(self, arguments: dict[str, Any] | str) -> dict[str, Any]:
        return {"ok": True, "echo": arguments}


class RAGAgentTests(unittest.TestCase):
    def test_local_document_question_forces_search(self) -> None:
        client = FakeChatClient(
            [{"message": {"role": "assistant", "content": "证据回答"}}]
        )
        knowledge_base = FakeKnowledgeBase()
        agent = RAGAgent(client, knowledge_base)  # type: ignore[arg-type]

        answer = agent.answer("这份实习通知的截止日期是什么？")

        self.assertTrue(answer.used_knowledge_base)
        self.assertTrue(answer.forced_search)
        self.assertEqual(answer.tool_call_count, 1)
        self.assertEqual(len(knowledge_base.calls), 1)
        self.assertEqual(answer.sources[0]["source_name"], "通知.docx")
        self.assertEqual(client.calls[0][-1]["role"], "tool")

    def test_model_can_choose_tool_for_unmatched_question(self) -> None:
        client = FakeChatClient(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "search_knowledge_base",
                                    "arguments": {"query": "张三是谁", "top_k": 3},
                                },
                            }
                        ],
                    }
                },
                {"message": {"role": "assistant", "content": "找到张三。"}},
            ]
        )
        knowledge_base = FakeKnowledgeBase()
        agent = RAGAgent(client, knowledge_base)  # type: ignore[arg-type]

        answer = agent.answer("张三是谁？")

        self.assertTrue(answer.used_knowledge_base)
        self.assertFalse(answer.forced_search)
        self.assertEqual(answer.tool_call_count, 1)
        self.assertEqual(len(knowledge_base.calls), 1)

    def test_general_question_can_skip_tool(self) -> None:
        client = FakeChatClient(
            [{"message": {"role": "assistant", "content": "四。"}}]
        )
        knowledge_base = FakeKnowledgeBase()
        agent = RAGAgent(client, knowledge_base)  # type: ignore[arg-type]

        answer = agent.answer("二加二等于多少？")

        self.assertFalse(answer.used_knowledge_base)
        self.assertEqual(knowledge_base.calls, [])

    def test_non_knowledge_tool_does_not_claim_rag_was_used(self) -> None:
        client = FakeChatClient(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": {"value": "hello"},
                                },
                            }
                        ],
                    }
                },
                {"message": {"role": "assistant", "content": "hello"}},
            ]
        )
        knowledge_base = FakeKnowledgeBase()
        registry = ToolRegistry([knowledge_base, FakeEchoTool()])
        agent = RAGAgent(client, registry)  # type: ignore[arg-type]

        answer = agent.answer("重复 hello")

        self.assertFalse(answer.used_knowledge_base)
        self.assertEqual(answer.tool_call_count, 1)
        self.assertEqual(knowledge_base.calls, [])


if __name__ == "__main__":
    unittest.main()
