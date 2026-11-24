"""大肥鱼语音网关：本机 HTTP 服务，给任何 AI 模型/软件调用。

接口：
  GET  /health                     健康检查（模型是否就绪、队列长度）
  GET  /v1/voices                  可用声线（feiyu-cute / feiyu-gentle）
  GET  /v1/models                  OpenAI 风格模型列表
  POST /v1/audio/speech            OpenAI 兼容：{model,input,voice,response_format,speed} → 音频字节
  POST /v1/speech                  原生异步：提交任务 → 202 {job_id}
  GET  /v1/jobs/{id}               查询任务状态
  GET  /v1/jobs/{id}/audio         下载结果音频
  POST /v1/jobs/{id}/cancel        取消任务（GPU 推理在 chunk 之间检查取消标志）

启动：
  E:\\feiyu\\.venv\\Scripts\\python.exe E:\\feiyu\\server\\gateway.py --port 9880

鉴权（可选）：环境变量 FEIYU_API_KEY 或 E:\\feiyu\\server\\api_key.txt 存在时，要求 Bearer Token。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import engine as feiyu_engine  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "cache")
API_KEY_FILE = os.path.join(ROOT, "api_key.txt")
MAX_CACHE_FILES = 200

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_TASK_Q: "queue.Queue[str]" = queue.Queue()
_CACHE: dict[str, str] = {}


def _api_key() -> str:
    key = os.environ.get("FEIYU_API_KEY", "").strip()
    if key:
        return key
    if os.path.isfile(API_KEY_FILE):
        try:
            return open(API_KEY_FILE, encoding="utf-8").read().strip()
        except OSError:
            return ""
    return ""


def _style_from_voice(voice: str | None) -> str:
    v = (voice or "cute").strip().lower()
    v = v.replace("feiyu-", "").replace("feiyu_", "")
    aliases = {"cute": "cute", "可爱": "cute", "gentle": "gentle", "温柔": "gentle"}
    return aliases.get(v, "cute")


def _encode(audio: np.ndarray, sr: int, fmt: str) -> tuple[bytes, str]:
    fmt = (fmt or "wav").lower()
    buf = io.BytesIO()
    if fmt == "mp3":
        tmp_wav = os.path.join(CACHE_DIR, f".tmp_{uuid.uuid4().hex}.wav")
        sf.write(tmp_wav, audio, sr, format="WAV", subtype="PCM_16")
        tmp_mp3 = tmp_wav[:-4] + ".mp3"
        try:
            ffmpeg = shutil.which("ffmpeg") or r"C:\ffmpeg-8.0.1-essentials_build\bin\ffmpeg.exe"
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", tmp_wav, "-codec:a", "libmp3lame", "-q:a", "4", tmp_mp3], check=True)
            data = open(tmp_mp3, "rb").read()
            return data, "audio/mpeg"
        finally:
            for p in (tmp_wav, tmp_mp3):
                if os.path.exists(p):
                    os.remove(p)
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), "audio/wav"


def _cache_key(text: str, style: str, speed: float, fmt: str, gain: bool) -> str:
    return hashlib.sha256(f"{text}|{style}|{speed}|{fmt}|{gain}".encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    path = _CACHE.get(key)
    if path and os.path.exists(path):
        return path
    return None


def _cache_put(key: str, data: bytes, ext: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{key}.{ext}")
    with open(path, "wb") as f:
        f.write(data)
    _CACHE[key] = path
    if len(_CACHE) > MAX_CACHE_FILES:
        for k in list(_CACHE)[: len(_CACHE) - MAX_CACHE_FILES]:
            old = _CACHE.pop(k)
            if os.path.exists(old):
                os.remove(old)
    return path


def _synthesize(text: str, style: str, speed: float, fmt: str, cancel: threading.Event | None = None) -> bytes:
    key = _cache_key(text, style, speed, fmt, True)
    cached = _cache_get(key)
    if cached:
        return open(cached, "rb").read()
    eng = feiyu_engine.get_engine()
    audio, sr = eng.synthesize(text, style=style, speed=speed, cancel=cancel)
    data, _ = _encode(audio, sr, fmt)
    _cache_put(key, data, "mp3" if fmt == "mp3" else "wav")
    return data


def _worker() -> None:
    while True:
        job_id = _TASK_Q.get()
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
        if not job or job.get("cancel"):
            continue
        job["status"] = "running"
        job["started_at"] = time.time()
        cancel = threading.Event()
        job["_cancel_event"] = cancel
        try:
            data = _synthesize(job["text"], job["style"], job["speed"], job["format"], cancel)
            job["audio"] = data
            job["status"] = "success"
            job["finished_at"] = time.time()
            job["url"] = f"/v1/jobs/{job_id}/audio"
        except feiyu_engine.CancelledError:
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["trace"] = traceback.format_exc()[-2000:]
            job["finished_at"] = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = "FeiyuVoice/0.1"

    def log_message(self, fmt: str, *args) -> None:  # 安静一点
        if os.environ.get("FEIYU_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------- 工具 ----------
    def _auth_ok(self) -> bool:
        key = _api_key()
        if not key:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {key}"

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_audio(self, data: bytes, ctype: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _require_auth(self) -> bool:
        if self._auth_ok():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": {"message": "invalid api key", "type": "auth_error"}})
        return False

    # ---------- 路由 ----------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            eng = feiyu_engine._ENGINE  # noqa: SLF001
            eng_ready = eng is not None
            return self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "engine": "feiyu-cosyvoice3",
                    "runtime": os.environ.get("FEIYU_RUNTIME", "windows-native"),
                    "vllm": os.environ.get("FEIYU_VLLM", "0") == "1",
                    "model_version": getattr(eng, "manifest", {}).get("version") if eng_ready else None,
                    "torch": __import__("torch").__version__,
                    "ready": eng_ready,
                    "current_style": getattr(eng, "_current_style", None) if eng_ready else None,
                    "queue": _TASK_Q.qsize(),
                    "voices": [v["id"] for v in (eng.voices() if eng_ready else [])],
                    "sample_rate": 24000,
                },
            )
        if path == "/v1/voices":
            return self._send_json(HTTPStatus.OK, {"data": feiyu_engine.get_engine().voices()})
        if path == "/v1/models":
            return self._send_json(
                HTTPStatus.OK,
                {"object": "list", "data": [{"id": v["id"], "object": "model", "owned_by": "feiyu"} for v in feiyu_engine.get_engine().voices()]},
            )
        m = re.fullmatch(r"/v1/jobs/([0-9a-f]{8,})", path)
        if m:
            job = _JOBS.get(m.group(1))
            if not job:
                return self._send_json(HTTPStatus.NOT_FOUND, {"error": {"message": "job not found"}})
            return self._send_json(
                HTTPStatus.OK,
                {k: v for k, v in job.items() if not k.startswith("_") and k != "audio"},
            )
        m = re.fullmatch(r"/v1/jobs/([0-9a-f]{8,})/audio", path)
        if m:
            job = _JOBS.get(m.group(1))
            if not job:
                return self._send_json(HTTPStatus.NOT_FOUND, {"error": {"message": "job not found"}})
            if job.get("status") != "success" or job.get("audio") is None:
                return self._send_json(HTTPStatus.CONFLICT, {"error": {"message": f"job status={job.get('status')}"}})
            ctype = "audio/mpeg" if job.get("format") == "mp3" else "audio/wav"
            return self._send_audio(job["audio"], ctype)
        return self._send_json(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if not self._require_auth():
            return

        if path == "/v1/audio/speech":
            body = self._read_json()
            text = (body.get("input") or body.get("text") or "").strip()
            if not text:
                return self._send_json(HTTPStatus.BAD_REQUEST, {"error": {"message": "input 不能为空"}})
            style = _style_from_voice(body.get("voice") or body.get("model"))
            speed = float(body.get("speed") or 1.0)
            fmt = (body.get("response_format") or "wav").lower()
            try:
                data = _synthesize(text, style, speed, fmt)
            except Exception as exc:  # noqa: BLE001
                return self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": {"message": f"{type(exc).__name__}: {exc}", "type": "synthesis_error"}},
                )
            return self._send_audio(data, "audio/mpeg" if fmt == "mp3" else "audio/wav")

        if path == "/v1/speech":
            body = self._read_json()
            text = (body.get("text") or body.get("input") or "").strip()
            if not text:
                return self._send_json(HTTPStatus.BAD_REQUEST, {"error": {"message": "text 不能为空"}})
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "status": "queued",
                "text": text,
                "style": _style_from_voice(body.get("style") or body.get("voice") or body.get("model")),
                "speed": float(body.get("speed") or 1.0),
                "format": (body.get("format") or "wav").lower(),
                "created_at": time.time(),
            }
            with _JOBS_LOCK:
                _JOBS[job_id] = job
            _TASK_Q.put(job_id)
            return self._send_json(HTTPStatus.ACCEPTED, {"job_id": job_id, "status": "queued", "poll": f"/v1/jobs/{job_id}"})

        m = re.fullmatch(r"/v1/jobs/([0-9a-f]{8,})/cancel", path)
        if m:
            job = _JOBS.get(m.group(1))
            if not job:
                return self._send_json(HTTPStatus.NOT_FOUND, {"error": {"message": "job not found"}})
            job["cancel"] = True
            ev = job.get("_cancel_event")
            if ev is not None:
                ev.set()
            return self._send_json(HTTPStatus.OK, {"job_id": job["id"], "status": job.get("status")})

        return self._send_json(HTTPStatus.NOT_FOUND, {"error": {"message": "not found"}})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type")
        self.end_headers()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9880)
    ap.add_argument("--preload", action="store_true", help="启动时就加载模型（否则首个请求时才加载）")
    args = ap.parse_args()
    os.makedirs(CACHE_DIR, exist_ok=True)

    # 先绑定端口再加载模型：端口就是单实例锁，避免多个客户端同时拉起导致重复加载 6GB 模型
    try:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"[gateway] 端口 {args.port} 已被占用（可能已有实例在运行）：{exc}", flush=True)
        return 0

    threading.Thread(target=_worker, daemon=True, name="feiyu-worker").start()
    if args.preload:
        print("[gateway] 预加载模型…", flush=True)
        feiyu_engine.get_engine()
        print("[gateway] 模型就绪", flush=True)
    print(f"[gateway] 监听 http://{args.host}:{args.port}  (auth={'on' if _api_key() else 'off'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[gateway] 退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

