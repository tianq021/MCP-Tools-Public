"""地图数据装置盯梢：轮询 sr_map_data，一旦当前加载范围内出现未解锁的地图数据装置就打印坐标并退出。

用法（DSH 后台任务）：
    python -B tool\\map_data_watch.py                      # 每 5 秒扫一次，30 分钟超时
    python -B tool\\map_data_watch.py --interval 4 --timeout 1200

退出码：
    0  找到（stdout 一行 JSON：zone/position/player_distance/map_unlocked/hologram_active）
    3  超时未找到
    2  桥接不可用
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch for map data entries in loaded range")
    parser.add_argument("--interval", type=float, default=5.0, help="扫描间隔秒")
    parser.add_argument("--timeout", type=float, default=1800.0, help="最长运行秒数")
    args = parser.parse_args()

    started = time.time()
    ever_ok = False
    last_err = ""
    while time.time() - started < args.timeout:
        try:
            res = call("sr_map_data")
            data = res.get("data") or {}
            ever_ok = True
        except BridgeError as exc:
            last_err = str(exc)
            time.sleep(args.interval)
            continue

        items = data.get("items") or []
        if items:
            payload = {
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "player_position": data.get("player_position"),
                "items": items,
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        time.sleep(args.interval)

    if not ever_ok:
        print("WAIT_FAILED: bridge unreachable (%s)" % last_err, file=sys.stderr)
        return 2
    print("IDLE_TIMEOUT: map data entry not in loaded range", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
