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
REPORT_PATH = PROJECT_ROOT / "storage" / "document_loader_validation.json"


def main() -> None:
    files = sorted(
        path
        for path in RAW_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx"}
    )
    results = []
    started = time.perf_counter()
    for path in files:
        row = {
            "source_path": str(path.relative_to(PROJECT_ROOT)),
            "extension": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
        }
        file_started = time.perf_counter()
        try:
            document = load_document(path)
            chunks = chunk_document(document)
            row.update(document.to_dict())
            row.update(
                {
                    "status": "ok" if document.char_count else "no_text",
                    "chunk_count": len(chunks),
                    "sample": document.sections[0].text[:240]
                    if document.sections
                    else "",
                }
            )
        except Exception as error:  # validation must report every failed file
            row.update(
                {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
        row["elapsed_seconds"] = round(time.perf_counter() - file_started, 3)
        results.append(row)

    counts = {
        status: sum(row["status"] == status for row in results)
        for status in ("ok", "no_text", "error")
    }
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "raw_root": str(RAW_ROOT),
        "file_count": len(files),
        "pdf_count": sum(path.suffix.lower() == ".pdf" for path in files),
        "docx_count": sum(path.suffix.lower() == ".docx" for path in files),
        "counts": counts,
        "total_sections": sum(row.get("section_count", 0) for row in results),
        "total_chunks": sum(row.get("chunk_count", 0) for row in results),
        "total_chars": sum(row.get("char_count", 0) for row in results),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "files": results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "files"}, ensure_ascii=False, indent=2))
    if counts["error"]:
        raise SystemExit(f"{counts['error']} document(s) failed to parse")


if __name__ == "__main__":
    main()
