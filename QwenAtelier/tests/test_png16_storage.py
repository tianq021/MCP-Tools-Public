"""Storage, export and preview behaviour for true 16-bit masters.

The 16-bit input is the PNG produced by the native encoder in phase 1/2, not a
Pillow re-encode. Pixels are compared sample by sample after decoding.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image

from app.main import Generation, create_app
from app.png16 import read_header

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "artifacts" / "png16" / "integrated" / "data"


def _load_verifier():
    path = Path(__file__).with_name("verify_png16_phase1.py")
    spec = importlib.util.spec_from_file_location("png16_verify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_config() -> dict:
    return {
        "prompt": "雨后的山间小屋，中文元数据测试",
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
        "bit_depth_file": 8,  # deliberately wrong: the IHDR must win
        "bit_depth_engine_declared": 16,
        "precision_source": "engine_api_generate_image_u16",
        "precision_evidence": [
            "png_ihdr_bit_depth_16",
            "engine_result_bit_depth_16",
            "engine_capabilities_include_16",
        ],
        "engine": "stable-diffusion.cpp 0518b34",
        "engine_api": "generate_image_u16",
        "engine_output_bit_depths": [8, 16],
        "models": {
            "diffusion_model": {"path": "qwen.gguf", "sha256": "c" * 64, "bytes": 100},
            "vae": {"path": "vae.safetensors", "sha256": "d" * 64, "bytes": 200},
        },
        "input": None,
        "source": "qwen-studio",
        "runtime_settings": {"vae_tiling": True},
    }


class Png16StorageTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / ".tmp"
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temp.name)
        (self.root / "图片").mkdir()
        self.app = create_app(self.root)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {"X-Studio-Token": self.client.get("/api/session").json()["token"]}
        self.store = self.app.state.store
        self.engine = self.app.state.engine
        if not (DATA / "ramp3.png").is_file():
            self.skipTest("native phase-1 artifacts are missing")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.app.state.engine.worker.join(timeout=2)
        self.temp.cleanup()

    def raw16(self):
        return (DATA / "ramp3.png").read_bytes()

    def wait_job(self, job_id, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.engine.jobs.get(job_id)
            if job and job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.02)
        self.fail(f"job {job_id} did not finish")

    def fake_load(self, settings, cancelled=None):
        # Mock the post-load state so capability probing runs as it does in production.
        self.engine.state = "ready"
        self.engine.loaded_settings = settings
        self.engine.process = Mock(pid=999)
        self.engine.process.poll.return_value = None

    def test_png16_status_three_states_without_loading_model(self):
        with patch.object(self.engine, "request", side_effect=AssertionError("must not probe while idle")):
            self.assertEqual(self.engine.png16_status()["state"], "unknown")
        self.assertEqual(self.client.get("/api/status").json()["png16"]["state"], "unknown")

        self.engine.state = "ready"
        self.engine.process = Mock(pid=999)
        self.engine.process.poll.return_value = None
        with patch.object(self.engine, "request", return_value={"output_bit_depths": [8, 16]}):
            self.assertEqual(self.engine.png16_status(refresh=True)["state"], "supported")
        self.engine._png16_status = None
        self.engine._png16_checked_at = 0.0
        with patch.object(self.engine, "request", return_value={"output_bit_depths": [8]}):
            self.assertEqual(self.engine.png16_status(refresh=True)["state"], "unsupported")
        self.engine._png16_status = None
        self.engine._png16_checked_at = 0.0
        with patch.object(self.engine, "request", side_effect=RuntimeError("probe failed")):
            self.assertEqual(self.engine.png16_status(refresh=True)["state"], "unknown")

    def test_master_bytes_metadata_gallery_and_preview(self):
        raw = self.raw16()
        config = sample_config()
        identity = self.store.save_output16(raw, config)
        master = self.store.file(identity)
        saved = master.read_bytes()

        # The file is 16-bit and its image chunks are byte-identical to the input.
        self.assertEqual(read_header(saved[:33])["bit_depth"], 16)
        verifier = _load_verifier()
        before = verifier.read_chunks(raw)
        after = verifier.read_chunks(saved)
        self.assertEqual(
            [chunk for chunk in before if chunk[0] not in (b"iTXt", b"tEXt")],
            [chunk for chunk in after if chunk[0] not in (b"iTXt", b"tEXt")],
        )
        self.assertEqual(
            b"".join(payload for kind, payload in before if kind == b"IDAT"),
            b"".join(payload for kind, payload in after if kind == b"IDAT"),
        )
        self.assertTrue(any(b"qwen_studio" in payload for kind, payload in after if kind == b"iTXt"))
        self.assertTrue(any(b"parameters" in payload for kind, payload in after if kind == b"iTXt"))

        # Independent decode: stored samples equal the input, and the raw dump.
        original = verifier.decode_png16(raw)
        stored = verifier.decode_png16(saved)
        self.assertEqual(original["samples"], stored["samples"])
        flat = [value for row in stored["samples"] for value in row]
        dumped = struct.unpack("<%dH" % (256 * 256 * 3), (DATA / "ramp3.u16").read_bytes())
        self.assertEqual(flat, list(dumped))

        # Gallery extraction comes from the qwen_studio chunk, not the sidecar.
        item = self.store.image(identity)
        for key in ("prompt", "negative_prompt", "width", "height", "steps", "cfg", "seed",
                    "sampler", "scheduler", "output_bit_depth",
                    "bit_depth_engine_declared", "precision_source", "engine_api"):
            self.assertEqual(item["config"].get(key), config[key], key)
        self.assertEqual(item["config"]["bit_depth_file"], 16)
        self.assertEqual(item["config"]["bit_depth_requested"], 16)
        self.assertEqual(item["config"]["models"], config["models"])
        self.assertEqual(json.loads(item["metadata"]["qwen_studio"])["prompt"], config["prompt"])
        self.assertEqual(item["config"]["precision_evidence"], config["precision_evidence"])

        sidecar = json.loads(master.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["image_sha256"], hashlib.sha256(master.read_bytes()).hexdigest())

        preview = self.store.preview_file(identity)
        self.assertEqual(preview.parent, self.store.data / "previews")
        self.assertNotEqual(preview.read_bytes(), saved)
        self.assertEqual(read_header(preview.read_bytes()[:33])["bit_depth"], 8)
        with Image.open(preview) as im:
            self.assertEqual(im.size, (256, 256))
            self.assertEqual(im.getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(im.getpixel((255, 255)), (255, 255, 255))

    def test_export_reuses_master_without_engine_and_preview_is_separate(self):
        identity = self.store.save_output16(self.raw16(), sample_config())
        master = self.store.file(identity).read_bytes()
        preview = self.store.preview_file(identity).read_bytes()

        with patch.object(self.engine, "request", side_effect=AssertionError("export must not call the engine")), \
             patch.object(self.engine, "submit", side_effect=AssertionError("export must not submit a job")):
            exported = self.client.get(f"/api/images/{identity}/export?depth=16")
            downloaded = self.client.get(f"/api/images/{identity}/file?download=true")
            eight = self.client.get(f"/api/images/{identity}/export?depth=8")
            preview_response = self.client.get(f"/api/images/{identity}/preview")

        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.content, master)
        self.assertEqual(hashlib.sha256(exported.content).hexdigest(), hashlib.sha256(master).hexdigest())
        self.assertEqual(exported.headers["x-image-source"], "master")
        self.assertEqual(downloaded.content, master)
        self.assertEqual(eight.content, preview)
        self.assertEqual(eight.headers["x-image-source"], "derived-8bit-preview")
        self.assertIn("_8bit.png", eight.headers["content-disposition"])
        self.assertEqual(preview_response.content, preview)
        self.assertEqual(self.client.get(f"/api/images/{identity}/export?depth=9").status_code, 400)

    def test_legacy_8bit_export16_is_refused_and_8bit_regression(self):
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), "teal").save(buffer, "PNG")
        raw = buffer.getvalue()
        config = {"prompt": "old", "negative_prompt": "", "width": 32, "height": 32,
                  "steps": 8, "cfg": 1.0, "seed": 5, "batch_count": 1,
                  "sampler": "euler", "scheduler": "default", "vae_tiling": True,
                  "source": "qwen-studio"}
        identity = self.store.save_output(raw, config)
        master = self.store.file(identity).read_bytes()

        refused = self.client.get(f"/api/images/{identity}/export?depth=16")
        self.assertEqual(refused.status_code, 409)
        detail = refused.json()["detail"]
        self.assertIn("8 bit", detail)
        self.assertIn("无法恢复", detail)
        self.assertNotIn("_16b.png", " ".join(p.name for p in (self.root / "outputs").glob("*.png")))

        self.assertEqual(self.client.get(f"/api/images/{identity}/export?depth=8").content, master)
        self.assertEqual(self.client.get(f"/api/images/{identity}/file?download=true").content, master)
        self.assertEqual(self.client.get(f"/api/images/{identity}/preview").status_code, 404)
        self.assertEqual(self.store.image(identity)["config"]["source"], "qwen-studio")

    def mock_engine(self, capabilities, response_images):
        calls = []

        def request(method, path, **kwargs):
            if path == "/sdcpp/v1/capabilities":
                return capabilities
            if method == "POST":
                calls.append(kwargs["json"])
                return {"id": f"mock{len(calls)}"}
            return {"status": "completed", "started": 1000, "completed": 2000,
                    "result": {"images": response_images}}

        return calls, request

    def test_default_8bit_request_and_save_are_unchanged(self):
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), "green").save(buffer, "PNG")
        images = [{"b64_json": base64.b64encode(buffer.getvalue()).decode()}]
        calls, request = self.mock_engine({}, images)
        config = Generation(prompt="legacy path", seed=3, steps=4,
                            content_confirmed=True).model_dump()
        with patch("app.engine.model_status", return_value={"blockers": []}), \
             patch.object(self.engine, "_load", side_effect=self.fake_load), \
             patch.object(self.engine, "model_hashes", return_value={"test": "hash"}), \
             patch.object(self.engine, "environment_record", return_value={}), \
             patch.object(self.engine, "request", side_effect=request):
            job = self.client.post("/api/jobs", headers=self.headers, json=config).json()
            result = self.wait_job(job["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("output_bit_depth", calls[0])
        item = self.store.images()[0]
        self.assertNotIn("output_bit_depth", item["config"])
        self.assertEqual(read_header(self.store.file(item["id"]).read_bytes()[:33])["bit_depth"], 8)
        with self.assertRaises(KeyError):
            self.store.preview_file(item["id"])

    def test_engine_rejects_16bit_when_capability_is_missing(self):
        calls, request = self.mock_engine({"output_bit_depths": [8]}, [])
        with patch("app.engine.model_status", return_value={"blockers": []}), \
             patch.object(self.engine, "_load", side_effect=self.fake_load), \
             patch.object(self.engine, "model_hashes", return_value={"test": "hash"}), \
             patch.object(self.engine, "environment_record", return_value={}), \
             patch.object(self.engine, "request", side_effect=request):
            job = self.client.post("/api/jobs", headers=self.headers, json=Generation(
                prompt="capability gate", seed=7, steps=4, output_bit_depth=16,
                content_confirmed=True).model_dump()).json()
            result = self.wait_job(job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("未声明 16 位", result["error"])
        self.assertEqual(calls, [])
        self.assertEqual(self.store.images(), [])

    def test_engine_16bit_job_saves_provenance_and_keeps_generation_uncached(self):
        raw = self.raw16()
        images = [{"b64_json": base64.b64encode(raw).decode(), "bit_depth": 16}]
        calls, request = self.mock_engine({"output_bit_depths": [8, 16]}, images)
        config = Generation(prompt="same params", seed=7, steps=4, output_bit_depth=16,
                            content_confirmed=True).model_dump()
        with patch("app.engine.model_status", return_value={"blockers": []}), \
             patch.object(self.engine, "_load", side_effect=self.fake_load), \
             patch.object(self.engine, "model_hashes", return_value={"test": "hash"}), \
             patch.object(self.engine, "environment_record", return_value={}), \
             patch.object(self.engine, "request", side_effect=request):
            first = self.client.post("/api/jobs", headers=self.headers, json=config).json()
            second = self.client.post("/api/jobs", headers=self.headers, json=config).json()
            first_result = self.wait_job(first["id"])
            second_result = self.wait_job(second["id"])

        self.assertEqual(first_result["status"], "completed", first_result.get("error"))
        self.assertEqual(second_result["status"], "completed", second_result.get("error"))
        self.assertEqual(len(calls), 2)
        self.assertEqual([call["output_bit_depth"] for call in calls], [16, 16])
        stored = sorted(self.store.images(), key=lambda image: image["created"])
        self.assertEqual(len(stored), 2)
        item = self.store.image(stored[0]["id"])
        self.assertEqual(item["config"]["bit_depth_file"], 16)
        self.assertEqual(item["config"]["bit_depth_engine_declared"], 16)
        self.assertEqual(item["config"]["precision_source"], "engine_api_generate_image_u16")
        self.assertEqual(item["config"]["engine_api"], "generate_image_u16")
        self.assertEqual(item["config"]["engine_output_bit_depths"], [8, 16])
        self.assertIn("png_ihdr_bit_depth_16", item["config"]["precision_evidence"])
        self.assertTrue(self.store.preview_file(stored[0]["id"]).is_file())


if __name__ == "__main__":
    unittest.main()
