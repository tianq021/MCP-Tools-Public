"""记录当前结晶市场行情快照（供以后对比/复盘）。

用法：
    python -B tool\\snapshot_market.py            # 抓一次并保存
    python -B tool\\snapshot_market.py --tag x    # 文件名加标签

输出：docs/data/market/market_day<天>_<时>_<标签>.json
数据：sr_market（当前价）+ sr_market_advice（基准价/饱和/建议）+ sr_time（游戏日时）+ sr_player（余额）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "data", "market")


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot plort market prices")
    parser.add_argument("--tag", default="", help="文件名标签")
    args = parser.parse_args()

    try:
        market = call("sr_market").get("data") or {}
        advice = call("sr_market_advice").get("data") or {}
        time_data = call("sr_time").get("data") or {}
        player = call("sr_player").get("data") or {}
    except BridgeError as exc:
        print(f"BRIDGE_ERROR: {exc}", file=sys.stderr)
        return 2

    day = time_data.get("day")
    hour = time_data.get("hour")
    rec = {
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "day": day,
        "hour": hour,
        "market_closed": market.get("market_closed"),
        "currency": player.get("currency"),
        "zone": player.get("zone"),
        "prices": market.get("prices", []),
        "advice": advice.get("advice", []),
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    hour_tag = f"{hour:05.2f}".replace(".", "") if isinstance(hour, (int, float)) else "na"
    name = f"market_day{day}_{hour_tag}"
    if args.tag:
        name += "_" + args.tag
    path = os.path.join(OUT_DIR, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    print(f"saved: {path}")
    print(f"day {day} hour {hour}  currency={rec['currency']}  market_closed={rec['market_closed']}")
    rows = sorted(rec["advice"], key=lambda r: -(r.get("price_ratio") or 0))
    sell_now = [r for r in rows if r.get("advice") == "SELL_NOW"]
    wait = [r for r in rows if r.get("advice") == "WAIT"]
    print("SELL_NOW: " + "、".join(f"{r['name']} {r['unit_price']}" for r in sell_now))
    print("WAIT    : " + "、".join(f"{r['name']} {r['unit_price']}" for r in wait))
    return 0


if __name__ == "__main__":
    sys.exit(main())
