from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ollama_rag.models import OllamaEmbeddingClient
from ollama_rag.vectorstore import search_database


TOOL_NAME = "search_knowledge_base"
MAX_TOP_K = 8
MAX_QUERY_CHARS = 2_000
MAX_CHUNK_CHARS = 1_800

KNOWLEDGE_BASE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "搜索用户本地知识库中的 PDF、Word、TXT 和 Markdown 文档。"
            "当问题涉及本地文件、通知、协议、简历、报告、学校、实习、课程，"
            "或需要核对原文、文件名、页码和段落时使用。"
        ),
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "适合语义检索的完整问题，不要只传关键词。",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回最相关的文本块数量，默认 5。",
                    "default": 5,
                    "minimum": 1,
                    "maximum": MAX_TOP_K,
                },
                "collection": {
                    "type": "string",
                    "description": (
                        "检索范围：personal_school 是学校和个人资料，"
                        "skills_technical 是技术文档，test_archive 是测试存档，"
                        "all 是全部资料。默认 all。"
                    ),
                    "enum": [
                        "all",
                        "personal_school",
                        "skills_technical",
                        "test_archive",
                    ],
                    "default": "all",
                },
                "source_types": {
                    "type": "array",
                    "description": "可选的文件类型过滤。",
                    "items": {"type": "string", "enum": ["pdf", "docx", "txt", "md"]},
                },
            },
        },
    },
}


def _database_chunk_count(database: Path) -> int | None:
    """Return a count only for databases using the current vector schema."""
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(chunks)").fetchall()
            }
            required = {
                "source_name",
                "source_path",
                "source_type",
                "locator",
                "text",
                "dimension",
                "vector",
            }
            if not required.issubset(columns):
                return None
            row = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()
            return int(row[0]) if row else None
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def find_best_vector_database(vector_root: Path) -> Path:
    """Choose the compatible SQLite index containing the most chunks."""
    candidates: list[tuple[int, Path]] = []
    if vector_root.is_dir():
        for database in vector_root.rglob("*.sqlite3"):
            chunk_count = _database_chunk_count(database)
            if chunk_count is not None:
                candidates.append((chunk_count, database))
    if not candidates:
        raise FileNotFoundError(
            f"No compatible vector database was found under: {vector_root}"
        )
    candidates.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    return candidates[0][1]


class KnowledgeBaseTool:
    """Validate tool arguments, embed a query, and search the SQLite index."""

    name = TOOL_NAME
    schema = KNOWLEDGE_BASE_TOOL

    def __init__(
        self,
        database: Path,
        *,
        embedding_client: OllamaEmbeddingClient | None = None,
        default_top_k: int = 5,
        min_score: float | None = None,
    ) -> None:
        self.database = database.resolve()
        if not self.database.is_file():
            raise FileNotFoundError(f"Vector database does not exist: {self.database}")
        if _database_chunk_count(self.database) is None:
            raise ValueError(f"Vector database schema is not compatible: {self.database}")
        if not 1 <= default_top_k <= MAX_TOP_K:
            raise ValueError(f"default_top_k must be between 1 and {MAX_TOP_K}")
        self.embedding_client = embedding_client or OllamaEmbeddingClient()
        self.default_top_k = default_top_k
        self.min_score = min_score

    @property
    def chunk_count(self) -> int:
        return _database_chunk_count(self.database) or 0

    def execute(self, arguments: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                return {"ok": False, "error": f"工具参数不是合法 JSON：{error}"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "工具参数必须是一个对象。"}

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"ok": False, "error": "query 必须是非空字符串。"}
        query = query.strip()
        if len(query) > MAX_QUERY_CHARS:
            return {
                "ok": False,
                "error": f"query 不能超过 {MAX_QUERY_CHARS} 个字符。",
            }

        raw_top_k = arguments.get("top_k", self.default_top_k)
        if isinstance(raw_top_k, bool) or not isinstance(raw_top_k, int):
            return {"ok": False, "error": "top_k 必须是整数。"}
        top_k = max(1, min(raw_top_k, MAX_TOP_K))

        collection = arguments.get("collection", "all")
        allowed_collections = {
            "all",
            "personal_school",
            "skills_technical",
            "test_archive",
        }
        if collection not in allowed_collections:
            return {"ok": False, "error": f"未知 collection：{collection}"}
        source_types = arguments.get("source_types", [])
        if not isinstance(source_types, list) or not all(
            isinstance(value, str) for value in source_types
        ):
            return {"ok": False, "error": "source_types 必须是字符串数组。"}
        allowed_source_types = {"pdf", "docx", "txt", "md"}
        normalized_source_types = [
            value.lower().lstrip(".") for value in source_types
        ]
        if any(value not in allowed_source_types for value in normalized_source_types):
            return {"ok": False, "error": "source_types 包含不支持的文件类型。"}

        query_batch = self.embedding_client.embed_queries([query])
        rows = search_database(
            self.database,
            query_batch.vectors[0],
            top_k=top_k,
            collection=collection,
            source_types=normalized_source_types,
        )
        if self.min_score is not None:
            rows = [row for row in rows if row["score"] >= self.min_score]

        results = [
            {
                "rank": row["rank"],
                "score": row["score"],
                "source_name": row["source_name"],
                "source_path": row["source_path"],
                "source_type": row["source_type"],
                "collection": row.get("collection_name"),
                "locator": row["locator"],
                "text": row["text"][:MAX_CHUNK_CHARS],
            }
            for row in rows
        ]
        return {
            "ok": True,
            "query": query,
            "database": str(self.database),
            "collection": collection,
            "source_types": normalized_source_types,
            "result_count": len(results),
            "results": results,
            "answer_instruction": (
                "只根据以上检索结果回答；引用时标明 source_name 和 locator。"
                "如果证据不足，明确说明知识库中没有找到可靠答案。"
            ),
        }
