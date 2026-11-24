#!/usr/bin/env python3
"""一键补全史莱姆百科（成就「专业范儿」）。

前提：1 代游戏已用新插件启动并进入存档（插件含 sr_pedia_unlock，见 plugin/SlimeRancherBridge/PediaTools.cs）。

用法：
    python -B tool\\finish_pedia.py              # 预演：只列缺口
    python -B tool\\finish_pedia.py --apply      # 真执行：解锁全部未解锁的非隐藏条目

判定（源码实证）：
    PediaDirector.OnUnlockedChanged → entries 全部解锁（HIDDEN_ENTRIES 3 条不算）
    → IntStat.COMPLETED_SLIMEPEDIA=1 → 成就「专业范儿」
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

from bridge_client import BridgeError, call, health  # noqa: E402

HIDDEN = {"PARTY_GORDO_SLIME", "ECHO_NOTE_GORDO_SLIME", "ECHO_NOTES"}
STEAM_BIN = r"C:\Program Files (x86)\Steam\appcache\stats\UserGameStats_1110519685_433340.bin"
SCHED_SCRIPT = r"G:\Codex_mmx\SlimeRancher成就查询_20261117\build_sr.py"


def pedia_gaps() -> tuple[list[str], int, int]:
    raw = call("sr_pedia", {"unlocked_only": False, "limit": 200})
    entries = raw.get("data", {}).get("entries", [])
    if not entries:
        raise SystemExit(f"sr_pedia 没有返回条目: {json.dumps(raw, ensure_ascii=False)[:300]}")
    missing = [e["pedia_id"] for e in entries if not e["unlocked"]]
    visible_missing = [x for x in missing if x not in HIDDEN]
    return visible_missing, len(entries), len([e for e in entries if e["unlocked"]])


def achievements_status() -> tuple[int, int, bool] | None:
    """解析本机 Steam 成就位图；失败返回 None。"""
    if not os.path.exists(SCHED_SCRIPT):
        return None
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, r'{os.path.dirname(SCHED_SCRIPT)}')\n"
        "import build_sr as B\n"
        "schema = B.load_schema('sr1')\n"
        "local = B.load_local_unlocked('433340', schema)\n"
        "allapis = set(B.SR1_TRANS.keys())\n"
        "done = local & allapis\n"
        "print(json.dumps({'done': len(done), 'total': len(allapis),"
        " 'complete_slimepedia': 'complete_slimepedia' in done}))\n"
    )
    try:
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        payload = json.loads(out.stdout.strip().splitlines()[-1])
        return payload["done"], payload["total"], payload["complete_slimepedia"]
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真执行解锁；默认只预演")
    args = ap.parse_args()

    try:
        h = health()
    except BridgeError as exc:
        print(f"桥接不可用：{exc}")
        return 1
    # /health 返回的是信封 {"ok":true,"data":{...}}；CLI 里也兼容直接返回快照的情况
    snap = h.get("data") or h
    if not snap.get("game_loaded"):
        print(f"游戏未进入存档（scene={snap.get('scene')}, main_menu={snap.get('main_menu')}）")
        return 1
    print(f"桥接 {snap.get('bridge_version') or h.get('bridge')}  scene={snap.get('scene')}  "
          f"day={snap.get('day')}  currency={snap.get('currency')}")

    missing, total, unlocked = pedia_gaps()
    print(f"百科：已解锁 {unlocked}/{total}，非隐藏缺口 {len(missing)} 条：{', '.join(missing) if missing else '无'}")

    before = achievements_status()
    if before:
        print(f"成就位图：{before[0]}/{before[1]}，complete_slimepedia={before[2]}")

    if not missing:
        print("没有缺口，无需解锁。")
        return 0
    if not args.apply:
        print("\n[预演] 加 --apply 执行：sr_pedia_unlock（解锁上面这些条目）")
        return 0

    request_id = "pedia-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    result = call("sr_pedia_unlock", {"pedia_id": "", "dry_run": False}, request_id=request_id)
    print("\n--- sr_pedia_unlock ---")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
    if not result.get("ok"):
        return 1

    time.sleep(2)
    missing2, total2, unlocked2 = pedia_gaps()
    print(f"\n回读百科：{unlocked2}/{total2}，非隐藏缺口 {len(missing2)} 条：{', '.join(missing2) if missing2 else '无'}")

    time.sleep(3)
    after = achievements_status()
    if after:
        print(f"成就位图（Steam）：{after[0]}/{after[1]}，complete_slimepedia={after[2]}")
    print("\n完成" if not missing2 else "\n仍有缺口，需要人工看 sr_pedia 输出")
    return 0 if not missing2 else 2


if __name__ == "__main__":
    raise SystemExit(main())
