from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CHAT_MODEL = "gemma4:12b-it-qat"
DEFAULT_EMBEDDING_MODEL = "quentinz/bge-large-zh-v1.5:f32"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class AppSettings:
    """One source of truth for runtime paths and model configuration."""

    project_root: Path
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    chat_model: str = DEFAULT_CHAT_MODEL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    keep_alive: str = "15m"
    default_top_k: int = 5

    @classmethod
    def from_project(cls, project_root: Path) -> "AppSettings":
        return cls(
            project_root=project_root.resolve(),
            ollama_base_url=os.getenv("OLLAMA_RAG_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            chat_model=os.getenv("OLLAMA_RAG_CHAT_MODEL", DEFAULT_CHAT_MODEL),
            embedding_model=os.getenv(
                "OLLAMA_RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
            ),
        )

    @property
    def raw_documents(self) -> Path:
        return self.project_root / "data" / "raw" / "downloads" / "H_Downloads"

    @property
    def vector_root(self) -> Path:
        return self.project_root / "storage" / "vector_db"

    @property
    def full_vector_database(self) -> Path:
        return self.vector_root / "full" / "full_vectors.sqlite3"

    @property
    def import_report(self) -> Path:
        return self.vector_root / "full" / "import_report.json"

    @property
    def validation_report(self) -> Path:
        return self.vector_root / "full" / "validation_report.json"

    @property
    def ocr_cache(self) -> Path:
        return self.project_root / "storage" / "cache" / "ocr"

    @property
    def ocr_script(self) -> Path:
        return self.project_root / "scripts" / "windows_ocr.ps1"
