"""等游戏桥接就绪 → 自动发一条游戏内回复 → 继续监听下一条消息。

用途：玩家在游戏内提问（例如「还缺什么」）时游戏可能已经/即将关闭，
本脚本在后台等桥接恢复（game_loaded=true）后把回复补发进游戏内聊天框，
然后转成正常监听（收到下一条消息即退出并唤醒 DSH）。

用法：
    python -B tool\\chat_queue_reply.py "回复内容" [--wait-seconds 7200]

退出码：0 = 收到新消息需要处理；3 = 等待游戏超时；2 = 桥接出错
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call, health  # noqa: E402
from chat_listen import listen  # noqa: E402


def wait_bridge(timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            h = health()
            snap = h.get("data") or h
            if snap.get("game_loaded"):
                return True
        except BridgeError:
            pass
        except Exception:
            pass
        time.sleep(5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--wait-seconds", type=float, default=7200.0)
    args = ap.parse_args()

    print(f"[queue] waiting for bridge (up to {int(args.wait_seconds)}s)...", flush=True)
    if not wait_bridge(args.wait_seconds):
        print("[queue] timeout: game never became ready", flush=True)
        return 3

    try:
        res = call("sr_chat_send", {"text": args.text[:400]})
    except BridgeError as exc:
        print(json.dumps({"event": "send_failed", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 2
    data = res.get("data") or {}
    print(json.dumps({"event": "reply_sent", "ok": res.get("ok"), "delivered": data.get("delivered")},
                     ensure_ascii=False), flush=True)

    return listen()


if __name__ == "__main__":
    raise SystemExit(main())
