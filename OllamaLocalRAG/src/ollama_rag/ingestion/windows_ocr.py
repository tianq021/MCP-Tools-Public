"""Compatibility import; OCR code now lives in ``ollama_rag.documents``."""

from ollama_rag.documents.windows_ocr import ocr_pdf, sha256_file

__all__ = ["ocr_pdf", "sha256_file"]
