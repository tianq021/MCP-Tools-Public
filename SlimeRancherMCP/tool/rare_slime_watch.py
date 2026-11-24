"""稀有史莱姆盯梢：轮询游戏内存里的史莱姆，出现黄金史莱姆/招财猫史莱姆就立刻退出并打印。

用途（DSH 后台任务）：一出现稀有史莱姆就唤醒管家 → 游戏内提醒 + 飞书推送 + 截图留档。

用法：
    python -B tool\\rare_slime_watch.py                     # 每 2 秒扫一次，1 小时超时
    python -B tool\\rare_slime_watch.py --interval 1.5 --timeout 7200
    python -B tool\\rare_slime_watch.py --once              # 只扫一次（测试）

退出码：
    0  发现稀有史莱姆（stdout 一行 JSON：type/actor_id/position/player_distance/at）
    3  超时未发现
    2  桥接一直不可用
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call  # noqa: E402

RARE = {"GOLD_SLIME", "LUCKY_SLIME"}
ALERT_FILE = os.path.join(ROOT, "out", "chat", "rare_alert.json")


def scan():
    """返回稀有史莱姆列表（最多各 1 条）。"""
    res = call("sr_slimes", {"radius": 0, "limit": 200, "include_feral": True})
    data = res.get("data") or {}
    found = []
    for slime in data.get("slimes") or []:
        if slime.get("item_id") in RARE:
            found.append(slime)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch for rare slimes (gold/lucky)")
    parser.add_argument("--interval", type=float, default=2.0, help="扫描间隔秒")
    parser.add_argument("--timeout", type=float, default=3600.0, help="最长运行秒数")
    parser.add_argument("--once", action="store_true", help="只扫描一次")
    args = parser.parse_args()

    started = time.time()
    ever_connected = False
    last_error = ""
    while args.once or time.time() - started < args.timeout:
        try:
            found = scan()
            ever_connected = True
        except BridgeError as exc:
            last_error = str(exc)
            time.sleep(args.interval)
            continue

        if found:
            payload = {
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(found),
                "slimes": [
                    {
                        "type": s.get("item_id"),
                        "name": s.get("name"),
                        "actor_id": s.get("actor_id"),
                        "position": s.get("position"),
                        "player_distance": s.get("player_distance"),
                        "hunger": s.get("hunger"),
                    }
                    for s in found
                ],
            }
            os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
            with open(ALERT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        if args.once:
            print("no rare slime")
            return 3
        time.sleep(args.interval)

    if not ever_connected:
        print(f"WAIT_TIMEOUT: bridge never reachable ({last_error})", file=sys.stderr)
        return 2
    print("IDLE_TIMEOUT: no rare slime", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
