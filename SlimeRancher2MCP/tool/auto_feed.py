"""自动补充喂食器食物：优先放史莱姆「最喜欢的食物」，其次放它们正常吃的那一类。

用法（项目根目录下运行）：
    python -B tool/auto_feed.py                     # 预演：列出每台喂食器准备补什么、为什么
    python -B tool/auto_feed.py --apply <ID>        # 执行；重试沿用同一 ID
    python -B tool/auto_feed.py --apply <ID> --reply    # 顺便把摘要发到游戏聊天框
    python -B tool/auto_feed.py --to 100            # 目标份数（默认补到槽位上限）

选食物规则（2026-11-24 玩家要求）：
  1. 先看这台喂食器所在围栏里养的史莱姆，收集它们的「最爱食物」；
  2. 最爱没货，就用它们食谱里「额外可吃」的食物；
  3. 再没有，就按食物组（肉/水果/蔬菜）挑同类里仓库有货的；
  4. 都不行就保持当前喂食器里的种类（只要仓库还有）。

其它约束：
  * 只动地块筒仓里的食物，不碰背包和其它场景容器；
  * 只处理已加载的围栏喂食器（玩家在牧场时）；
  * 喂食器编号会随玩家距离漂移，执行前重新定位 + dry_run 校验目标身份。
"""
from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp"))
from bridge_client import BridgeError, call  # noqa: E402


class ActionError(Exception):
    pass


def rpc(name: str, args: dict | None = None, request_id: str | None = None) -> dict:
    response = call(name, args or {}, request_id)
    if not response.get("ok"):
        error = response.get("error") or {}
        raise ActionError(f"{error.get('code', 'bridge_error')} {error.get('message', '')}".strip())
    data = response.get("data") or {}
    if data.get("available") is False:
        raise ActionError(str(data.get("reason", "unavailable")))
    return data


def pages(name: str, args: dict | None = None, limit: int = 100) -> list[dict]:
    rows: list[dict] = []
    offset: int | None = 0
    seen: set[int] = set()
    while offset is not None and offset not in seen:
        seen.add(offset)
        data = rpc(name, {**(args or {}), "limit": limit, "offset": offset})
        rows.extend(data.get("items") or [])
        offset = data.get("next_offset")
    return rows


def short(value: str | None) -> str:
    return (value or "").split(".")[-1]


def horizontal(a: dict, b: dict) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["z"]) - float(b["z"]))


def snapshot() -> dict:
    return rpc("sr2_storage_list")


def feeder_rows(data: dict) -> list[dict]:
    """已加载的围栏喂食器（含位置和稳定 selector）。"""
    rows = []
    for store in data.get("items") or []:
        if store.get("kind") != "scene_storage":
            continue
        if "Feeder" not in str(store.get("object_name") or ""):
            continue
        slot = (store.get("slots") or [{}])[0]
        rows.append({
            "index": store.get("index"),
            "selector": store.get("selector"),
            "position": store.get("position"),
            "food": short(slot.get("id")),
            "count": int(slot.get("count") or 0),
            "max": int(slot.get("max") or 100),
        })
    return rows


def silo_stock(data: dict) -> dict[str, list[tuple[str, int]]]:
    """{食物短名: [(筒仓 key, 数量)]}，按数量从多到少。"""
    stock: dict[str, list[tuple[str, int]]] = {}
    for store in data.get("items") or []:
        if store.get("kind") != "plot_silo" or not store.get("storage_key"):
            continue
        for slot in store.get("slots") or []:
            name = short(slot.get("id"))
            count = int(slot.get("count") or 0)
            if not name or count <= 0:
                continue
            stock.setdefault(name, []).append((store["storage_key"], count))
    for rows in stock.values():
        rows.sort(key=lambda row: -row[1])
    return stock


def build_menu(snapshot_data: dict) -> dict:
    """算出每台喂食器「应先放什么食物」的候选顺序。"""
    plots = pages("sr2_plots")
    corrals = pages("sr2_corrals")
    diet = pages("sr2_slime_diet")

    corral_plots = [p for p in plots if p.get("type") == "CORRAL" and p.get("position")]
    corral_slimes = {c.get("id"): [short(s) for s in (c.get("slimes") or {})] for c in corrals}
    by_slime = {short(row.get("id")): row for row in diet if row.get("id")}

    # 食物组 → 具体食物（只用基础史莱姆推，避免 Largo 两组混在一起）
    group_foods: dict[str, list[str]] = {}
    for row in diet:
        if row.get("is_largo"):
            continue
        items = [short(x) for x in (row.get("favorites") or [])] + \
                [short(x) for x in (row.get("additional_foods") or [])]
        for group in row.get("food_groups") or []:
            bucket = group_foods.setdefault(group, [])
            for item in items:
                if item and item not in bucket:
                    bucket.append(item)

    stock = silo_stock(snapshot_data)
    plan_rows = []
    for feeder in feeder_rows(snapshot_data):
        nearest = min(corral_plots, key=lambda p: horizontal(p["position"], feeder["position"])) \
            if corral_plots and feeder.get("position") else None
        species = corral_slimes.get(nearest["id"], []) if nearest else []
        favorites, additional, groups = [], [], []
        for stem in species:
            row = by_slime.get(stem)
            if not row:
                continue
            favorites += [short(x) for x in (row.get("favorites") or [])]
            additional += [short(x) for x in (row.get("additional_foods") or [])]
            groups += list(row.get("food_groups") or [])

        candidates: list[tuple[str, str]] = []      # (食物, 理由)
        seen: set[str] = set()

        def push(item: str, reason: str) -> None:
            if item and item not in seen:
                seen.add(item)
                candidates.append((item, reason))

        for item in favorites:
            push(item, "最爱")
        for item in additional:
            push(item, "额外可吃")
        for group in groups:
            for item in group_foods.get(group, []):
                push(item, "食谱同类")
        if feeder.get("food"):
            push(feeder["food"], "当前种类")

        # 有货的候选（保持喜好优先级）
        available = []
        for item, reason in candidates:
            sources = stock.get(item) or []
            if sources and sum(c for _, c in sources) > 0:
                available.append({"food": item, "reason": reason, "sources": sources})
        chosen = available[0] if available else None
        plan_rows.append({
            "feeder": feeder,
            "corral": nearest["id"] if nearest else None,
            "species": species,
            "chosen": chosen,
            "candidates": available,
        })
    return {"rows": plan_rows, "stock": stock}


def plan_transfer(source: str, target_index: int, food: str, amount: int) -> int:
    """dry_run 校验目标身份 + 食物种类；返回可搬数量。"""
    try:
        data = rpc("sr2_transfer", {"from": source, "to": str(target_index), "item": food,
                                    "count": str(amount), "dry_run": True})
    except (ActionError, BridgeError):
        return 0
    plan = data.get("plan") or []
    if not plan or any(short(row.get("name")) != food for row in plan):
        return 0
    return sum(int(row.get("move") or 0) for row in plan)


def target_still_valid(feeder: dict) -> bool:
    """执行前确认编号此刻仍指向同一台喂食器（位置一致）。"""
    for row in feeder_rows(snapshot()):
        if row["index"] != feeder["index"]:
            continue
        if not feeder.get("position"):
            return True
        return horizontal(row["position"], feeder["position"]) < 1.0
    return False


def _label(feeder: dict) -> str:
    return f"喂食器#{feeder['index']}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="给围栏喂食器自动补食物（优先史莱姆最爱）")
    parser.add_argument("--apply", metavar="REQUEST_ID")
    parser.add_argument("--reply", action="store_true")
    parser.add_argument("--to", type=int, default=0, help="补到多少份；0=补到槽位上限")
    args = parser.parse_args(argv)

    data = snapshot()
    if not feeder_rows(data):
        print("没有已加载的围栏喂食器（要先回到牧场）。")
        return 0

    menu = build_menu(data)
    tasks = []
    for row in menu["rows"]:
        feeder = row["feeder"]
        want = (args.to or feeder["max"]) - feeder["count"]
        if want <= 0:
            continue
        chosen = row["chosen"]
        if not chosen:
            missing = "/".join(row["species"]) or "未知史莱姆"
            print(f"{_label(feeder)}（{missing}）仓库里没有可喂的食物，跳过。")
            continue
        tasks.append({"row": row, "want": want, "chosen": chosen})

    if not tasks:
        print("喂食器都够吃，暂时不用补。")
        return 0
    if not args.apply:
        print("预演 · 计划补充（按史莱姆喜好排序）：")
        for task in tasks:
            feeder = task["row"]["feeder"]
            chosen = task["chosen"]
            amount = min(task["want"], sum(c for _, c in chosen["sources"]))
            species = "/".join(task["row"]["species"]) or "未知"
            print(f"  {_label(feeder)} [{species}] 现在 {feeder['count']}/{feeder['max']}"
                  f" → 补 {amount} 份 {chosen['food']}（{chosen['reason']}，"
                  f"来源 {chosen['sources'][0][0].split('/')[0]}）")
        return 0

    done, failed = [], []
    for task in tasks:
        feeder = task["row"]["feeder"]
        remaining = task["want"]
        moved_any = 0
        last_reason = None
        # 按喜好顺序尝试：最爱 → 额外可吃 → 食谱同类 → 当前种类；
        # 前一种被游戏拒绝（比如喂食器里还有别的食物）就换下一种。
        for chosen in task["row"]["candidates"]:
            if remaining <= 0 or moved_any:
                break
            for source, stock in chosen["sources"]:
                if remaining <= 0:
                    break
                amount = min(remaining, stock)
                ok = plan_transfer(source, feeder["index"], chosen["food"], amount)
                if not ok:
                    last_reason = "校验不通过（可能是喂食器里还有别的食物）"
                    continue
                amount = min(amount, ok)
                if not target_still_valid(feeder):
                    last_reason = "编号漂移，已跳过"
                    break
                request_id = (f"{args.apply}-feed-{feeder['index']}-"
                              f"{hashlib.sha256((source + chosen['food']).encode()).hexdigest()[:6]}")
                try:
                    result = rpc("sr2_transfer", {"from": source, "to": str(feeder["index"]),
                                                  "item": chosen["food"], "count": str(amount),
                                                  "dry_run": False}, request_id)
                except (ActionError, BridgeError) as exc:
                    last_reason = str(exc)
                    break
                moved = sum(int(row.get("moved") or 0) for row in result.get("moved") or [])
                if moved <= 0:
                    last_reason = "一份也没搬成"
                    break
                done.append(f"{_label(feeder)} {chosen['food']}({chosen['reason']}) +{moved}")
                moved_any += moved
                remaining -= moved
        if moved_any <= 0:
            failed.append(f"{_label(feeder)}（{last_reason or '仓库里没有合适食物'}）")

    after = feeder_rows(snapshot())
    state = "、".join(f"{_label(row)} {row['food'] or '空'}×{row['count']}" for row in after)
    summary = ("已补：" + "、".join(done) if done else "没有补充成功")
    summary += f"。现在：{state}"
    print(summary + (f" 异常：{'；'.join(failed)}" if failed else ""))
    if args.reply:
        try:
            rpc("sr2_chat_send", {"text": summary[:400]})
        except (ActionError, BridgeError):
            pass
    return 0 if done else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ActionError, BridgeError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        sys.exit(1)
