"""Modular local Ollama RAG package."""

from .api import IndexService, RAGService
from .config import AppSettings

__version__ = "0.2.0"

__all__ = ["AppSettings", "IndexService", "RAGService", "__version__"]
