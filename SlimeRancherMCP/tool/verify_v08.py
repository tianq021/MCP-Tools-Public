# -*- coding: utf-8 -*-
"""v0.8.0 只读工具验证：直接走 HTTP 通道（不依赖 MCP 会话是否已重载）。"""
import json
import sys

sys.path.insert(0, r"E:\SlimeRancherMCP\mcp")
from bridge_client import call  # noqa: E402


def brief(value, limit=3):
    if isinstance(value, list):
        return json.dumps(value[:limit], ensure_ascii=False)[:400]
    return json.dumps(value, ensure_ascii=False)[:400]


def run(name, args=None):
    try:
        resp = call(name, args or {})
    except Exception as exc:  # noqa: BLE001
        print(f"== {name}: BRIDGE ERROR {exc}")
        return None
    ok = resp.get("ok")
    print(f"== {name}: ok={ok} elapsed={resp.get('elapsed_ms')}ms")
    if not ok:
        print("   error:", brief(resp.get("error")))
        return resp
    data = resp.get("data") or {}
    if name == "sr_watch":
        print("   alerts:", data.get("count"), "| summary:", brief(data.get("summary"), 20))
        for a in (data.get("alerts") or [])[:6]:
            print("   -", a.get("severity"), a.get("code"), a.get("message"))
    elif name == "sr_market_advice":
        print("   total:", data.get("total"), "closed:", data.get("market_closed"))
        for row in (data.get("advice") or [])[:6]:
            print("   -", row["item_id"], row["unit_price"], "base", row["base_value"],
                  "sat", row["saturation_ratio"], row["advice"])
    elif name == "sr_saves":
        print("   count:", data.get("count"), "current:", data.get("current"))
        for row in (data.get("saves") or []):
            print("   -", row.get("save_name"), "day", row.get("day"), "money", row.get("currency"),
                  "current", row.get("current"), row.get("summary_error", ""))
    elif name == "sr_gordos":
        print("   total:", data.get("total"), "popped:", data.get("popped_total"),
              "loaded:", data.get("loaded_total"))
        for row in (data.get("gordos") or [])[:5]:
            print("   -", row["gordo_id"], "eaten", row["eaten"], "/", row.get("target_count"),
                  "popped", row["popped"], "key", row["drops_key"], "zone", row.get("zone"))
    elif name == "sr_pods":
        print("   total:", data.get("total"), "open:", data.get("open_total"), "locked:", data.get("locked_total"))
    elif name == "sr_doors":
        print("   total:", data.get("total"), "locked:", data.get("locked_total"),
              "open:", data.get("open_total"), "closed:", data.get("closed_total"))
        for row in (data.get("doors") or [])[:5]:
            print("   -", row["door_id"], row["state"], "zone", row.get("zone"),
                  "region", row.get("locked_region"))
    elif name == "sr_corrals":
        print("   total:", data.get("total"))
        for row in (data.get("corrals") or [])[:5]:
            print("   -", row["plot_id"], "loaded", row["loaded"], "slimes", row.get("slime_count"),
                  "loose", row.get("loose_plort_count"), "warnings", row.get("warnings"))
    elif name == "sr_slimes":
        print("   total_in_memory:", data.get("total_in_memory"), "page:", len(data.get("slimes") or []))
        for row in (data.get("slimes") or [])[:5]:
            print("   -", row["item_id"], "hunger", row.get("hunger"),
                  "corral", row.get("nearest_corral"), "dist", row.get("player_distance"))
    elif name == "sr_slime_info":
        for row in (data.get("slimes") or [])[:3]:
            print("   -", row["item_id"], row["name"], "diet", row.get("food_groups"),
                  "fav", row.get("favorites"), "produces", row.get("produces"))
    elif name == "sr_overview":
        print("   player:", brief(data.get("player"), 5))
        print("   market top:", brief((data.get("market") or {}).get("top_prices"), 3))
        print("   slimes:", brief(data.get("slimes"), 5))
        print("   ranch:", brief(data.get("ranch"), 8))
        print("   warnings:", data.get("warnings"))
    else:
        print("   data:", brief(data, 8))
    return resp


if __name__ == "__main__":
    tests = [
        ("sr_watch", {}),
        ("sr_market_advice", {}),
        ("sr_saves", {}),
        ("sr_gordos", {}),
        ("sr_pods", {}),
        ("sr_doors", {}),
        ("sr_corrals", {}),
        ("sr_slimes", {"limit": 5}),
        ("sr_slime_info", {"item_id": "PINK_SLIME"}),
        ("sr_overview", {}),
    ]
    for name, args in tests:
        run(name, args)
        print()
