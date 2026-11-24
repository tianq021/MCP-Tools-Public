"""Compatibility import; model clients now live in ``ollama_rag.models``."""

from ollama_rag.models.ollama_embeddings import EmbeddingBatch, OllamaEmbeddingClient

__all__ = ["EmbeddingBatch", "OllamaEmbeddingClient"]
