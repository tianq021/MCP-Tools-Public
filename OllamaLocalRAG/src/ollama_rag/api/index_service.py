from __future__ import annotations

from pathlib import Path
from typing import Callable

from ollama_rag.config import AppSettings
from ollama_rag.indexing import FullIndexBuilder, FullIndexResult
from ollama_rag.models import OllamaEmbeddingClient


class IndexService:
    """Unified API for creating or incrementally refreshing the formal index."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        embedding_client: OllamaEmbeddingClient,
    ) -> None:
        self.settings = settings
        self.embedding_client = embedding_client

    @classmethod
    def create(cls, project_root: Path) -> "IndexService":
        settings = AppSettings.from_project(project_root)
        embedding_client = OllamaEmbeddingClient(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            keep_alive=settings.keep_alive,
        )
        return cls(settings=settings, embedding_client=embedding_client)

    def build(
        self,
        *,
        batch_size: int = 64,
        enable_ocr: bool = True,
        progress: Callable[[str], None] | None = None,
    ) -> FullIndexResult:
        self.embedding_client.require_model()
        builder = FullIndexBuilder(
            project_root=self.settings.project_root,
            raw_root=self.settings.raw_documents,
            database=self.settings.full_vector_database,
            report=self.settings.import_report,
            embedding_client=self.embedding_client,
            batch_size=batch_size,
            enable_ocr=enable_ocr,
            progress=progress,
        )
        return builder.build()
