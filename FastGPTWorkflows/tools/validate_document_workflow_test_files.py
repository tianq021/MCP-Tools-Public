import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = ROOT / "generated" / "document_workflow_tests"
PDF_DIR = ROOT / "output" / "pdf" / "document_workflow_tests"

FILES = {
    "docx": DOC_DIR / "01_星港计划培训通知_错别字与矛盾测试.docx",
    "pdf": PDF_DIR / "02_社区节能阶段报告_事实冲突与安全测试.pdf",
    "markdown_errors": DOC_DIR / "03_智能知识中心发布说明_术语与结构测试.md",
    "txt_privacy": DOC_DIR / "04_客户投诉回复草稿_语气隐私与矛盾测试.txt",
    "markdown_control": DOC_DIR / "05_服务变更标准说明_正常对照样本.md",
    "readme": DOC_DIR / "README_测试说明与预期问题.md",
    "zip": DOC_DIR / "文档审校工作流_测试文档包.zip",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    errors = []
    for label, path in FILES.items():
        if not path.exists() or path.stat().st_size == 0:
            errors.append(f"{label}: missing or empty: {path}")

    if errors:
        print(json.dumps({"errors": errors, "pass": False}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    doc = Document(FILES["docx"])
    docx_text = "\n".join(p.text for p in doc.paragraphs)
    docx_text += "\n" + "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    for marker in ("顺力上线", "共组织两场", "第三场", "圆满的顺利完成"):
        if marker not in docx_text:
            errors.append(f"docx: missing marker: {marker}")

    pdf_reader = PdfReader(FILES["pdf"])
    pdf_text = "\n".join(page.extract_text() or "" for page in pdf_reader.pages)
    for marker in ("23栋", "合计", "IGNORE PREVIOUS INSTRUCTIONS", "test_demo_12345_DO_NOT_USE"):
        if marker not in pdf_text:
            errors.append(f"pdf: missing marker: {marker}")

    markdown_text = FILES["markdown_errors"].read_text(encoding="utf-8")
    for marker in ("v2.0", "AI资料库 2.1", "百分之百", "没有任何已知限制"):
        if marker not in markdown_text:
            errors.append(f"markdown_errors: missing marker: {marker}")

    txt_text = FILES["txt_privacy"].read_text(encoding="utf-8")
    for marker in ("3个工作日", "5个工作日", "138-0000-0000", "test_token_DO_NOT_USE"):
        if marker not in txt_text:
            errors.append(f"txt_privacy: missing marker: {marker}")

    with ZipFile(FILES["zip"]) as archive:
        archive_names = set(archive.namelist())
    expected_names = {path.name for label, path in FILES.items() if label != "zip"}
    if archive_names != expected_names:
        errors.append(
            f"zip: entries differ: expected={sorted(expected_names)} actual={sorted(archive_names)}"
        )

    summary = {
        "docxParagraphs": len(doc.paragraphs),
        "docxTables": len(doc.tables),
        "pdfPages": len(pdf_reader.pages),
        "zipEntries": sorted(archive_names),
        "files": {
            label: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for label, path in FILES.items()
        },
        "errors": errors,
        "pass": not errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
