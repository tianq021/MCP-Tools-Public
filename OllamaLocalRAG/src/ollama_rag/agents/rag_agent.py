from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ollama_rag.models import OllamaChatClient
from ollama_rag.tools import KnowledgeBaseTool, Tool, ToolRegistry

SYSTEM_PROMPT = """你是一个本地知识库助手，并且可以调用 search_knowledge_base。

必须检索的情况：
- 问题涉及用户的本地文件、文档、资料、PDF、Word、通知、协议、简历或报告；
- 问题要求核对原文、文件名、页码、段落、日期、人员或具体规定；
- 问题涉及知识库中的学校、实习、企业实训、课程、考试等具体事项；
- 不检索就无法确认事实。不能确定时优先检索。

普通闲聊、计算、翻译和与本地资料无关的一般知识可以直接回答。
调用工具后，只能依据工具返回的原文作答，并在相关陈述后用“[文件名，位置]”标注来源。
工具没有找到充分证据时，明确说知识库中没有找到，不得猜测。不要声称自己直接读取了未检索的文件。
知识库包含 personal_school（学校和个人资料）、skills_technical（技术资料）和 test_archive（测试存档）三个集合；可以按问题选择 collection，不能判断时使用 all。
"""

FORCE_SEARCH_PATTERNS = (
    r"我的(?:文件|文档|资料|下载)",
    r"(?:本地|知识库|文件|文档|资料|PDF|Word|docx)",
    r"(?:哪|第几)(?:个文件|一页|页|段)",
    r"(?:原文|通知|协议|简历|报告|规定)",
    r"(?:学校|实习|企业实训|课程|补考)",
)


@dataclass(frozen=True)
class RAGAnswer:
    content: str
    used_knowledge_base: bool
    forced_search: bool
    tool_call_count: int
    sources: list[dict[str, Any]]


class RAGAgent:
    def __init__(
        self,
        chat_client: OllamaChatClient,
        tools: ToolRegistry | Tool,
        *,
        max_tool_rounds: int = 2,
    ) -> None:
        if max_tool_rounds <= 0:
            raise ValueError("max_tool_rounds must be positive")
        self.chat_client = chat_client
        self.tools = tools if isinstance(tools, ToolRegistry) else ToolRegistry([tools])
        self.knowledge_base = self.tools.require(KnowledgeBaseTool.name)
        self.max_tool_rounds = max_tool_rounds

    @staticmethod
    def should_force_search(question: str) -> bool:
        return any(re.search(pattern, question, re.IGNORECASE) for pattern in FORCE_SEARCH_PATTERNS)

    @staticmethod
    def suggest_collection(question: str) -> str:
        if re.search(
            r"(?:代码|Python|API|skill|技能|插件|编程|技术|项目|部署)",
            question,
            re.IGNORECASE,
        ):
            return "skills_technical"
        if re.search(
            r"(?:学校|实习|企业实训|课程|补考|考试|简历|协议|通知)",
            question,
            re.IGNORECASE,
        ):
            return "personal_school"
        return "all"

    @staticmethod
    def _tool_call(
        arguments: dict[str, Any],
        *,
        name: str = KnowledgeBaseTool.name,
        index: int = 0,
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "index": index,
                "name": name,
                "arguments": arguments,
            },
        }

    @staticmethod
    def _collect_sources(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
        if not tool_result.get("ok"):
            return []
        return [
            {
                "source_name": row["source_name"],
                "source_path": row["source_path"],
                "locator": row["locator"],
                "score": row["score"],
            }
            for row in tool_result.get("results", [])
        ]

    def _append_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_name: str,
        arguments: dict[str, Any] | str,
        sources: list[dict[str, Any]],
    ) -> None:
        result = self.tools.execute(tool_name, arguments)
        if tool_name == KnowledgeBaseTool.name:
            sources.extend(self._collect_sources(result))
        messages.append(
            {
                "role": "tool",
                "tool_name": tool_name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    def answer(self, question: str, *, top_k: int = 5) -> RAGAnswer:
        question = question.strip()
        if not question:
            raise ValueError("question must not be empty")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        sources: list[dict[str, Any]] = []
        tool_call_count = 0
        used_knowledge_base = False
        forced_search = self.should_force_search(question)

        if forced_search:
            arguments = {
                "query": question,
                "top_k": top_k,
                "collection": self.suggest_collection(question),
            }
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [self._tool_call(arguments)],
                }
            )
            self._append_tool_result(
                messages,
                self.knowledge_base.name,
                arguments,
                sources,
            )
            tool_call_count += 1
            used_knowledge_base = True

        for _ in range(self.max_tool_rounds + 1):
            response = self.chat_client.chat(
                messages,
                tools=self.tools.schemas,
            )
            message = response.get("message")
            if not isinstance(message, dict):
                raise RuntimeError("Ollama response is missing message")
            messages.append(message)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = str(message.get("content", "")).strip()
                return RAGAnswer(
                    content=content,
                    used_knowledge_base=used_knowledge_base,
                    forced_search=forced_search,
                    tool_call_count=tool_call_count,
                    sources=sources,
                )

            if tool_call_count >= self.max_tool_rounds:
                for call in tool_calls:
                    function = call.get("function", {})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": str(function.get("name", "unknown")),
                            "content": json.dumps(
                                {"ok": False, "error": "已达到工具调用次数上限，请根据现有证据回答。"},
                                ensure_ascii=False,
                            ),
                        }
                    )
                final = self.chat_client.chat(messages)
                final_message = final.get("message", {})
                return RAGAnswer(
                    content=str(final_message.get("content", "")).strip(),
                    used_knowledge_base=used_knowledge_base,
                    forced_search=forced_search,
                    tool_call_count=tool_call_count,
                    sources=sources,
                )

            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name")
                if not isinstance(name, str) or self.tools.get(name) is None:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": str(name or "unknown"),
                            "content": json.dumps(
                                {"ok": False, "error": f"未知工具：{name}"},
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue
                self._append_tool_result(
                    messages,
                    name,
                    function.get("arguments", {}),
                    sources,
                )
                tool_call_count += 1
                used_knowledge_base = (
                    used_knowledge_base or name == KnowledgeBaseTool.name
                )

        raise RuntimeError("RAG agent loop ended unexpectedly")
