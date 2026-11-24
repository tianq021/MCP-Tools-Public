import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "tmp" / "document_service_reference_test"
if TEST_DIR.exists():
    shutil.rmtree(TEST_DIR)
TEST_DIR.mkdir(parents=True)

os.environ["DOCUMENT_SERVICE_OUTPUT_DIR"] = str(TEST_DIR)
os.environ["DOCUMENT_SERVICE_PUBLIC_BASE_URL"] = "https://documents.example.test"
sys.path.insert(0, str(ROOT / "document_service_reference"))

from document_service import OUTPUT_DIR, render_payload  # noqa: E402
from docx import Document  # noqa: E402
from pypdf import PdfReader  # noqa: E402


def payload(fmt, content="# 修改后的文档\n\n项目名称已改为星港系统。"):
    return {
        "taskReady": True,
        "finalContent": content,
        "sourceFiles": [
            {
                "filename": f"source.{fmt}",
                "text": "项目名称为星海系统。",
                "extension": fmt,
            }
        ],
        "taskSpec": {"operationMode": "targeted_replace"},
        "output": {
            "filename": f"modified.{fmt}",
            "format": fmt,
            "preserveLayout": "preferred",
        },
    }


results = {}
formats = ("docx", "pdf", "md", "txt", "csv", "json", "html", "py")
for fmt in formats:
    result = render_payload(payload(fmt))
    results[fmt] = result
    assert result["downloadUrl"].startswith("https://documents.example.test/files/")
    assert result["metadata"]["sizeBytes"] > 0
    assert len(result["metadata"]["sha256"]) == 64
    assert result["metadata"]["hashScope"] == "generated_file_binary"
    assert result["metadata"]["createdTime"].endswith("Z")
    assert result["sourceMetadata"][0]["hashScope"] == "parsed_text_utf8"
    assert len(result["sourceMetadata"][0]["parsedTextSha256"]) == 64
    assert result["sourceMetadata"][0]["originalBinaryMetadataStatus"] == "unavailable_from_fastgpt_readFiles"

files = list(OUTPUT_DIR.iterdir())
assert len(files) == len(formats)

docx_path = next(path for path in files if path.suffix == ".docx")
docx_text = "\n".join(p.text for p in Document(docx_path).paragraphs)
assert "星港系统" in docx_text

pdf_path = next(path for path in files if path.suffix == ".pdf")
pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_path).pages)
assert "星港系统" in pdf_text

fallback = render_payload(
    {
        **payload("docx", ""),
        "finalContent": "",
        "sourceFiles": [{"filename": "source.pdf", "text": "可提取的源正文", "extension": "pdf"}],
        "output": {"filename": "fallback.docx", "format": "docx", "preserveLayout": "not_required"},
    }
)
assert fallback["status"] == "source_text_fallback_new_file"

failed = False
try:
    render_payload({**payload("pdf", ""), "finalContent": "", "sourceFiles": []})
except ValueError:
    failed = True
assert failed, "无正文时必须拒绝生成"

print(
    {
        "outputDir": str(OUTPUT_DIR),
        "formats": sorted(results),
        "metadataFields": sorted(results["docx"]["metadata"]),
        "sourceMetadataFields": sorted(results["docx"]["sourceMetadata"][0]),
        "fallbackStatus": fallback["status"],
        "noContentRejected": failed,
        "pass": True,
    }
)
