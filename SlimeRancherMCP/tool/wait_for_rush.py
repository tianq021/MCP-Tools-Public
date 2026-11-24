"""等玩家开一局 Rush（TIME_LIMIT_V2）后退出，唤醒 DSH 接管。

轮询 sr_saves：当前存档 game_mode == TIME_LIMIT_V2 且游戏已进档 → 打印信息并退出 0。
超时返回 3。

用法：python -B tool\\wait_for_rush.py [--timeout 1800]
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


def current_save() -> dict | None:
    res = call("sr_saves", {})
    saves = (res.get("data") or {}).get("saves") or []
    for s in saves:
        if s.get("current"):
            return s
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            h = health()
            snap = h.get("data") or h
            if snap.get("game_loaded"):
                cur = current_save()
                if cur and cur.get("game_mode") == "TIME_LIMIT_V2":
                    print(json.dumps({
                        "event": "rush_detected",
                        "save_name": cur.get("save_name"),
                        "day": cur.get("day"),
                        "currency": cur.get("currency"),
                        "game_mode": cur.get("game_mode"),
                        "saved_at": cur.get("saved_at"),
                    }, ensure_ascii=False), flush=True)
                    return 0
        except BridgeError:
            pass
        except Exception as exc:  # 防御：轮询期间任何异常都不退出
            print(json.dumps({"event": "poll_error", "error": repr(exc)}), file=sys.stderr, flush=True)
        time.sleep(args.interval)

    print(json.dumps({"event": "timeout"}, ensure_ascii=False), flush=True)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
