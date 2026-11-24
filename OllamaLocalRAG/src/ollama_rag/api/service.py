from __future__ import annotations

from pathlib import Path
from typing import Any

from ollama_rag.agents import RAGAgent, RAGAnswer
from ollama_rag.config import AppSettings
from ollama_rag.models import OllamaChatClient, OllamaEmbeddingClient
from ollama_rag.tools import (
    KnowledgeBaseTool,
    Tool,
    ToolRegistry,
    find_best_vector_database,
)


class RAGService:
    """Unified application API used by CLI, tests and future HTTP adapters."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        database: Path,
        embedding_client: OllamaEmbeddingClient,
        chat_client: OllamaChatClient,
        knowledge_base: KnowledgeBaseTool,
        tools: ToolRegistry,
        agent: RAGAgent,
    ) -> None:
        self.settings = settings
        self.database = database.resolve()
        self.embedding_client = embedding_client
        self.chat_client = chat_client
        self.knowledge_base = knowledge_base
        self.tools = tools
        self.agent = agent

    @classmethod
    def create(
        cls,
        project_root: Path,
        *,
        database: Path | None = None,
        chat_model: str | None = None,
        default_top_k: int | None = None,
    ) -> "RAGService":
        settings = AppSettings.from_project(project_root)
        selected_database = (
            database.resolve()
            if database is not None
            else find_best_vector_database(settings.vector_root)
        )
        embedding_client = OllamaEmbeddingClient(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            keep_alive=settings.keep_alive,
        )
        knowledge_base = KnowledgeBaseTool(
            selected_database,
            embedding_client=embedding_client,
            default_top_k=default_top_k or settings.default_top_k,
        )
        tools = ToolRegistry([knowledge_base])
        chat_client = OllamaChatClient(
            base_url=settings.ollama_base_url,
            model=chat_model or settings.chat_model,
            keep_alive=settings.keep_alive,
        )
        agent = RAGAgent(chat_client, tools)
        return cls(
            settings=settings,
            database=selected_database,
            embedding_client=embedding_client,
            chat_client=chat_client,
            knowledge_base=knowledge_base,
            tools=tools,
            agent=agent,
        )

    def register_tool(self, tool: Tool) -> None:
        """Expose another local tool without changing the CLI or agent loop."""
        self.tools.register(tool)

    def ask(self, question: str, *, top_k: int | None = None) -> RAGAnswer:
        return self.agent.answer(
            question,
            top_k=top_k or self.settings.default_top_k,
        )

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        collection: str = "all",
        source_types: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.knowledge_base.execute(
            {
                "query": query,
                "top_k": top_k or self.settings.default_top_k,
                "collection": collection,
                "source_types": source_types or [],
            }
        )

    def status(self) -> dict[str, Any]:
        return {
            "database": str(self.database),
            "chunk_count": self.knowledge_base.chunk_count,
            "chat_model": self.chat_client.model,
            "embedding_model": self.embedding_client.model,
            "ollama_base_url": self.settings.ollama_base_url,
            "tools": list(self.tools.names),
        }
