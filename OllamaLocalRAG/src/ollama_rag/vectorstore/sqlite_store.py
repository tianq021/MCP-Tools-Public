from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ollama_rag.documents import TextChunk


def build_database(
    database: Path,
    chunks: list[TextChunk],
    vectors: list[list[float]],
    *,
    model: str,
) -> dict[str, Any]:
    if not chunks or len(chunks) != len(vectors):
        raise ValueError("Chunks and vectors must be non-empty and have equal lengths")
    dimension = len(vectors[0])
    if dimension <= 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("Embedding vectors have invalid dimensions")

    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_suffix(database.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()

    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            PRAGMA synchronous = FULL;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                source_name TEXT NOT NULL,
                source_path TEXT NOT NULL,
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
                UNIQUE(source_path, chunk_index),
                CHECK(length(vector) = dimension * 4)
            );
            CREATE INDEX idx_chunks_source_path ON chunks(source_path);
            CREATE INDEX idx_chunks_source_type ON chunks(source_type);
            """
        )
        metadata = {
            "created_at": datetime.now().astimezone().isoformat(),
            "model": model,
            "dimension": str(dimension),
            "chunk_count": str(len(chunks)),
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items()
        )
        for row_id, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True), 1):
            connection.execute(
                """
                INSERT INTO chunks (
                    id, source_name, source_path, source_type, chunk_index,
                    locator, locator_start, locator_end, text, char_count,
                    section_count, dimension, vector
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    chunk.source_path.name,
                    str(chunk.source_path),
                    chunk.source_type,
                    chunk.chunk_index,
                    chunk.locator,
                    chunk.locator_start,
                    chunk.locator_end,
                    chunk.text,
                    chunk.char_count,
                    chunk.section_count,
                    dimension,
                    struct.pack(f"<{dimension}f", *vector),
                ),
            )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    finally:
        connection.close()
    os.replace(temporary, database)
    return {
        "database": str(database),
        "chunk_count": len(chunks),
        "dimension": dimension,
        "integrity_check": "ok",
    }


def search_database(
    database: Path,
    query_vector: list[float],
    *,
    top_k: int = 5,
    collection: str | None = None,
    source_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        dimension_row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'dimension'"
        ).fetchone()
        if dimension_row is None:
            raise RuntimeError("Vector dimension metadata is missing")
        dimension = int(dimension_row["value"])
        if len(query_vector) != dimension:
            raise ValueError(
                f"Query dimension mismatch: {len(query_vector)} != {dimension}"
            )
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(chunks)").fetchall()
        }
        conditions: list[str] = []
        parameters: list[Any] = []
        if collection and collection != "all" and "collection_name" in columns:
            conditions.append("collection_name = ?")
            parameters.append(collection)
        normalized_source_types = sorted(
            {
                source_type.strip().lower().lstrip(".")
                for source_type in (source_types or [])
                if source_type.strip()
            }
        )
        if normalized_source_types:
            placeholders = ", ".join("?" for _ in normalized_source_types)
            conditions.append(f"source_type IN ({placeholders})")
            parameters.extend(normalized_source_types)
        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = connection.execute(
            f"SELECT * FROM chunks{where_clause} ORDER BY id", parameters
        ).fetchall()
    finally:
        connection.close()

    query_norm = math.sqrt(sum(value * value for value in query_vector))
    if query_norm == 0 or not math.isfinite(query_norm):
        raise ValueError("Query vector is invalid")
    normalized_query = [value / query_norm for value in query_vector]
    if not rows:
        return []

    try:
        import numpy as np

        matrix = np.frombuffer(
            b"".join(row["vector"] for row in rows), dtype="<f4"
        ).reshape(len(rows), dimension)
        scores = matrix @ np.asarray(normalized_query, dtype=np.float32)
        order = np.argsort(scores)[::-1]
        ranked_rows = [(rows[int(index)], float(scores[int(index)])) for index in order]
    except ImportError:
        ranked_rows = []
        for row in rows:
            vector = struct.unpack(f"<{dimension}f", row["vector"])
            score = sum(value * query for value, query in zip(vector, normalized_query))
            ranked_rows.append((row, score))
        ranked_rows.sort(key=lambda item: item[1], reverse=True)

    scored = []
    seen_texts: set[str] = set()
    for row, score in ranked_rows:
        text_key = str(row["text"])
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)
        output = {
            key: row[key]
            for key in (
                "source_name",
                "source_path",
                "source_type",
                "chunk_index",
                "locator",
                "text",
                "char_count",
            )
        }
        if "collection_name" in row.keys():
            output["collection_name"] = row["collection_name"]
        if "source_sha256" in row.keys():
            output["source_sha256"] = row["source_sha256"]
        output["score"] = round(score, 6)
        scored.append(output)
        if len(scored) >= top_k:
            break

    results = scored
    for rank, result in enumerate(results, start=1):
        result["rank"] = rank
    return results
