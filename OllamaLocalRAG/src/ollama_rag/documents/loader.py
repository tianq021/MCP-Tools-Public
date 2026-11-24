from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pypdf.errors import PdfReadError


SUPPORTED_EXTENSIONS = {".docx", ".md", ".pdf", ".txt"}


@dataclass(frozen=True)
class DocumentSection:
    source_path: Path
    source_type: str
    locator_type: str
    locator: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        output = asdict(self)
        output["source_path"] = str(self.source_path)
        return output


@dataclass(frozen=True)
class DocumentLoadResult:
    source_path: Path
    source_type: str
    sections: list[DocumentSection]
    unit_count: int
    empty_units: int
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return sum(len(section.text) for section in self.sections)

    def to_dict(self, *, include_sections: bool = False) -> dict[str, Any]:
        output = {
            "source_path": str(self.source_path),
            "source_type": self.source_type,
            "section_count": len(self.sections),
            "unit_count": self.unit_count,
            "empty_units": self.empty_units,
            "char_count": self.char_count,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }
        if include_sections:
            output["sections"] = [section.to_dict() for section in self.sections]
        return output


@dataclass(frozen=True)
class TextChunk:
    source_path: Path
    source_type: str
    chunk_index: int
    locator_start: str
    locator_end: str
    text: str
    char_count: int
    section_count: int

    @property
    def locator(self) -> str:
        if self.locator_start == self.locator_end:
            return self.locator_start
        return f"{self.locator_start} - {self.locator_end}"

    def to_dict(self) -> dict[str, Any]:
        output = asdict(self)
        output["source_path"] = str(self.source_path)
        output["locator"] = self.locator
        return output


def normalize_text(text: str) -> str:
    # Some PDF font maps emit isolated UTF-16 surrogate code points. They are
    # not valid UTF-8 and must not reach JSON payloads sent to Ollama.
    text = re.sub(r"[\ud800-\udfff]", " ", text)
    text = text.replace("\ufeff", "").replace("\ufffd", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"Unable to decode text file: {path}")


def _load_text(path: Path) -> DocumentLoadResult:
    text, encoding = _read_text(path)
    normalized = normalize_text(text)
    sections = []
    if normalized:
        sections.append(
            DocumentSection(
                source_path=path,
                source_type=path.suffix.lower().lstrip("."),
                locator_type="document",
                locator="document",
                text=normalized,
                metadata={"encoding": encoding},
            )
        )
    return DocumentLoadResult(
        source_path=path,
        source_type=path.suffix.lower().lstrip("."),
        sections=sections,
        unit_count=1,
        empty_units=0 if normalized else 1,
        metadata={"encoding": encoding},
    )


def _load_pdf(path: Path) -> DocumentLoadResult:
    warnings: list[str] = []
    page_texts: list[str] = []
    backend = "pypdf"
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            decrypt_result = reader.decrypt("")
            if not decrypt_result:
                raise ValueError("PDF is encrypted and requires a password")
            warnings.append("PDF encryption opened with an empty password")
        for page in reader.pages:
            # Plain extraction is preferable for RAG: it keeps rotated text that
            # pypdf's layout mode can omit, while page metadata preserves citation.
            text = page.extract_text() or ""
            page_texts.append(text)
    except (PdfReadError, ValueError) as error:
        backend = "pdfplumber"
        warnings.append(
            f"pypdf failed ({type(error).__name__}); pdfplumber fallback used"
        )
        with pdfplumber.open(path) as pdf:
            page_texts = [page.extract_text(layout=True) or "" for page in pdf.pages]

    sections: list[DocumentSection] = []
    empty_pages = 0
    for page_number, text in enumerate(page_texts, start=1):
        normalized = normalize_text(text)
        if not normalized:
            empty_pages += 1
            continue
        sections.append(
            DocumentSection(
                source_path=path,
                source_type="pdf",
                locator_type="page",
                locator=f"page {page_number}",
                text=normalized,
                metadata={"page_number": page_number},
            )
        )

    if empty_pages:
        warnings.append(f"{empty_pages} page(s) contained no extractable text")
    return DocumentLoadResult(
        source_path=path,
        source_type="pdf",
        sections=sections,
        unit_count=len(page_texts),
        empty_units=empty_pages,
        warnings=warnings,
        metadata={"page_count": len(page_texts), "extraction_backend": backend},
    )


def _table_row_text(table: Table, row_index: int) -> str:
    values: list[str] = []
    seen_cells: set[int] = set()
    for cell in table.rows[row_index].cells:
        cell_identity = id(cell._tc)
        if cell_identity in seen_cells:
            continue
        seen_cells.add(cell_identity)
        value = normalize_text("\n".join(paragraph.text for paragraph in cell.paragraphs))
        values.append(value)
    return " | ".join(value for value in values if value)


def _load_docx(path: Path) -> DocumentLoadResult:
    document = Document(path)
    sections: list[DocumentSection] = []
    paragraph_index = 0
    table_index = 0
    unit_count = 0
    empty_units = 0

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            paragraph_index += 1
            unit_count += 1
            text = normalize_text(item.text)
            if not text:
                empty_units += 1
                continue
            sections.append(
                DocumentSection(
                    source_path=path,
                    source_type="docx",
                    locator_type="paragraph",
                    locator=f"paragraph {paragraph_index}",
                    text=text,
                    metadata={
                        "paragraph_index": paragraph_index,
                        "style": item.style.name if item.style else None,
                    },
                )
            )
        elif isinstance(item, Table):
            table_index += 1
            for row_index in range(len(item.rows)):
                unit_count += 1
                text = _table_row_text(item, row_index)
                if not text:
                    empty_units += 1
                    continue
                sections.append(
                    DocumentSection(
                        source_path=path,
                        source_type="docx",
                        locator_type="table_row",
                        locator=f"table {table_index} row {row_index + 1}",
                        text=text,
                        metadata={
                            "table_index": table_index,
                            "row_index": row_index + 1,
                        },
                    )
                )

    core = document.core_properties
    metadata = {
        "paragraph_count": paragraph_index,
        "table_count": table_index,
        "title": core.title or None,
        "subject": core.subject or None,
    }
    return DocumentLoadResult(
        source_path=path,
        source_type="docx",
        sections=sections,
        unit_count=unit_count,
        empty_units=empty_units,
        metadata=metadata,
    )


def load_document(path: Path) -> DocumentLoadResult:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported document extension: {extension}")
    if extension == ".pdf":
        return _load_pdf(path)
    if extension == ".docx":
        return _load_docx(path)
    return _load_text(path)


def _split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    punctuation = "。！？；.!?;\n"
    minimum_cut = max(1, int(max_chars * 0.6))
    while start < len(text):
        maximum_end = min(len(text), start + max_chars)
        end = maximum_end
        if maximum_end < len(text):
            search_start = min(maximum_end, start + minimum_cut)
            best = max(text.rfind(mark, search_start, maximum_end) for mark in punctuation)
            if best >= search_start:
                end = best + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return pieces


def chunk_document(
    document: DocumentLoadResult,
    *,
    max_chars: int = 300,
    overlap_chars: int = 40,
) -> list[TextChunk]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be in [0, max_chars)")

    atomic: list[DocumentSection] = []
    for section in document.sections:
        pieces = _split_text(section.text, max_chars, overlap_chars)
        for piece_index, piece in enumerate(pieces, start=1):
            locator = section.locator
            if len(pieces) > 1:
                locator = f"{locator} part {piece_index}"
            atomic.append(
                DocumentSection(
                    source_path=section.source_path,
                    source_type=section.source_type,
                    locator_type=section.locator_type,
                    locator=locator,
                    text=piece,
                    metadata=section.metadata,
                )
            )

    chunks: list[TextChunk] = []
    buffer: list[DocumentSection] = []
    buffer_chars = 0

    def flush() -> None:
        nonlocal buffer, buffer_chars
        if not buffer:
            return
        text = "\n".join(section.text for section in buffer)
        chunks.append(
            TextChunk(
                source_path=document.source_path,
                source_type=document.source_type,
                chunk_index=len(chunks),
                locator_start=buffer[0].locator,
                locator_end=buffer[-1].locator,
                text=text,
                char_count=len(text),
                section_count=len(buffer),
            )
        )
        buffer = []
        buffer_chars = 0

    for section in atomic:
        separator_chars = 1 if buffer else 0
        if buffer and buffer_chars + separator_chars + len(section.text) > max_chars:
            flush()
        buffer.append(section)
        buffer_chars += separator_chars + len(section.text)
    flush()
    return chunks
