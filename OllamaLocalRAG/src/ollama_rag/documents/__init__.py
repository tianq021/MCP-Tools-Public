"""Document loading, normalization, chunking and OCR."""

from .loader import (
    SUPPORTED_EXTENSIONS,
    DocumentLoadResult,
    DocumentSection,
    TextChunk,
    chunk_document,
    load_document,
    normalize_text,
)
from .windows_ocr import ocr_pdf, sha256_file

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
