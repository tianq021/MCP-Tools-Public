"""Backward-compatible facade for model and vector-store modules."""

from ollama_rag.models import EmbeddingBatch, OllamaEmbeddingClient
from ollama_rag.vectorstore import build_database, search_database

__all__ = [
    "EmbeddingBatch",
    "OllamaEmbeddingClient",
    "build_database",
    "search_database",
]
