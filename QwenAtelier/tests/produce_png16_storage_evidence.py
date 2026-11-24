"""Reproducible evidence for the Python storage/export chain.

Uses the PNG produced by the native encoder, the real Store.save_output16(),
and the real HTTP routes through the application's ASGI layer (TestClient).
Writes hashes and per-sample results to tests/artifacts/png16/.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import create_app

DATA = ROOT / "tests" / "artifacts" / "png16" / "integrated" / "data"
OUT = ROOT / "tests" / "artifacts" / "png16"


def load_verifier():
    path = ROOT / "tests" / "verify_png16_phase1.py"
    spec = importlib.util.spec_from_file_location("png16_verify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    raw = (DATA / "ramp3.png").read_bytes()
    config = {
        "prompt": "相同时刻的同一张渐变图",
        "negative_prompt": "模糊",
        "width": 256,
        "height": 256,
        "steps": 4,
        "cfg": 1.0,
        "seed": 12345,
        "batch_count": 1,
        "sampler": "euler",
        "scheduler": "default",
        "vae_tiling": True,
        "output_bit_depth": 16,
        "bit_depth_requested": 16,
        "bit_depth_file": 8,  # deliberately wrong: the saved IHDR must win
        "bit_depth_engine_declared": 16,
        "precision_source": "engine_api_generate_image_u16",
        "precision_evidence": ["png_ihdr_bit_depth_16", "engine_result_bit_depth_16",
                               "engine_capabilities_include_16"],
        "engine": "stable-diffusion.cpp 0518b34",
        "engine_api": "generate_image_u16",
        "engine_output_bit_depths": [8, 16],
        "models": {"vae": {"path": "vae.safetensors", "sha256": "f" * 64, "bytes": 1}},
        "source": "qwen-studio",
    }

    with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temporary:
        root = Path(temporary)
        (root / "图片").mkdir()
        app = create_app(root)
        client = TestClient(app)
        client.__enter__()
        try:
            store = app.state.store
            identity = store.save_output16(raw, config)
            master = store.file(identity)
            preview = store.preview_file(identity)
            exported = client.get(f"/api/images/{identity}/export?depth=16")
            downloaded = client.get(f"/api/images/{identity}/file?download=true")
            eight = client.get(f"/api/images/{identity}/export?depth=8")
            item = store.image(identity)

            verifier = load_verifier()
            before = verifier.read_chunks(raw)
            after = verifier.read_chunks(master.read_bytes())
            samples = [value for row in verifier.decode_png16(exported.content)["samples"] for value in row]
            dumped = struct.unpack("<%dH" % (256 * 256 * 3), (DATA / "ramp3.u16").read_bytes())

            summary = {
                "image_id": identity,
                "master_name": master.name,
                "master_sha256": digest(master.read_bytes()),
                "idat_unchanged": (b"".join(p for k, p in before if k == b"IDAT")
                                   == b"".join(p for k, p in after if k == b"IDAT")),
                "non_text_chunks_unchanged": (
                    [c for c in before if c[0] not in (b"iTXt", b"tEXt")]
                    == [c for c in after if c[0] not in (b"iTXt", b"tEXt")]
                ),
                "export_status": exported.status_code,
                "export_sha256": digest(exported.content),
                "export_equals_master": exported.content == master.read_bytes(),
                "download_equals_master": downloaded.content == master.read_bytes(),
                "export_source_header": exported.headers.get("x-image-source"),
                "eight_export_status": eight.status_code,
                "eight_export_sha256": digest(eight.content),
                "eight_equals_preview": eight.content == preview.read_bytes(),
                "eight_source_header": eight.headers.get("x-image-source"),
                "preview_name": preview.name,
                "preview_sha256": digest(preview.read_bytes()),
                "samples": len(samples),
                "samples_equal_native_dump": list(dumped) == samples,
                "distinct_levels": len(set(samples)),
                "gallery_prompt": item["config"].get("prompt"),
                "gallery_seed": item["config"].get("seed"),
                "gallery_models": item["config"].get("models"),
                "gallery_precision_source": item["config"].get("precision_source"),
                "gallery_bit_depth_file": item["config"].get("bit_depth_file"),
            }
            (OUT / "python-export-evidence.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if all((summary["export_equals_master"], summary["download_equals_master"],
                             summary["samples_equal_native_dump"], summary["eight_equals_preview"])) else 1
        finally:
            client.__exit__(None, None, None)
            app.state.engine.worker.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
