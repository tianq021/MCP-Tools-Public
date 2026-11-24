from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ollama_rag.documents import chunk_document, load_document  # noqa: E402
from ollama_rag.models import OllamaEmbeddingClient  # noqa: E402
from ollama_rag.vectorstore import build_database, search_database  # noqa: E402


RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "downloads" / "H_Downloads"
PDF_PATH = (
    RAW_ROOT
    / "2024级学生企业实训实施方案"
    / "附件2：酷课网校外企业实习系统操作说明（学生端）.pdf"
)
DOCX_PATH = (
    RAW_ROOT
    / "2024级学生企业实训实施方案"
    / "关于落实2024级学生2026学年秋季学期教学工作的通知.docx"
)
OUTPUT_DIR = PROJECT_ROOT / "storage" / "vector_db" / "pdf_docx_test"
DATABASE_PATH = OUTPUT_DIR / "pdf_docx_vectors.sqlite3"
REPORT_PATH = OUTPUT_DIR / "pdf_docx_vector_test_result.json"


TEST_CASES = [
    {
        "query": "在酷课网实习系统里搜索不到实习企业时，学生应该怎样添加企业？",
        "expected_source": PDF_PATH.name,
        "expected_terms": ["点击添加企业", "填写企业信息"],
        "expected_locator": "page 1",
    },
    {
        "query": "上传实习保险材料时，保险文件必须包含哪些内容？",
        "expected_source": PDF_PATH.name,
        "expected_terms": ["实习保单", "参保人员名单"],
        "expected_locator": "page 2",
    },
    {
        "query": "普通校外企业实训申请的教务处审核截止日期是什么时候，之后还受理吗？",
        "expected_source": DOCX_PATH.name,
        "expected_terms": ["10月18日", "10月19日", "不再受理"],
        "expected_locator": "paragraph 22",
    },
    {
        "query": "2024级第4学期挂科课程的补考安排在哪几天？",
        "expected_source": DOCX_PATH.name,
        "expected_terms": ["8月17日至8月21日"],
        "expected_locator": "paragraph 38",
    },
]


def locator_contains(locator_range: str, expected_locator: str) -> bool:
    expected_match = re.fullmatch(r"(page|paragraph) (\d+)", expected_locator)
    if expected_match is None:
        return locator_range == expected_locator
    locator_type, expected_number_text = expected_match.groups()
    numbers = [
        int(number)
        for number in re.findall(rf"{re.escape(locator_type)} (\d+)", locator_range)
    ]
    if not numbers:
        return False
    expected_number = int(expected_number_text)
    return min(numbers) <= expected_number <= max(numbers)


def main() -> None:
    documents = [load_document(PDF_PATH), load_document(DOCX_PATH)]
    chunks = [chunk for document in documents for chunk in chunk_document(document)]

    client = OllamaEmbeddingClient()
    client.require_model()
    document_batch = client.embed_documents([chunk.text for chunk in chunks])
    database_result = build_database(
        DATABASE_PATH, chunks, document_batch.vectors, model=client.model
    )

    query_batch = client.embed_queries([test["query"] for test in TEST_CASES])
    cases = []
    for test, query_vector in zip(TEST_CASES, query_batch.vectors, strict=True):
        results = search_database(DATABASE_PATH, query_vector, top_k=3)
        combined_text = "\n".join(result["text"] for result in results)
        source_matched = results[0]["source_name"] == test["expected_source"]
        terms_matched = all(term in combined_text for term in test["expected_terms"])
        locator_matched = any(
            result["source_name"] == test["expected_source"]
            and locator_contains(result["locator"], test["expected_locator"])
            for result in results
        )
        cases.append(
            {
                **test,
                "top1_source": results[0]["source_name"],
                "top1_locator": results[0]["locator"],
                "source_matched": source_matched,
                "terms_matched_in_top3": terms_matched,
                "locator_matched_in_top3": locator_matched,
                "passed": source_matched and terms_matched and locator_matched,
                "results": results,
            }
        )

    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "ollama_version": client.version(),
        "model": client.model,
        "dimension": document_batch.dimension,
        "documents": [document.to_dict() for document in documents],
        "chunk_count": len(chunks),
        "document_embedding": document_batch.timing_dict(),
        "query_embedding": query_batch.timing_dict(),
        "database": database_result,
        "passed": all(case["passed"] for case in cases),
        "cases": cases,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "documents": len(documents),
                "chunks": len(chunks),
                "dimension": document_batch.dimension,
                "cases": [
                    {
                        "query": case["query"],
                        "passed": case["passed"],
                        "top1_source": case["top1_source"],
                        "top1_locator": case["top1_locator"],
                    }
                    for case in cases
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not report["passed"]:
        raise SystemExit("PDF/DOCX vector retrieval test failed")


if __name__ == "__main__":
    main()
