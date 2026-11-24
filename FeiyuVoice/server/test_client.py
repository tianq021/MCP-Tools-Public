"""网关自检：健康检查 + 声线列表 + 合成一句短话（走 OpenAI 兼容接口）。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("FEIYU_URL", "http://127.0.0.1:9880").rstrip("/")
KEY = os.environ.get("FEIYU_API_KEY", "").strip()
OUT = os.environ.get("FEIYU_OUT_DIR", r"E:\feiyu\outputs\mcp")
TEXT = os.environ.get("FEIYU_TEST_TEXT", "自检通过，我现在可以说话了。")


def req(method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if KEY:
        r.add_header("Authorization", f"Bearer {KEY}")
    try:
        with urllib.request.urlopen(r, timeout=900) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    t0 = time.perf_counter()
    code, body = req("GET", "/health")
    print(f"[health] HTTP {code} {body.decode('utf-8', 'replace')[:300]}  ({time.perf_counter() - t0:.2f}s)")
    if code != 200:
        return 1
    code, body = req("GET", "/v1/voices")
    print(f"[voices] HTTP {code} {body.decode('utf-8', 'replace')[:300]}")
    t1 = time.perf_counter()
    code, body = req("POST", "/v1/audio/speech", {"model": "feiyu-cute", "voice": "cute", "input": TEXT, "response_format": "wav"})
    dt = time.perf_counter() - t1
    print(f"[speech] HTTP {code} 字节={len(body)} 耗时={dt:.2f}s")
    if code != 200:
        print(body[:500])
        return 1
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"selftest_{time.strftime('%H%M%S')}.wav")
    open(path, "wb").write(body)
    print(f"[save] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
