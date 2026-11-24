"""结晶价格监控：等到目标价就退出并唤醒管家（不卖货、只读行情）。

背景：结晶饱和每小时降 25%，价格会从地板价慢慢爬回当日上限；
本脚本轮询 sr_market_advice，任意一个关注品种达到目标价就打印并退出，
DSH 收到后台任务完成通知后即可提醒玩家 / 按纪律卖出。

用法：
    python -B tool\\market_watch.py --interval 20 --timeout 1800
    python -B tool\\market_watch.py --target CRYSTAL_PLORT=48 --target RAD_PLORT=40

退出码：0 = 有品种达到目标价；3 = 超时未达到。
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

# 默认目标价 ≈ 当日上限的 75%~80%（保守，留出继续上涨空间）
DEFAULT_TARGETS = {
    "CRYSTAL_PLORT": 48,
    "RAD_PLORT": 40,
    "BOOM_PLORT": 42,
    "PHOSPHOR_PLORT": 24,
    "HONEY_PLORT": 45,
    "HUNTER_PLORT": 60,
    "QUANTUM_PLORT": 79,
}


def parse_targets(items: list[str]) -> dict[str, int]:
    targets = dict(DEFAULT_TARGETS)
    for raw in items:
        if "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        try:
            targets[key.strip()] = int(float(val))
        except ValueError:
            continue
    return targets


def snapshot() -> tuple[bool, dict[str, dict]]:
    res = call("sr_market_advice", {})
    data = res.get("data") or {}
    closed = bool(data.get("market_closed"))
    rows: dict[str, dict] = {}
    for item in data.get("advice") or []:
        item_id = item.get("item_id")
        if item_id:
            rows[item_id] = item
    return closed, rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch plort prices and wake the butler")
    parser.add_argument("--interval", type=float, default=20.0, help="轮询间隔秒")
    parser.add_argument("--timeout", type=float, default=1800.0, help="最长运行秒数")
    parser.add_argument("--target", action="append", default=[], help="item_id=价格，可重复")
    args = parser.parse_args()

    targets = parse_targets(args.target)
    started = time.time()
    last_prices: dict[str, int] = {}

    while time.time() - started < args.timeout:
        try:
            closed, rows = snapshot()
        except BridgeError as exc:
            print("BRIDGE_ERROR: %s" % exc, file=sys.stderr)
            time.sleep(args.interval)
            continue
        if closed:
            time.sleep(args.interval)
            continue

        triggered = []
        prices = {}
        for item_id, target in targets.items():
            row = rows.get(item_id)
            if not row:
                continue
            price = int(row.get("unit_price") or 0)
            prices[item_id] = price
            if price >= target:
                triggered.append({
                    "item_id": item_id,
                    "name": row.get("name"),
                    "price": price,
                    "target": target,
                    "ceiling_guess": row.get("base_value", 0) * 2,
                })

        if triggered:
            print(json.dumps({
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "alert": "PRICE_TARGET_REACHED",
                "triggered": triggered,
                "prices": prices,
            }, ensure_ascii=False), flush=True)
            return 0

        last_prices = prices
        time.sleep(args.interval)

    print(json.dumps({
        "alert": "PRICE_WATCH_TIMEOUT",
        "prices": last_prices,
        "targets": targets,
    }, ensure_ascii=False), flush=True)
    return 3


if __name__ == "__main__":
    sys.exit(main())
