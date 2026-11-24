"""把管家回复推进游戏内聊天框（sr_chat_send）。

用法：
    python -B tool\\chat_reply.py "回复内容"
    echo 回复内容 | python -B tool\\chat_reply.py -

说明：文本最长 400 字；只写内存队列和游戏内 UI，不动存档。
"""
from __future__ import annotations

import json
import os
import sys
import time
from chat_listen import listen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call  # noqa: E402

REPLY_LOG = os.path.join(ROOT, "out", "chat", "replies.jsonl")


def append_log(entry: dict) -> None:
    """把每条回复写进本地日志，便于事后核对「到底发没发」。"""
    try:
        os.makedirs(os.path.dirname(REPLY_LOG), exist_ok=True)
        with open(REPLY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    args = sys.argv[1:]
    rearm = args[:1] == ["--listen"]
    if rearm:
        args = args[1:]
    if not args:
        print("usage: chat_reply.py [--listen] <text or ->", file=sys.stderr)
        return 64
    if args[0] == "-":
        text = sys.stdin.read().strip()
    else:
        text = " ".join(args).strip()
    if not text:
        print("empty text", file=sys.stderr)
        return 65
    entry = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "length": len(text[:400]),
        "text": text[:400],
    }
    try:
        res = call("sr_chat_send", {"text": text[:400]})
    except BridgeError as exc:
        entry["ok"] = False
        entry["error"] = str(exc)
        append_log(entry)
        print(f"BRIDGE_ERROR: {exc}", file=sys.stderr)
        return 2
    data = res.get("data") or {}
    entry["ok"] = bool(res.get("ok"))
    entry["delivered"] = data.get("delivered")
    entry["bridge_incoming_total"] = data.get("incoming_total")
    append_log(entry)
    print(json.dumps(res, ensure_ascii=False, indent=2), file=sys.stderr if rearm else sys.stdout, flush=True)
    if rearm and res.get("ok"):
        return listen()
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
