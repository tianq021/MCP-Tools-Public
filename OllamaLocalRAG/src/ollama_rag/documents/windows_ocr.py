from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from .loader import DocumentLoadResult, DocumentSection, normalize_text


DEFAULT_MAX_IMAGE_DIMENSION = 2_400


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _read_cached_result(cache_file: Path, source_sha256: str) -> dict[str, Any] | None:
    if not cache_file.is_file():
        return None
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if cached.get("source_sha256") != source_sha256:
        return None
    if not isinstance(cached.get("pages"), list):
        return None
    return cached


def _run_windows_ocr(image_path: Path, script_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-ImagePath",
            str(image_path),
        ],
        check=False,
        capture_output=True,
        timeout=180,
    )
    stdout = completed.stdout.decode("utf-8-sig", errors="replace").strip()
    stderr = completed.stderr.decode("utf-8-sig", errors="replace").strip()
    if completed.returncode != 0:
        raise RuntimeError(
            f"Windows OCR failed for {image_path.name}: {stderr or stdout}"
        )
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Windows OCR returned invalid JSON for {image_path.name}: {stdout[:500]}"
        ) from error
    if not isinstance(result, dict):
        raise RuntimeError("Windows OCR result must be an object")
    return result


def _render_page(page: Any, output_path: Path) -> tuple[int, int]:
    width, height = page.get_size()
    longest = max(float(width), float(height))
    scale = min(3.0, DEFAULT_MAX_IMAGE_DIMENSION / longest)
    bitmap = page.render(scale=max(scale, 1.0))
    image = bitmap.to_pil()
    image.save(output_path, format="PNG", optimize=False)
    return image.size


def ocr_pdf(
    path: Path,
    *,
    cache_root: Path,
    script_path: Path,
) -> DocumentLoadResult:
    path = path.resolve()
    script_path = script_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if not script_path.is_file():
        raise FileNotFoundError(script_path)

    source_sha256 = sha256_file(path)
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_file = cache_root / f"{source_sha256}.json"
    cached = _read_cached_result(cache_file, source_sha256)

    if cached is None:
        pages: list[dict[str, Any]] = []
        language: str | None = None
        document = pdfium.PdfDocument(str(path))
        try:
            with tempfile.TemporaryDirectory(
                prefix="ocr-", dir=str(cache_root)
            ) as temporary_directory:
                temporary_root = Path(temporary_directory)
                for page_index in range(len(document)):
                    page = document[page_index]
                    try:
                        image_path = temporary_root / f"page-{page_index + 1:04d}.png"
                        width, height = _render_page(page, image_path)
                        result = _run_windows_ocr(image_path, script_path)
                        language = str(result.get("language") or language or "") or None
                        pages.append(
                            {
                                "page_number": page_index + 1,
                                "text": normalize_text(str(result.get("text", ""))),
                                "image_width": width,
                                "image_height": height,
                            }
                        )
                    finally:
                        page.close()
        finally:
            document.close()

        cached = {
            "source_path": str(path),
            "source_sha256": source_sha256,
            "engine": "windows_media_ocr",
            "language": language,
            "pages": pages,
        }
        temporary_cache = cache_file.with_suffix(".json.tmp")
        temporary_cache.write_text(
            json.dumps(cached, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_cache.replace(cache_file)

    sections: list[DocumentSection] = []
    empty_pages = 0
    for page in cached["pages"]:
        page_number = int(page["page_number"])
        text = normalize_text(str(page.get("text", "")))
        if not text:
            empty_pages += 1
            continue
        sections.append(
            DocumentSection(
                source_path=path,
                source_type="pdf",
                locator_type="page",
                locator=f"page {page_number}",
                text=text,
                metadata={"page_number": page_number, "ocr": True},
            )
        )

    warnings = ["Windows OCR fallback used"]
    if empty_pages:
        warnings.append(f"{empty_pages} OCR page(s) contained no recognized text")
    return DocumentLoadResult(
        source_path=path,
        source_type="pdf",
        sections=sections,
        unit_count=len(cached["pages"]),
        empty_units=empty_pages,
        warnings=warnings,
        metadata={
            "page_count": len(cached["pages"]),
            "extraction_backend": "windows_media_ocr",
            "ocr_language": cached.get("language"),
            "source_sha256": source_sha256,
            "ocr_cache": str(cache_file),
        },
    )
