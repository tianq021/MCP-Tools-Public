from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ollama_rag.api import IndexService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="建立正式的全量本地向量库。")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--no-ocr", action="store_true", help="不处理扫描 PDF。")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    service = IndexService.create(PROJECT_ROOT)
    result = service.build(
        batch_size=args.batch_size,
        enable_ocr=not args.no_ocr,
        progress=lambda message: print(message, flush=True),
    )
    print(
        json.dumps(
            {
                "database": str(result.database),
                "report": str(result.report),
                "files": result.file_count,
                "indexed_documents": result.indexed_document_count,
                "chunks": result.chunk_count,
                "ocr_documents": result.ocr_document_count,
                "no_text": result.no_text_count,
                "failures": result.failure_count,
                "embedded_chunks": result.embedded_chunk_count,
                "reused_chunks": result.reused_chunk_count,
                "dimension": result.dimension,
                "elapsed_seconds": result.elapsed_seconds,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result.failure_count:
        raise SystemExit(f"Full index completed with {result.failure_count} failure(s)")


if __name__ == "__main__":
    main()
