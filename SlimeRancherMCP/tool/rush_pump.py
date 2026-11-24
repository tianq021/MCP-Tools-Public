#!/usr/bin/env python3
"""Rush 模式铺钱：全类型结晶各卖 ≥25 个（倍率拉满）+ 批量发卖把现金顶到位。

源码依据（EndGameV2UI / GameModeSettings）：
    结算分 = ceil(现金 × 倍率)；倍率 = 1 + Σ(卖出≥25 的结晶类型加成)
    默认 0.05，DERVISH/FIRE/MOSAIC/QUANTUM/TANGLE 各 0.08 → 17 种全满 ≈ 1.95×
    目标 75,000 → 现金 ≥ 38,462（留 buffer 到 42,000）

用法：
    python -B tool\\rush_pump.py            # 预演（只看当前状态与计划）
    python -B tool\\rush_pump.py --apply    # 真执行
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call  # noqa: E402

PLORTS = [
    "RAD_PLORT", "ROCK_PLORT", "PINK_PLORT", "GOLD_PLORT", "BOOM_PLORT", "PHOSPHOR_PLORT",
    "TABBY_PLORT", "HUNTER_PLORT", "HONEY_PLORT", "PUDDLE_PLORT", "CRYSTAL_PLORT", "QUANTUM_PLORT",
    "DERVISH_PLORT", "MOSAIC_PLORT", "TANGLE_PLORT", "FIRE_PLORT", "SABER_PLORT",
]
TARGET_CURRENCY = 42000
MIN_PER_TYPE = 26  # 判定是 >=25，留 1 个余量


def rid(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:12]}"


def currency() -> int:
    res = call("sr_player", {})
    return int((res.get("data") or {}).get("currency") or 0)


def give(item: str, count: int) -> int:
    res = call("sr_give_item", {"item_id": item, "count": count, "dry_run": False}, request_id=rid("give"))
    if not res.get("ok"):
        raise RuntimeError(f"give {item} failed: {json.dumps(res, ensure_ascii=False)[:300]}")
    return int((res.get("data") or {}).get("added") or 0)


def sell_all(item: str) -> tuple[int, int]:
    res = call("sr_sell_all", {"item_id": item, "scope": "vacpack", "count": -1, "dry_run": False},
               request_id=rid("sell"))
    if not res.get("ok"):
        raise RuntimeError(f"sell {item} failed: {json.dumps(res, ensure_ascii=False)[:300]}")
    data = res.get("data") or {}
    total = int(data.get("total_sold") or data.get("sold") or 0)
    credited = int(data.get("total_credited") or data.get("credited") or 0)
    return total, credited


def sell_until_empty(item: str) -> int:
    sold = 0
    while True:
        n, _ = sell_all(item)
        sold += n
        if n <= 0:
            break
    return sold


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    state = call("sr_state", {})
    text = ((state.get("data") or {}).get("text") or "")
    mode = "TIME_LIMIT_V2" if "gameMode=TIME_LIMIT_V2" in text else "?"
    cur = currency()
    print(f"当前模式={mode} 现金={cur}")
    if mode != "TIME_LIMIT_V2":
        print("不是 Rush 模式，停止（避免污染普通存档）。")
        return 1
    if not args.apply:
        print("预演：将对 17 种结晶各发/卖一次（各 ≥26 个），再把现金顶到", TARGET_CURRENCY)
        return 0

    report = []
    # 第一轮：每种 30 个 → 满足类型加成
    for item in PLORTS:
        before = currency()
        added = give(item, 30)
        sold = sell_until_empty(item) if added > 0 else 0
        after = currency()
        report.append({"item": item, "given": added, "sold": sold, "currency": after,
                       "delta": after - before})
        print(f"  {item:16s} given={added:3d} sold={sold:3d}  +{after - before:5d}  现金={after}")

    # 第二轮：现金不足就继续用高价结晶补（黄金 300）
    while currency() < TARGET_CURRENCY:
        before = currency()
        added = give("GOLD_PLORT", 999)
        if added <= 0:
            print("无法再发黄金结晶，停止")
            break
        sold = sell_until_empty("GOLD_PLORT")
        after = currency()
        print(f"  GOLD pump given={added} sold={sold} +{after - before} 现金={after}")
        if after == before:
            break

    final = currency()
    print("\n=== 汇总 ===")
    print(f"现金 = {final}  （1.95 倍时结算分 ≈ {int(final * 1.95)}）")
    print("类型统计（供核对，卖出≥25 的每种 +0.05/0.08）：")
    for row in report:
        print(f"  {row['item']:16s} 卖出 {row['sold']:3d}  ✓" if row["sold"] >= 25
              else f"  {row['item']:16s} 卖出 {row['sold']:3d}  ✗ 不足 25")
    return 0 if final >= 38462 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BridgeError as exc:
        print("BRIDGE_ERROR:", exc)
        raise SystemExit(2)
    except RuntimeError as exc:
        print("ERROR:", exc)
        raise SystemExit(3)
