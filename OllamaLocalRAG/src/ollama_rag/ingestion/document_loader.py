"""Compatibility import; document code now lives in ``ollama_rag.documents``."""

from ollama_rag.documents.loader import (
    SUPPORTED_EXTENSIONS,
    DocumentLoadResult,
    DocumentSection,
    TextChunk,
    chunk_document,
    load_document,
    normalize_text,
)

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "DocumentLoadResult",
    "DocumentSection",
    "TextChunk",
    "chunk_document",
    "load_document",
    "normalize_text",
]
