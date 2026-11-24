"""等玩家回到市场附近（ScorePlort 已加载）后，自动卖出背包结晶并批量加钱。

背景：sr_sell / sr_sell_all 需要加载中的 ScorePlort（市场 deposit 点）；
玩家在野外时 ScorePlort=0，卖出会失败（TARGET_UNAVAILABLE）。

用法：python -B tool\\rush_pump_when_market.py [--cycles 3] [--timeout 1800]
退出：0 = 已完成加钱；3 = 超时（市场一直没加载）
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


def rid(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:12]}"


def market_loaded() -> tuple[bool, str]:
    res = call("sr_state", {})
    text = (res.get("data") or {}).get("text") or ""
    for line in text.splitlines():
        if "marketShutdown=" in line:
            pass
        if "gameMode=" in line:
            mode = line.strip()
        if line.strip().startswith("ScorePlort="):
            count = int(line.strip().split("=")[1])
            return count > 0, text
    return False, text


def currency() -> int:
    return int((call("sr_player", {}) or {}).get("data", {}).get("currency") or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=1800.0)
    args = ap.parse_args()

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            ok, _ = market_loaded()
        except BridgeError:
            ok = False
        if ok:
            break
        time.sleep(5)
    else:
        print(json.dumps({"event": "timeout_waiting_market"}, ensure_ascii=False), flush=True)
        return 3

    print(json.dumps({"event": "market_ready", "cash": currency()}, ensure_ascii=False), flush=True)
    for i in range(args.cycles):
        sold_total = credited = 0
        while True:
            res = call("sr_sell_all", {"item_id": "GOLD_PLORT", "scope": "vacpack", "count": -1,
                                       "dry_run": False}, request_id=rid("sell"))
            data = res.get("data") or {}
            n = int(data.get("total_sold") or data.get("sold") or 0)
            c = int(data.get("total_credited") or data.get("credited") or 0)
            sold_total += n
            credited += c
            if n <= 0:
                break
        give = call("sr_give_item", {"item_id": "GOLD_PLORT", "count": 999, "dry_run": False},
                    request_id=rid("give"))
        added = int((give.get("data") or {}).get("added") or 0)
        # 立刻卖掉刚发的
        while True:
            res = call("sr_sell_all", {"item_id": "GOLD_PLORT", "scope": "vacpack", "count": -1,
                                       "dry_run": False}, request_id=rid("sell"))
            data = res.get("data") or {}
            n = int(data.get("total_sold") or data.get("sold") or 0)
            c = int(data.get("total_credited") or data.get("credited") or 0)
            sold_total += n
            credited += c
            if n <= 0:
                break
        print(json.dumps({"event": "cycle", "round": i + 1, "sold": sold_total, "credited": credited,
                          "given": added, "cash": currency()}, ensure_ascii=False), flush=True)

    final = currency()
    print(json.dumps({"event": "done", "cash": final, "score_estimate": int(final * 1.95)},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
