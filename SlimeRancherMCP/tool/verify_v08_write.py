# -*- coding: utf-8 -*-
"""v0.8.0 写工具/边界工具验证（全部 dry_run 或无害文件操作）。"""
import json
import sys

sys.path.insert(0, r"E:\SlimeRancherMCP\mcp")
from bridge_client import call  # noqa: E402


def brief(value, limit=400):
    text = json.dumps(value, ensure_ascii=False)
    return text[:limit]


def run(name, args=None):
    try:
        resp = call(name, args or {})
    except Exception as exc:  # noqa: BLE001
        print(f"== {name}: BRIDGE ERROR {exc}")
        return
    ok = resp.get("ok")
    data = resp.get("data") or {}
    print(f"== {name}: ok={ok} elapsed={resp.get('elapsed_ms')}ms")
    if not ok:
        print("   error:", brief(resp.get("error")))
        return
    keys = list(data.keys())
    print("   keys:", keys[:12])
    for key in ("backup_dir", "count", "planned", "available", "total", "ready",
                "pending_ranchers", "current_general_offer", "slot_after_target",
                "accessible_slots", "distance", "food_id", "free_space", "note", "dry_run"):
        if key in data:
            print(f"   {key}:", brief(data[key]))
    if "files" in data:
        print("   files:", [f.get("file") for f in data["files"]][:6])
    if "sources" in data:
        print("   sources:", brief(data["sources"], 300))


if __name__ == "__main__":
    tests = [
        ("sr_save", {}),                      # 预演：只列磁盘
        ("sr_backup", {"label": "verify-v08"}),  # 真实文件备份（无害）
        ("sr_silo_port", {"plot_id": "plot0242645904"}),  # 只列仓口
        ("sr_silo_port", {"plot_id": "plot0242645904", "slot": 4}),  # 预演切槽
        ("sr_collect_all", {}),               # 预演
        ("sr_feed", {}),                      # 无喂食器 → 预期 TARGET_UNAVAILABLE
        ("sr_harvest_all", {}),               # 无钻井 → 预期 0
        ("sr_sell_all", {"item_id": "PINK_PLORT"}),  # 预演
        ("sr_store_all", {"mode": "storage"}),       # 预演
        ("sr_exchange_pending", {}),
        ("sr_teleport", {"plot_id": "plot0242645904"}),  # 预演
        ("sr_restore_backup", {}),            # 缺参数 → 预期 BAD_ARGS
    ]
    for name, args in tests:
        run(name, args)
        print()
