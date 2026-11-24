#!/usr/bin/env python3
"""【已实测：不推荐使用】Rush 死亡快进实验 —— 实测 Rush 死亡只前进约 5 游戏小时、扣约 41% 现金。

实测（2026-11-20 22:42）：一次死亡 242,885 → 143,503（-40.9%），时间 day1 15:23 → 22:10。
即 Rush 用的是 hoursLostOnDeath≈5h（不是 hoursTilDawnOnDeath 跳到黎明），
死亡换 5 分钟但扣四成现金，**不划算，不要用来冲成就**。保留此脚本仅作机制验证工具。

原理（源码实证）：
    PlayerRadAbsorber.Absorb → PlayerModel.AddRads：辐射超过 maxRads 后每 10 点扣 10 血
    → 血量归零 → DeathHandler.Kill(SLIME_RAD)
    → PlayerDeathHandler：LockUntil(max(GetNextDawnAfterNextDusk() 或 HoursFromNow(hoursLostOnDeath),
                                           WorldTime()+390), 5s)

用法：
    python -B tool\\rush_death_boost.py            # 预演（只看状态）
    python -B tool\\rush_death_boost.py --apply    # 执行 1 轮（生成辐射史莱姆 + 等死亡）
    python -B tool\\rush_death_boost.py --apply --rounds 3

安全：
    每次死亡后读现金，低于 MIN_CASH 立即停止（7.5 万成就只需 38,462 现金 ×1.95 倍）
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

MIN_CASH = 80000
SLIME_COUNT = 20
MAX_WAIT_S = 150


def rid(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:12]}"


def snapshot() -> dict:
    res = call("sr_player", {})
    data = res.get("data") or {}
    state = call("sr_state", {})
    text = (state.get("data") or {}).get("text") or ""
    info = {}
    for line in text.splitlines():
        if line.startswith("worldTime="):
            info = dict(p.split("=", 1) for p in line.split() if "=" in p)
        if "gameMode=" in line:
            for tok in line.split():
                if tok.startswith("gameMode="):
                    info["mode"] = tok.split("=", 1)[1]
    return {
        "currency": int(data.get("currency") or 0),
        "health": int(data.get("health") or 0),
        "max_health": int(data.get("max_health") or 0),
        "radiation": int(data.get("radiation") or 0),
        "day": int(float(info.get("day", 0))),
        "hour": float(info.get("hour", 0)),
        "mode": info.get("mode", "?"),
        "pos": data.get("position"),
    }


def spawn_rad() -> None:
    res = call("sr_spawn_slime", {"slime_id": "RAD_SLIME", "count": SLIME_COUNT, "distance": 1,
                                  "dry_run": False}, request_id=rid("rad"))
    if not res.get("ok"):
        raise RuntimeError(json.dumps(res, ensure_ascii=False)[:300])


def wait_death(before: dict, timeout: float) -> dict | None:
    """轮询到日/时跳变（死亡快进）或血量明显下降后恢复。"""
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(3)
        try:
            cur = snapshot()
        except BridgeError:
            time.sleep(2)
            continue
        print(f"  health={cur['health']}/{cur['max_health']} rad={cur['radiation']:3d} "
              f"day={cur['day']} hour={cur['hour']:.1f} cash={cur['currency']}", flush=True)
        # 死亡判定：日期/时间跳变，或血量回满且现金下降
        if cur["day"] != before["day"] or abs(cur["hour"] - before["hour"]) > 2:
            return cur
        if cur["health"] <= 0:
            return cur
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()

    s = snapshot()
    print(f"模式={s['mode']} day={s['day']} hour={s['hour']:.2f} cash={s['currency']} "
          f"health={s['health']}/{s['max_health']}")
    if s["mode"] != "TIME_LIMIT_V2":
        print("不是 Rush 模式，停止")
        return 1
    if not args.apply:
        print(f"预演：每轮生成 {SLIME_COUNT} 只辐射史莱姆（脚下 1m），最多等 {MAX_WAIT_S}s")
        print(f"安全线：现金低于 {MIN_CASH} 停止")
        return 0

    for i in range(args.rounds):
        before = snapshot()
        if before["currency"] < MIN_CASH:
            print(f"现金 {before['currency']} < 安全线 {MIN_CASH}，停止加速")
            return 0
        print(f"\n--- 第 {i + 1} 轮：generated {SLIME_COUNT} rad slimes (day {before['day']} {before['hour']:.1f}) ---",
              flush=True)
        spawn_rad()
        after = wait_death(before, MAX_WAIT_S)
        if after is None:
            print("本轮没等到死亡（可能玩家移动出辐射圈），需要再试或手动站住")
            return 2
        print(f"死亡/跳变完成 → day={after['day']} hour={after['hour']:.1f} cash={after['currency']} "
              f"（相对上一轮 {(after['currency'] - before['currency']):+d}）")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BridgeError as exc:
        print("BRIDGE_ERROR:", exc)
        raise SystemExit(2)
    except RuntimeError as exc:
        print("ERROR:", exc)
        raise SystemExit(3)
