"""Backward-compatible facade for :mod:`ollama_rag.documents`."""

from ollama_rag.documents import (
    SUPPORTED_EXTENSIONS,
    DocumentLoadResult,
    DocumentSection,
    TextChunk,
    chunk_document,
    load_document,
    normalize_text,
    ocr_pdf,
    sha256_file,
)

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "DocumentLoadResult",
    "DocumentSection",
    "TextChunk",
    "chunk_document",
    "load_document",
    "normalize_text",
    "ocr_pdf",
    "sha256_file",
]
