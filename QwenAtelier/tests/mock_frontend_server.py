"""Mock API + static frontend server for UI tests. No model, no real service."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DATA16 = ROOT / "tests" / "artifacts" / "png16" / "integrated" / "data" / "ramp3.png"
RAW16 = DATA16.read_bytes()
with Image.open(DATA16) as image:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG")
    PREVIEW16 = buffer.getvalue()
buffer = io.BytesIO()
Image.new("RGB", (64, 64), "teal").save(buffer, "PNG")
RAW8 = buffer.getvalue()

MODE = {"state": "unknown"}
JOBS = []
STATS = {"load_requests": 0}


def image_config(kind):
    if kind == 16:
        return {
            "prompt": "16 bit 渐变测试",
            "negative_prompt": "",
            "width": 256,
            "height": 256,
            "steps": 4,
            "cfg": 1.0,
            "seed": 12345,
            "sampler": "euler",
            "scheduler": "default",
            "vae_tiling": True,
            "source": "qwen-studio",
            "generated_at": 1761090000,
            "elapsed_seconds": 12.5,
            "output_bit_depth": 16,
            "bit_depth_requested": 16,
            "bit_depth_file": 16,
            "bit_depth_engine_declared": 16,
            "precision_source": "engine_api_generate_image_u16",
            "precision_evidence": ["png_ihdr_bit_depth_16", "engine_result_bit_depth_16",
                                   "engine_capabilities_include_16"],
            "engine": "stable-diffusion.cpp 0518b34",
            "engine_api": "generate_image_u16",
            "engine_output_bit_depths": [8, 16],
            "models": {"diffusion_model": {"path": "qwen_image_2.1-Q6_K.gguf", "sha256": "a" * 64,
                                           "bytes": 12_000_000_000},
                       "vae": {"path": "qwen_image_2.1_vae_bf16.safetensors", "sha256": "b" * 64,
                               "bytes": 675_000_000}},
            "reproducibility": "模拟数据，仅用于界面检查。",
        }
    return {
        "prompt": "旧的 8 bit 作品",
        "negative_prompt": "",
        "width": 64,
        "height": 64,
        "steps": 8,
        "cfg": 1.0,
        "seed": 7,
        "sampler": "euler",
        "scheduler": "default",
        "vae_tiling": True,
        "source": "qwen-studio",
        "generated_at": 1761000000,
        "elapsed_seconds": 3.2,
        "models": {"diffusion_model": {"path": "qwen_image_2.1-Q4_K.gguf", "sha256": "c" * 64,
                                       "bytes": 9_000_000_000}},
    }


IMAGES = [
    {
        "id": "img16", "name": "gradient_16b.png", "width": 256, "height": 256,
        "size": len(RAW16), "created": 1761090000,
        "sha256": hashlib.sha256(RAW16).hexdigest(), "tag": "none", "deleted": 0,
        "bit_depth": 16, "config": image_config(16),
    },
    {
        "id": "img8", "name": "legacy_8b.png", "width": 64, "height": 64,
        "size": len(RAW8), "created": 1761000000,
        "sha256": hashlib.sha256(RAW8).hexdigest(), "tag": "none", "deleted": 0,
        "bit_depth": 8, "config": image_config(8),
    },
]

PRESETS = [
    {"id": "preset16", "name": "16 bit 参数", "created": 1761090000,
     "config": {"prompt": "16 preset", "negative_prompt": "", "width": 1024, "height": 1024,
                "steps": 20, "cfg": 1.0, "seed": 42, "batch_count": 1, "sampler": "euler",
                "scheduler": "default", "vae_tiling": True, "output_bit_depth": 16}},
    {"id": "preset8", "name": "旧参数", "created": 1761000000,
     "config": {"prompt": "old preset", "negative_prompt": "", "width": 512, "height": 512,
                "steps": 10, "cfg": 1.0, "seed": 9, "batch_count": 1, "sampler": "euler",
                "scheduler": "default", "vae_tiling": True}},
]


def components(loaded):
    return [
        {"key": key, "label": label, "path": f"H:/models/{key}", "name": f"{key}.gguf",
         "size": 1_000_000, "state": "available", "detail": "模拟组件", "optional": key == "vision",
         "device": "CPU", "loaded": loaded, "weight_placement": "CPU", "eager_load": False}
        for key, label in [("diffusion_model", "扩散模型"), ("text_encoder", "文本编码器"),
                           ("vae", "VAE"), ("vision", "视觉编码器")]
    ]


def status():
    state = MODE["state"]
    if state == "unknown":
        png16 = {"state": "unknown", "levels": None,
                 "detail": "引擎未加载，尚未确认 16 位输出能力；不会自动加载模型探测"}
    elif state == "unsupported":
        png16 = {"state": "unsupported", "levels": [8],
                 "detail": "当前引擎未声明 output_bit_depths，16 bit 不可用"}
    else:
        png16 = {"state": "supported", "levels": [8, 16], "detail": "当前引擎支持 16 bit 输出"}
    return {
        "gpu": None,
        "memory": {"process_rss": 0, "process_private": 0, "available": 0, "total": 0},
        "models": {"blockers": [], "components": components(state != "unknown"),
                   "files_ready": True, "individual_preload_supported": False},
        "engine": {"state": "idle" if state == "unknown" else "ready", "error": None,
                   "active_job": None, "elapsed": 0, "loaded": state != "unknown",
                   "queued": 0, "pid": None},
        "png16": png16,
        "timestamp": 1761090000,
    }


SETTINGS = {
    "diffusion_model": "H:/models/qwen_image_2.1-Q6_K.gguf",
    "text_encoder": "H:/models/Qwen3VL-8B-Instruct-Q4_K_M.gguf",
    "vae": "H:/models/qwen_image_2.1_vae_bf16.safetensors",
    "vision": "", "max_vram": 6.0, "text_encoder_cpu": True, "vae_cpu": False,
    "keep_loaded": True, "eager_load": False, "output_directory": "outputs", "port": 7860,
    "extra_args": "", "assistant_url": "https://api.deepseek.com", "assistant_model": "deepseek-flash",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, content_type="image/png", filename=None, headers=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _image(self, identity):
        return next((item for item in IMAGES if item["id"] == identity), None)

    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == "/__mode":
            requested = query.get("state", ["unknown"])[0]
            if requested in ("unknown", "unsupported", "supported"):
                MODE["state"] = requested
            self._json(200, {"state": MODE["state"]})
            return
        if path == "/__stats":
            self._json(200, STATS)
            return
        if path == "/":
            data = (WEB / "index.html").read_bytes()
            self._bytes(data, "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            target = (WEB / name).resolve()
            if target.parent != WEB.resolve() or not target.is_file():
                self._json(404, {"detail": "not found"})
                return
            content_type = {"js": "application/javascript", "css": "text/css"}.get(
                target.suffix.lstrip("."), "application/octet-stream")
            self._bytes(target.read_bytes(), content_type)
            return
        if path == "/api/session":
            self._json(200, {"token": "mock-token", "app": "Qwen Atelier", "version": "mock"})
            return
        if path == "/api/settings":
            self._json(200, SETTINGS)
            return
        if path == "/api/status":
            self._json(200, status())
            return
        if path == "/api/jobs":
            self._json(200, JOBS)
            return
        if path == "/api/images":
            self._json(200, [{k: v for k, v in item.items()} for item in IMAGES])
            return
        if path == "/api/presets":
            self._json(200, PRESETS)
            return
        if path == "/api/assistant/config":
            self._json(200, {"base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                             "has_key": False, "saved": False,
                             "system_prompt": {"path": "prompts/system.md", "chars": 0,
                                               "persona_chars": 0, "custom": False}})
            return
        if path.startswith("/api/images/"):
            parts = path.split("/")
            identity = parts[3]
            item = self._image(identity)
            if item is None:
                self._json(404, {"detail": "图片不存在"})
                return
            action = parts[4] if len(parts) > 4 else ""
            if action == "":
                self._json(200, item)
                return
            if action == "preview":
                if identity == "img16":
                    self._bytes(PREVIEW16)
                else:
                    self._json(404, {"detail": "这张图没有 8 位预览"})
                return
            if action == "file":
                is_download = query.get("download", ["false"])[0] == "true"
                data = RAW16 if identity == "img16" else RAW8
                filename = item["name"] if is_download else None
                self._bytes(data, filename=filename)
                return
            if action == "export":
                depth = query.get("depth", ["8"])[0]
                if depth == "16":
                    if identity == "img16":
                        self._bytes(RAW16, filename="gradient_16b.png",
                                    headers={"X-Image-Bit-Depth": "16", "X-Image-Source": "master"})
                    else:
                        self._json(409, {"detail": "这张图落盘时只有 8 bit/通道，颜色信息已经丢失，"
                                                 "无法恢复为真正的 16 位；本阶段不提供容器转换。"})
                else:
                    data = PREVIEW16 if identity == "img16" else RAW8
                    self._bytes(data, filename="gradient_16b_8bit.png",
                                headers={"X-Image-Bit-Depth": "8",
                                         "X-Image-Source": "derived-8bit-preview"})
                return
        self._json(404, {"detail": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        if parsed.path == "/api/gallery/scan":
            self._json(200, {"count": 0, "errors": []})
            return
        if parsed.path == "/api/jobs":
            payload = json.loads(body or b"{}")
            job = {"id": f"mock{len(JOBS) + 1}", "status": "queued", "config": payload,
                   "created": 1761090000, "image_ids": [], "stage": "排队中", "progress": 0}
            JOBS.insert(0, job)
            self._json(202, job)
            return
        if parsed.path == "/api/models/load":
            STATS["load_requests"] += 1
            self._json(200, {"state": "loading"})
            return
        self._json(404, {"detail": "not found"})


def start():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


if __name__ == "__main__":
    server = start()
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_address[1]}"}))
    threading.Event().wait()
