"""Compatibility import; vector storage now lives in ``ollama_rag.vectorstore``."""

from ollama_rag.vectorstore.sqlite_store import build_database, search_database

__all__ = ["build_database", "search_database"]
