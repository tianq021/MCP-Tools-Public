from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ollama_rag.documents import chunk_document, load_document  # noqa: E402


RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "downloads" / "H_Downloads"
REPORT_PATH = PROJECT_ROOT / "storage" / "index_capacity_estimate.json"
SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
EMBEDDING_DIMENSION = 1024
FLOAT32_BYTES = 4


def main() -> None:
    files = sorted(
        path
        for path in RAW_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )
    by_extension: dict[str, dict[str, int]] = {}
    failures = []
    no_text = []
    total_chunks = 0
    total_chars = 0
    started = time.perf_counter()

    for path in files:
        extension = path.suffix.lower()
        summary = by_extension.setdefault(
            extension, {"files": 0, "chunks": 0, "chars": 0, "no_text": 0}
        )
        summary["files"] += 1
        try:
            document = load_document(path)
            chunks = chunk_document(document)
            summary["chunks"] += len(chunks)
            summary["chars"] += document.char_count
            total_chunks += len(chunks)
            total_chars += document.char_count
            if not document.char_count:
                summary["no_text"] += 1
                no_text.append(str(path.relative_to(PROJECT_ROOT)))
        except Exception as error:
            failures.append(
                {
                    "source_path": str(path.relative_to(PROJECT_ROOT)),
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )

    raw_vector_bytes = total_chunks * EMBEDDING_DIMENSION * FLOAT32_BYTES
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "raw_root": str(RAW_ROOT),
        "file_count": len(files),
        "chunk_count": total_chunks,
        "char_count": total_chars,
        "embedding_dimension": EMBEDDING_DIMENSION,
        "vector_dtype": "float32",
        "raw_vector_bytes": raw_vector_bytes,
        "raw_vector_mib": round(raw_vector_bytes / 1024 / 1024, 2),
        "by_extension": by_extension,
        "no_text_count": len(no_text),
        "no_text_files": no_text,
        "failure_count": len(failures),
        "failures": failures,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(f"Capacity scan found {len(failures)} failure(s)")


if __name__ == "__main__":
    main()
