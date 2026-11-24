"""无人机自动补水：电池快没电时自动用背包水补满（外部轮询，不改游戏）。

原理（源码实证）：
    DroneStationBattery 实现 LiquidConsumer.AddLiquid()，
    任何液体浇一次即把电池重置为满（DURATION_HOURS = 28 游戏小时）。
    原版没有自动供液机制，所以本脚本从外部轮询：电量低于阈值 + 背包有水
    + 该无人机站点已加载 → 调用 sr_recharge_drone（每次消耗 1 单位水，回满）。

用法：
    python -B tool\\drone_watch.py --interval 20 --timeout 3600
    python -B tool\\drone_watch.py --threshold-hours 4 --dry-run

退出码：0 = 超时正常结束；3 = 桥接不可用
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


def find_water_slot() -> int | None:
    res = call("sr_vacpack", {})
    for slot in (res.get("data") or {}).get("slots") or []:
        item = str(slot.get("item_id") or "")
        if item.startswith("WATER") and int(slot.get("count") or 0) >= 1:
            return int(slot.get("slot"))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-recharge drones with vacpack water")
    parser.add_argument("--interval", type=float, default=20.0, help="轮询间隔秒")
    parser.add_argument("--timeout", type=float, default=3600.0, help="最长运行秒数")
    parser.add_argument("--threshold-hours", type=float, default=3.0,
                        help="剩余电量低于该游戏小时数就补水")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写")
    args = parser.parse_args()

    started = time.time()
    seq = 0
    failures = 0
    while time.time() - started < args.timeout:
        try:
            now = world_time()
            res = call("sr_drones", {})
            drones = (res.get("data") or {}).get("drones") or []
        except BridgeError as exc:
            failures += 1
            if failures >= 3:
                print(json.dumps({"alert": "BRIDGE_UNAVAILABLE", "error": str(exc),
                                  "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                                 ensure_ascii=False), flush=True)
                return 3
            time.sleep(args.interval)
            continue
        failures = 0

        for drone in drones:
            if not drone.get("loaded"):
                continue
            expires = drone.get("battery_expires_at_world_time")
            if expires is None:
                continue
            hours_left = (float(expires) - now) / 3605.0
            if hours_left > args.threshold_hours:
                continue
            slot = find_water_slot()
            if slot is None:
                print(json.dumps({"alert": "NO_WATER", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "site_id": drone.get("site_id"),
                                  "hours_left": round(hours_left, 2)},
                                 ensure_ascii=False), flush=True)
                time.sleep(args.interval)
                break
            seq += 1
            try:
                out = call("sr_recharge_drone", {
                    "site_id": drone.get("site_id"),
                    "water_slot": slot,
                    "dry_run": bool(args.dry_run),
                    "request_id": "dsh-drone-recharge-%d-%d" % (int(started), seq),
                })
            except BridgeError as exc:
                print(json.dumps({"alert": "RECHARGE_FAILED",
                                  "site_id": drone.get("site_id"),
                                  "error": str(exc)}, ensure_ascii=False), flush=True)
                continue
            data = out.get("data") or {}
            print(json.dumps({
                "alert": "RECHARGED",
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "site_id": drone.get("site_id"),
                "hours_left_before": round(hours_left, 2),
                "water_before": data.get("water_count_before"),
                "water_after": data.get("water_count_after"),
                "battery_expires_after": data.get("battery_expires_after"),
                "dry_run": bool(args.dry_run),
            }, ensure_ascii=False), flush=True)
        time.sleep(args.interval)

    print(json.dumps({"alert": "DRONE_WATCH_TIMEOUT"}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
