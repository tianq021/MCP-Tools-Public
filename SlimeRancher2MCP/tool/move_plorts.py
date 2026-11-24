"""把场景容器（结晶收集器等）里的结晶一次性搬进**各自区域**的地块仓库。

背景：sr2_transfer 的 from/to 编号是按「到玩家的距离」排序的，玩家在牧场走动时编号
会翻转，MCP 分两次调用很容易打错目标（实测出现过 same_storage / 空计划）。
本脚本在**同一个进程内**完成 读快照 → 规划 → 执行 → 回读核对，把竞态窗口压到毫秒级；
执行前还会用 dry_run 校验计划里的物品 ID 与来源一致，不安全就整体放弃。

目标仓库的选择（2026-11-21 玩家规则「每个区域的结晶放进自己区域的仓库」）：
  ① 桥接 0.5.25+ 给已加载容器带 zone：优先搬进与来源收集器同 zone 的筒仓；
  ② 旧桥接没有 zone：退回「离来源最近的已加载筒仓」近似；
  ③ 本区域没有筒仓时退回主仓兜底；`--target` 可强制指定一个目标。

用法：
    python -B tool/move_plorts.py                    # 预演（默认，不写游戏）
    python -B tool/move_plorts.py --apply batch-1    # 执行；同一次批处理重试要沿用同名批次
    python -B tool/move_plorts.py --target plot:...  # 强制指定单个目标仓库
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

import bridge_client as bc  # noqa: E402

DEFAULT_TARGET = "plot:plot0031824558/58d5bd4fc903e1c49aba61495aa74014"
PLORT_SUFFIX = "Plort"


def snapshot() -> dict:
    r = bc.call("sr2_storage_list")
    if not r.get("ok"):
        raise RuntimeError(f"storage_list 失败: {r.get('error')}")
    data = r["data"]
    if not data.get("available"):
        raise RuntimeError(f"storage_list 不可用: {data.get('reason')}")
    return data


def distance(origin: dict | None, position: dict | None) -> float | None:
    if not origin or not position:
        return None
    try:
        return math.dist((origin["x"], origin["y"], origin["z"]),
                         (position["x"], position["y"], position["z"]))
    except (KeyError, TypeError):
        return None


def plot_silos(data: dict) -> list[dict]:
    return [item for item in data.get("items") or []
            if item.get("kind") == "plot_silo" and item.get("loaded") and item.get("storage_key")]


def nearest(silos: list[dict], origin: dict | None) -> dict | None:
    def key(silo):
        value = distance(origin, silo.get("position"))
        return 1e12 if value is None else value
    return min(silos, key=key) if silos else None


def target_for(data: dict, source: dict, override: str | None = None) -> str:
    """来源容器所在区域的仓库；优先同 zone 筒仓，旧桥接退回最近的已加载筒仓。"""
    if override:
        return override
    origin = source.get("position") or data.get("player_position")
    silos = plot_silos(data)
    zone = source.get("zone")
    if zone and any(silo.get("zone") for silo in silos):
        same_zone = [silo for silo in silos if silo.get("zone") == zone]
        if same_zone:
            return nearest(same_zone, origin)["storage_key"]
        return DEFAULT_TARGET          # 本区域没有筒仓 → 主仓兜底
    silo = nearest(silos, origin)
    return silo["storage_key"] if silo else DEFAULT_TARGET


def sources(data: dict) -> list[tuple[int, str, int, dict]]:
    """返回 [(容器编号, 结晶 ID, 数量, 容器行)]，只挑场景容器里的结晶。"""
    out = []
    for item in data.get("items") or []:
        if item.get("kind") != "scene_storage":
            continue
        for slot in item.get("slots") or []:
            sid = slot.get("id") or ""
            if slot.get("count", 0) > 0 and sid.endswith(PLORT_SUFFIX):
                # 用不带命名空间的名字，Matches 接受裸 ID
                out.append((item["index"], sid.split(".")[-1], slot["count"], item))
    return out


def assign(data: dict, override: str | None) -> list[tuple[int, str, int, str]]:
    return [(index, item, count, target_for(data, store, override))
            for index, item, count, store in sources(data)]


def plan_ok(index: int, item: str, target: str) -> bool:
    r = bc.call("sr2_transfer", {"from": str(index), "to": target, "item": item, "dry_run": True})
    d = r.get("data") if r.get("ok") else None
    if not d or not d.get("available"):
        return False
    plan = d.get("plan") or []
    return len(plan) >= 1 and all(p.get("name") == item or p.get("id", "").endswith(item) for p in plan)


def execute(index: int, item: str, target: str, batch: str) -> dict:
    args = {"from": str(index), "to": target, "item": item, "dry_run": False}
    return bc.call("sr2_transfer", args, request_id=f"{batch}-{item}-{index}")


def total_plorts(data: dict, target: str) -> dict[str, int]:
    got: dict[str, int] = {}
    for item in data.get("items") or []:
        if item.get("storage_key") != target:
            continue
        for slot in item.get("slots") or []:
            if slot.get("count", 0) <= 0:
                continue
            name = slot.get("name") or slot.get("id")
            got[name] = got.get(name, 0) + slot["count"]
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description="把收集器里的结晶搬进各自区域的仓库")
    ap.add_argument("--target", default=None, help="强制指定目标仓库的地块键（默认按来源区域自动选）")
    ap.add_argument("--apply", metavar="BATCH", help="执行；BATCH 是本次动作的稳定批次 ID")
    args = ap.parse_args()

    try:
        data = snapshot()
    except Exception as error:
        print(f"读不到仓库快照（游戏没开或桥接未加载）：{error}")
        return 2
    jobs = assign(data, args.target)
    player = data.get("player_position") or {}
    print(f"玩家位置 ({player.get('x')}, {player.get('y')}, {player.get('z')})")
    if not jobs:
        print("没有需要搬运的场景结晶。")
        return 0

    before = {target: total_plorts(data, target) for _, _, _, target in jobs}
    print(f"待搬 {len(jobs)} 笔：")
    for index, item, count, target in jobs:
        print(f"  容器#{index} {item} × {count} → {target}")

    # 竞态防护：执行前用当前快照逐笔 dry_run 校验；编号或目标漂移就整体重读重算。
    for attempt in range(3):
        stale = [job for job in jobs if not plan_ok(job[0], job[1], job[3])]
        if not stale:
            break
        print(f"[尝试 {attempt + 1}] 有 {len(stale)} 笔需要重算，重读快照…")
        time.sleep(0.15)
        data = snapshot()
        jobs = assign(data, args.target)
        if not jobs:
            print("重读后已无待搬结晶。")
            return 0
        before = {target: total_plorts(data, target) for _, _, _, target in jobs}
    else:
        print("预演校验连续失败，放弃执行（编号仍在漂移）。")
        return 3

    if not args.apply:
        print("\n预演通过；加 --apply <批次ID> 执行。")
        return 0

    print("\n开始执行…")
    moved: list[str] = []
    failed: list[str] = []
    for index, item, count, target in jobs:
        r = execute(index, item, target, args.apply)
        d = r.get("data") if r.get("ok") else None
        if d and d.get("moved_count"):
            for row in d["moved"]:
                moved.append(f"{row.get('name')} ×{row.get('moved')} → {target} 槽{row.get('target_slot')}")
        else:
            reason = (d or {}).get("reason") or (r.get("error") or {}).get("code") or "unknown"
            failed.append(f"容器#{index} {item}: {reason}")

    print("\n已搬：")
    for line in moved:
        print(f"  ✔ {line}")
    for line in failed:
        print(f"  ✘ {line}")
    after_data = snapshot()
    for target in sorted({job[3] for job in jobs}):
        print(f"\n目标仓库 {target} 结晶：{before.get(target)} → {total_plorts(after_data, target)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
