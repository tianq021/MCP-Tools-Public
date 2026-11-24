"""本地语音播报：直接 HTTP 调 feiyu 网关合成 wav 并播放（不依赖 MCP 通道）。

用法：
    python -B tool\\feiyu_say.py "要说的话" [--voice cute|gentle] [--speed 0.95]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import winsound

GATEWAY = os.environ.get("FEIYU_URL", "http://127.0.0.1:9880").rstrip("/")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "voice")


def synth(text: str, voice: str, speed: float, timeout: float = 180.0) -> bytes:
    body = json.dumps(
        {"input": text, "voice": voice, "speed": speed, "response_format": "wav"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(GATEWAY + "/v1/audio/speech", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--voice", default="cute", choices=["cute", "gentle"])
    ap.add_argument("--speed", type=float, default=0.95)
    ap.add_argument("--no-play", action="store_true")
    args = ap.parse_args()

    started = time.time()
    try:
        data = synth(args.text, args.voice, args.speed)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, time.strftime("say_%Y%m%d-%H%M%S") + ".wav")
    with open(path, "wb") as fh:
        fh.write(data)

    played = "skipped"
    if not args.no_play:
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME)
            played = "played"
        except Exception as exc:  # noqa: BLE001
            played = f"play_failed: {exc}"

    print(
        json.dumps(
            {
                "ok": True,
                "path": path,
                "bytes": len(data),
                "played": played,
                "elapsed_s": round(time.time() - started, 1),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
