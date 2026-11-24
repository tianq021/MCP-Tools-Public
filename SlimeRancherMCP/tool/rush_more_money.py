#!/usr/bin/env python3
"""Rush 加钱：批量发黄金结晶并全部卖出（Rush 市场固定价 300/个，不受饱和影响）。

用法：python -B tool\\rush_more_money.py --cycles 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call  # noqa: E402


def rid(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:12]}"


def currency() -> int:
    return int((call("sr_player", {}) or {}).get("data", {}).get("currency") or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--item", default="GOLD_PLORT")
    args = ap.parse_args()

    print("起始现金:", currency())
    for i in range(args.cycles):
        give = call("sr_give_item", {"item_id": args.item, "count": 999, "dry_run": False},
                    request_id=rid("give"))
        added = int((give.get("data") or {}).get("added") or 0)
        if added <= 0:
            print("发不进去（背包满？）", json.dumps(give, ensure_ascii=False)[:200])
            break
        sold_total = 0
        credited = 0
        while True:
            res = call("sr_sell_all", {"item_id": args.item, "scope": "vacpack", "count": -1,
                                       "dry_run": False}, request_id=rid("sell"))
            data = res.get("data") or {}
            n = int(data.get("total_sold") or data.get("sold") or 0)
            c = int(data.get("total_credited") or data.get("credited") or 0)
            sold_total += n
            credited += c
            if n <= 0:
                break
        print(f"  第 {i + 1} 轮: 发放 {added} 卖出 {sold_total} 入账 {credited} → 现金 {currency()}")

    final = currency()
    print(f"最终现金 = {final}  （×1.95 ≈ {int(final * 1.95)}）")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BridgeError as exc:
        print("BRIDGE_ERROR:", exc)
        raise SystemExit(2)
