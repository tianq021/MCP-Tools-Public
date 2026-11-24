"""采集器成熟监控：任意采集器周期结束（可收割）就退出并唤醒管家（只读）。

背景：新手采集器只跑 1 轮（cycles=1），周期结束后必须玩家互动才吐料；
本脚本轮询 sr_extractors + sr_time，发现"周期已结束、还没吐料"的设备就报警，
管家收到后台任务完成通知后提醒玩家去收（或到现场触发 sr_harvest）。

用法：
    python -B tool\\extractor_watch.py --interval 30 --timeout 1800
    python -B tool\\extractor_watch.py --only-missing   # 只报还没吐料的

退出码：0 = 有设备可收割；3 = 超时
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call  # noqa: E402


def world_time() -> float:
    res = call("sr_time", {})
    return float((res.get("data") or {}).get("world_time") or 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch extractors for a finished cycle")
    parser.add_argument("--interval", type=float, default=30.0, help="轮询间隔秒")
    parser.add_argument("--timeout", type=float, default=1800.0, help="最长运行秒数")
    args = parser.parse_args()

    started = time.time()
    while time.time() - started < args.timeout:
        try:
            now = world_time()
            res = call("sr_extractors", {})
            extractors = (res.get("data") or {}).get("extractors") or []
        except BridgeError as exc:
            print("BRIDGE_ERROR: %s" % exc, file=sys.stderr)
            time.sleep(args.interval)
            continue

        ready = []
        for item in extractors:
            end = item.get("cycle_ends_at_world_time")
            queued = int(item.get("queued_output") or 0)
            gadget = item.get("gadget_id")
            if not gadget:
                continue
            # 周期结束（或已在吐料）即视为可处理
            if queued > 0:
                ready.append({
                    "site_id": item.get("site_id"),
                    "gadget_id": gadget,
                    "zone": item.get("zone"),
                    "position": item.get("position"),
                    "state": "ejecting",
                    "queued_output": queued,
                })
            elif end is not None and float(end) <= now:
                ready.append({
                    "site_id": item.get("site_id"),
                    "gadget_id": gadget,
                    "zone": item.get("zone"),
                    "position": item.get("position"),
                    "state": "ready",
                    "cycle_ends_at": end,
                })

        if ready:
            print(json.dumps({
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "alert": "EXTRACTOR_READY",
                "world_time": now,
                "ready": ready,
            }, ensure_ascii=False), flush=True)
            return 0

        time.sleep(args.interval)

    print(json.dumps({"alert": "EXTRACTOR_WATCH_TIMEOUT"}, ensure_ascii=False), flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
