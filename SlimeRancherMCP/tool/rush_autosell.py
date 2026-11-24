"""Rush 模式自动卖结晶（后台轮询）。

做三件事，循环执行：
1) sr_collect_all 触发围栏集晶器吸附（有集晶器的围栏）
2) 读所有已加载的 PLORT 仓储（围栏集晶器仓），把非粉红结晶全部卖掉
3) 打印余额变化（Rush 模式三档成就看累计收入）

用法：
  python -B tool/rush_autosell.py --interval 20 --duration 1800
"""
from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, r"E:\SlimeRancherMCP\mcp")
import bridge_client as B  # noqa: E402

SKIP = {"PINK_PLORT"}


def currency() -> int:
    data = (B.call("sr_player", {}) or {}).get("data") or {}
    return int(data.get("currency") or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--duration", type=int, default=1800)
    ap.add_argument("--min-count", type=int, default=1, help="单个格子至少多少才卖")
    args = ap.parse_args()

    deadline = time.monotonic() + args.duration
    seq = 0
    start = currency()
    print(f"start currency={start}", flush=True)
    while time.monotonic() < deadline:
        try:
            B.call("sr_collect_all", {"dry_run": False}, request_id=f"autosell-col-{seq}")
            time.sleep(1)
            storages = ((B.call("sr_storage", {"storage_type": "PLORT", "limit": 50}) or {}).get("data") or {}).get("storage") or []
            for st in storages:
                sid = st.get("storage_id")
                for slot in st.get("slots") or []:
                    item = slot.get("item_id") or "NONE"
                    count = int(slot.get("count") or 0)
                    if item == "NONE" or item in SKIP or count < args.min_count:
                        continue
                    seq += 1
                    r = B.call(
                        "sr_sell",
                        {
                            "item_id": item,
                            "source_slot": slot.get("slot"),
                            "count": count,
                            "source_storage_id": sid,
                            "dry_run": False,
                        },
                        request_id=f"autosell-{seq}-{int(time.time())}",
                    )
                    if r.get("error"):
                        print(f"sell {item} x{count} failed: {r['error'].get('code')}", flush=True)
                    else:
                        d = r.get("data") or {}
                        print(
                            f"sell {item} x{count} -> +{d.get('credited')} balance={d.get('currency_after')}",
                            flush=True,
                        )
        except Exception as exc:  # 通道暂时不可用/游戏暂停时继续轮询
            print(f"loop error: {exc}", flush=True)
        time.sleep(args.interval)
    print(f"end currency={currency()} (start {start})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
