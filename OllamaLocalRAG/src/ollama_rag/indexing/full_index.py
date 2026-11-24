from __future__ import annotations

import json
import os
import sqlite3
import struct
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ollama_rag.documents import (
    DocumentLoadResult,
    TextChunk,
    chunk_document,
    load_document,
    ocr_pdf,
    sha256_file,
)
from ollama_rag.documents import SUPPORTED_EXTENSIONS
from ollama_rag.models import OllamaEmbeddingClient


SCHEMA_VERSION = "2"
PARSER_VERSION = "document_loader_v1_windows_ocr_v1"
EMBEDDING_TEXT_VERSION = "source_name_relative_path_and_chunk_v1"
COLLECTIONS = {
    "personal_school": "学校、实习、课程、简历、协议及其他个人资料",
    "skills_technical": "skill-main 下的技术、技能与项目文档",
    "test_archive": "测试、修改及脱敏样例文件",
}


@dataclass
class PreparedDocument:
    path: Path
    relative_path: str
    collection: str
    source_sha256: str
    size_bytes: int
    modified_ns: int
    status: str
    document: DocumentLoadResult | None
    chunks: list[TextChunk]
    error: str | None = None


@dataclass(frozen=True)
class FullIndexResult:
    database: Path
    report: Path
    file_count: int
    indexed_document_count: int
    chunk_count: int
    ocr_document_count: int
    no_text_count: int
    failure_count: int
    embedded_chunk_count: int
    reused_chunk_count: int
    dimension: int
    elapsed_seconds: float


def assign_collection(relative_path: Path) -> str:
    lowered_parts = {part.lower() for part in relative_path.parts}
    lowered_name = relative_path.name.lower()
    if "skill-main" in lowered_parts:
        return "skills_technical"
    if "test" in lowered_parts or lowered_name in {"modified.pdf", "redacted.pdf"}:
        return "test_archive"
    return "personal_school"


def needs_ocr(document: DocumentLoadResult) -> bool:
    if document.source_type != "pdf":
        return False
    minimum_expected_chars = max(50, document.unit_count * 25)
    return document.char_count < minimum_expected_chars


class FullIndexBuilder:
    def __init__(
        self,
        *,
        project_root: Path,
        raw_root: Path,
        database: Path,
        report: Path,
        embedding_client: OllamaEmbeddingClient | None = None,
        max_chars: int = 300,
        overlap_chars: int = 40,
        batch_size: int = 64,
        enable_ocr: bool = True,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.project_root = project_root.resolve()
        self.raw_root = raw_root.resolve()
        self.database = database.resolve()
        self.report = report.resolve()
        self.embedding_client = embedding_client or OllamaEmbeddingClient()
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        self.batch_size = batch_size
        self.enable_ocr = enable_ocr
        self.progress = progress or (lambda message: None)
        self.ocr_cache = self.project_root / "storage" / "cache" / "ocr"
        self.ocr_script = self.project_root / "scripts" / "windows_ocr.ps1"

    def _files(self) -> list[Path]:
        if not self.raw_root.is_dir():
            raise FileNotFoundError(self.raw_root)
        return sorted(
            path
            for path in self.raw_root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    def _prepare(self, path: Path) -> PreparedDocument:
        relative = path.relative_to(self.raw_root)
        stat = path.stat()
        source_sha256 = sha256_file(path)
        try:
            document = load_document(path)
            status = "ok"
            if self.enable_ocr and needs_ocr(document):
                ocr_document = ocr_pdf(
                    path,
                    cache_root=self.ocr_cache,
                    script_path=self.ocr_script,
                )
                if ocr_document.char_count > document.char_count:
                    document = ocr_document
                    status = "ocr"
            chunks = chunk_document(
                document,
                max_chars=self.max_chars,
                overlap_chars=self.overlap_chars,
            )
            if not chunks:
                status = "no_text"
            return PreparedDocument(
                path=path,
                relative_path=str(relative),
                collection=assign_collection(relative),
                source_sha256=source_sha256,
                size_bytes=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                status=status,
                document=document,
                chunks=chunks,
            )
        except Exception as error:
            return PreparedDocument(
                path=path,
                relative_path=str(relative),
                collection=assign_collection(relative),
                source_sha256=source_sha256,
                size_bytes=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                status="error",
                document=None,
                chunks=[],
                error=f"{type(error).__name__}: {error}",
            )

    def _load_reusable_vectors(self) -> dict[tuple[str, int, str], bytes]:
        if not self.database.is_file():
            return {}
        connection = sqlite3.connect(f"file:{self.database.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            try:
                metadata = dict(connection.execute("SELECT key, value FROM metadata"))
                if metadata.get("schema_version") != SCHEMA_VERSION:
                    return {}
                if metadata.get("model") != self.embedding_client.model:
                    return {}
                if int(metadata.get("max_chars", "-1")) != self.max_chars:
                    return {}
                if int(metadata.get("overlap_chars", "-1")) != self.overlap_chars:
                    return {}
                if metadata.get("embedding_text_version") != EMBEDDING_TEXT_VERSION:
                    return {}
                rows = connection.execute(
                    """
                    SELECT source_sha256, chunk_index, text, vector
                    FROM chunks
                    ORDER BY id
                    """
                ).fetchall()
            except sqlite3.Error:
                return {}
        finally:
            connection.close()
        return {
            (str(row["source_sha256"]), int(row["chunk_index"]), str(row["text"])): row[
                "vector"
            ]
            for row in rows
        }

    @staticmethod
    def _embedding_text(prepared: PreparedDocument, chunk: TextChunk) -> str:
        """Add stable source context without changing the text shown to users."""
        return (
            f"文件名：{prepared.path.name}\n"
            f"相对路径：{prepared.relative_path}\n"
            f"正文：{chunk.text}"
        )

    def _embed_chunks(
        self,
        documents: list[PreparedDocument],
        reusable: dict[tuple[str, int, str], bytes],
    ) -> tuple[dict[tuple[str, int], bytes], int, int, int]:
        vector_blobs: dict[tuple[str, int], bytes] = {}
        pending: list[tuple[PreparedDocument, TextChunk]] = []
        reused = 0
        dimension = 0
        for prepared in documents:
            for chunk in prepared.chunks:
                reuse_key = (prepared.source_sha256, chunk.chunk_index, chunk.text)
                blob = reusable.get(reuse_key)
                if blob is not None:
                    vector_blobs[(prepared.relative_path, chunk.chunk_index)] = blob
                    dimension = len(blob) // 4
                    reused += 1
                else:
                    pending.append((prepared, chunk))

        embedded = 0
        total = len(pending)
        for start in range(0, total, self.batch_size):
            batch = pending[start : start + self.batch_size]
            result = self.embedding_client.embed_documents(
                [self._embedding_text(prepared, chunk) for prepared, chunk in batch]
            )
            if dimension and result.dimension != dimension:
                raise ValueError(
                    f"Embedding dimension changed: {result.dimension} != {dimension}"
                )
            dimension = result.dimension
            for (prepared, chunk), vector in zip(batch, result.vectors, strict=True):
                vector_blobs[(prepared.relative_path, chunk.chunk_index)] = struct.pack(
                    f"<{dimension}f", *vector
                )
            embedded += len(batch)
            self.progress(f"嵌入进度：{embedded}/{total} 个新文本块")

        if not dimension:
            raise RuntimeError("No vectors were generated or reused")
        return vector_blobs, dimension, embedded, reused

    def _write_database(
        self,
        documents: list[PreparedDocument],
        vector_blobs: dict[tuple[str, int], bytes],
        *,
        dimension: int,
        created_at: str,
        elapsed_seconds: float,
    ) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.database.with_suffix(self.database.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                PRAGMA journal_mode = DELETE;
                PRAGMA synchronous = FULL;
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE collections (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL
                );
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    collection_id INTEGER NOT NULL REFERENCES collections(id),
                    source_name TEXT NOT NULL,
                    source_path TEXT NOT NULL UNIQUE,
                    relative_path TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    section_count INTEGER NOT NULL,
                    unit_count INTEGER NOT NULL,
                    empty_units INTEGER NOT NULL,
                    char_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    warnings_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE chunks (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    collection_name TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    locator TEXT NOT NULL,
                    locator_start TEXT NOT NULL,
                    locator_end TEXT NOT NULL,
                    text TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    section_count INTEGER NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    UNIQUE(document_id, chunk_index),
                    CHECK(length(vector) = dimension * 4)
                );
                CREATE TABLE import_runs (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    indexed_document_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    elapsed_seconds REAL NOT NULL
                );
                CREATE INDEX idx_documents_collection ON documents(collection_id);
                CREATE INDEX idx_documents_sha256 ON documents(source_sha256);
                CREATE INDEX idx_documents_status ON documents(status);
                CREATE INDEX idx_chunks_collection ON chunks(collection_name);
                CREATE INDEX idx_chunks_source_path ON chunks(source_path);
                CREATE INDEX idx_chunks_source_type ON chunks(source_type);
                """
            )
            collection_ids: dict[str, int] = {}
            for collection_id, (name, description) in enumerate(COLLECTIONS.items(), 1):
                connection.execute(
                    "INSERT INTO collections(id, name, description) VALUES (?, ?, ?)",
                    (collection_id, name, description),
                )
                collection_ids[name] = collection_id

            chunk_count = sum(len(document.chunks) for document in documents)
            indexed_count = sum(bool(document.chunks) for document in documents)
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "created_at": created_at,
                "model": self.embedding_client.model,
                "dimension": str(dimension),
                "max_chars": str(self.max_chars),
                "overlap_chars": str(self.overlap_chars),
                "parser_version": PARSER_VERSION,
                "embedding_text_version": EMBEDDING_TEXT_VERSION,
                "file_count": str(len(documents)),
                "indexed_document_count": str(indexed_count),
                "chunk_count": str(chunk_count),
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items()
            )

            chunk_id = 1
            for document_id, prepared in enumerate(documents, 1):
                loaded = prepared.document
                connection.execute(
                    """
                    INSERT INTO documents (
                        id, collection_id, source_name, source_path, relative_path,
                        source_type, source_sha256, size_bytes, modified_ns, status,
                        section_count, unit_count, empty_units, char_count, chunk_count,
                        warnings_json, metadata_json, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        collection_ids[prepared.collection],
                        prepared.path.name,
                        str(prepared.path),
                        prepared.relative_path,
                        prepared.path.suffix.lower().lstrip("."),
                        prepared.source_sha256,
                        prepared.size_bytes,
                        prepared.modified_ns,
                        prepared.status,
                        len(loaded.sections) if loaded else 0,
                        loaded.unit_count if loaded else 0,
                        loaded.empty_units if loaded else 0,
                        loaded.char_count if loaded else 0,
                        len(prepared.chunks),
                        json.dumps(loaded.warnings if loaded else [], ensure_ascii=False),
                        json.dumps(loaded.metadata if loaded else {}, ensure_ascii=False),
                        prepared.error,
                    ),
                )
                for chunk in prepared.chunks:
                    vector = vector_blobs[(prepared.relative_path, chunk.chunk_index)]
                    connection.execute(
                        """
                        INSERT INTO chunks (
                            id, document_id, collection_name, source_name, source_path,
                            source_sha256, source_type, chunk_index, locator,
                            locator_start, locator_end, text, char_count, section_count,
                            dimension, vector
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            document_id,
                            prepared.collection,
                            prepared.path.name,
                            str(prepared.path),
                            prepared.source_sha256,
                            prepared.path.suffix.lower().lstrip("."),
                            chunk.chunk_index,
                            chunk.locator,
                            chunk.locator_start,
                            chunk.locator_end,
                            chunk.text,
                            chunk.char_count,
                            chunk.section_count,
                            dimension,
                            vector,
                        ),
                    )
                    chunk_id += 1

            connection.execute(
                """
                INSERT INTO import_runs (
                    created_at, file_count, indexed_document_count,
                    chunk_count, elapsed_seconds
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (created_at, len(documents), indexed_count, chunk_count, elapsed_seconds),
            )
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        finally:
            connection.close()
        os.replace(temporary, self.database)

    def build(self) -> FullIndexResult:
        started = time.perf_counter()
        created_at = datetime.now().astimezone().isoformat()
        files = self._files()
        self.progress(f"发现 {len(files)} 份支持的文档")

        documents: list[PreparedDocument] = []
        for index, path in enumerate(files, 1):
            prepared = self._prepare(path)
            documents.append(prepared)
            self.progress(
                f"解析进度：{index}/{len(files)} | {prepared.status} | {prepared.relative_path}"
            )

        reusable = self._load_reusable_vectors()
        vector_blobs, dimension, embedded, reused = self._embed_chunks(
            documents, reusable
        )
        elapsed_before_write = time.perf_counter() - started
        self._write_database(
            documents,
            vector_blobs,
            dimension=dimension,
            created_at=created_at,
            elapsed_seconds=elapsed_before_write,
        )

        status_counts = Counter(document.status for document in documents)
        collection_counts = Counter(document.collection for document in documents)
        extension_counts = Counter(document.path.suffix.lower() for document in documents)
        chunk_count = sum(len(document.chunks) for document in documents)
        elapsed = round(time.perf_counter() - started, 3)
        report = {
            "created_at": created_at,
            "database": str(self.database),
            "schema_version": SCHEMA_VERSION,
            "raw_root": str(self.raw_root),
            "model": self.embedding_client.model,
            "embedding_text_version": EMBEDDING_TEXT_VERSION,
            "dimension": dimension,
            "max_chars": self.max_chars,
            "overlap_chars": self.overlap_chars,
            "file_count": len(documents),
            "indexed_document_count": sum(bool(document.chunks) for document in documents),
            "chunk_count": chunk_count,
            "status_counts": dict(sorted(status_counts.items())),
            "collection_counts": dict(sorted(collection_counts.items())),
            "extension_counts": dict(sorted(extension_counts.items())),
            "embedded_chunk_count": embedded,
            "reused_chunk_count": reused,
            "elapsed_seconds": elapsed,
            "documents": [
                {
                    "source_path": str(document.path),
                    "relative_path": document.relative_path,
                    "collection": document.collection,
                    "source_sha256": document.source_sha256,
                    "status": document.status,
                    "char_count": document.document.char_count if document.document else 0,
                    "chunk_count": len(document.chunks),
                    "warnings": document.document.warnings if document.document else [],
                    "error": document.error,
                }
                for document in documents
            ],
        }
        self.report.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = self.report.with_suffix(self.report.suffix + ".tmp")
        temporary_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary_report.replace(self.report)

        return FullIndexResult(
            database=self.database,
            report=self.report,
            file_count=len(documents),
            indexed_document_count=sum(bool(document.chunks) for document in documents),
            chunk_count=chunk_count,
            ocr_document_count=status_counts.get("ocr", 0),
            no_text_count=status_counts.get("no_text", 0),
            failure_count=status_counts.get("error", 0),
            embedded_chunk_count=embedded,
            reused_chunk_count=reused,
            dimension=dimension,
            elapsed_seconds=elapsed,
        )
