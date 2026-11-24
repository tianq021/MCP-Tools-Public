"""每种结晶在地块筒仓里只留约 10%，其余卖掉（「留一成、其余变现」用）。

用法（项目根目录下运行）：
    python -B tool/keep10_sell.py                  # 预演，只打印计划
    python -B tool/keep10_sell.py --apply <ID>     # 执行；重试沿用同一 ID 和参数

流程：
 1. sr2_storage_list 读所有地块筒仓的槽位，按结晶种类汇总（只算筒仓，场景收集器另算）；
 2. 给每种结晶挑一个「留种仓」（优先已经放着该结晶的筒仓，否则用还有空槽的筒仓），
    把 10% 的保留量转进去（已经够就不转）；
 3. 其余筒仓里的同种结晶按筒仓整批卖出（sr2_sell_plorts + item + storage）；
 4. 回读筒仓与新币，输出每种结晶的保留量/卖出量/到账。

留种仓转不进去时退化为「不动该种结晶存量最多的那个筒仓」，保证不会把某种结晶卖空。
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp"))
from bridge_client import BridgeError, call


class ActionError(Exception):
    pass


def rpc(name, args=None, request_id=None, *, allow_reason=False):
    response = call(name, args or {}, request_id)
    if not response.get("ok"):
        error = response.get("error") or {}
        raise ActionError(f"{error.get('code', 'bridge_error')} {error.get('message', '')}".strip())
    data = response["data"]
    if data.get("available") is False or (data.get("reason") and not allow_reason):
        raise ActionError(str(data.get("reason", "unavailable")))
    return data


def short(name):
    return (name or "").replace("IdentifiableType.", "")


def is_plort(name):
    return short(name).endswith("Plort")


def silos():
    """只取地块筒仓：槽位、空闲槽数、结晶分布。"""
    data = rpc("sr2_storage_list")
    rows = []
    for store in data.get("items") or []:
        if store.get("kind") != "plot_silo" or not store.get("storage_key"):
            continue
        slots = []
        for slot in store.get("slots") or []:
            sid = slot.get("id")
            count = int(slot.get("count") or 0)
            if not sid or not slot.get("unlocked") or count <= 0 or not is_plort(sid):
                continue
            slots.append({"id": short(sid), "count": count})
        free = sum(1 for slot in store.get("slots") or []
                   if slot.get("unlocked") and not slot.get("id"))
        rows.append({"ref": store["storage_key"], "free": free, "plorts": slots})
    return rows


def count_in(store, item):
    return sum(slot["count"] for slot in store["plorts"] if slot["id"] == item)


def totals_of(rows):
    totals = {}
    for store in rows:
        for slot in store["plorts"]:
            totals[slot["id"]] = totals.get(slot["id"], 0) + slot["count"]
    return totals


def plan(rows):
    """给每种结晶分配留种仓。返回 (总量表, {结晶: 留种仓}, {结晶: 保留量})。

    选仓顺序：
      1. 某个筒仓里恰好有 ≥保留量且 ≤2×保留量 的同种结晶 → 就留那儿，不搬；
      2. 某个筒仓已有该结晶但不足保留量 → 只补差额转进去；
      3. 都没有 → 找一个还有空槽的筒仓，把保留量转进去；
      4. 连空槽都没有 → 退化为「不动存量最多的筒仓」。
    """
    totals = totals_of(rows)
    free = {store["ref"]: store["free"] for store in rows}
    where, keeps = {}, {}
    for item in sorted(totals):
        total = totals[item]
        keep = total if total <= 10 else max(1, int(round(total * 0.1)))
        keeps[item] = keep
        holders = [(count_in(s, item), s) for s in rows if count_in(s, item) > 0]
        enough = [(c, s) for c, s in holders if keep <= c <= keep * 2]
        short = [(c, s) for c, s in holders if c < keep]
        if enough:
            where[item] = min(enough, key=lambda pair: pair[0])[1]
        elif short:
            where[item] = max(short, key=lambda pair: pair[0])[1]
        else:
            usable = [s for s in rows if free.get(s["ref"], 0) > 0]
            if usable:
                host = max(usable, key=lambda s: free[s["ref"]])
                free[host["ref"]] -= 1
                where[item] = host
            elif holders:
                where[item] = max(holders, key=lambda pair: pair[0])[1]
            else:
                where[item] = None
    return totals, where, keeps


def keep_in_place(rows, host, item, keep, request_id, apply, entry):
    """把保留量凑进 host；返回实际用于留种的筒仓 ref。"""
    existing = count_in(host, item)
    need = keep - existing
    if need <= 0:
        return host["ref"]
    sources = sorted((s for s in rows if s["ref"] != host["ref"] and count_in(s, item) > 0),
                     key=lambda s: count_in(s, item), reverse=True)
    if not sources:
        return host["ref"]
    src = sources[0]
    if not apply:
        entry["transfer"] = f"{short(item)}×{need} 从 {src['ref'][-12:]} 转入"
        entry["need"] = need
        return host["ref"]
    try:
        result = rpc("sr2_transfer", {
            "from": src["ref"], "to": host["ref"], "item": item,
            "count": str(need), "dry_run": False,
        }, f"{request_id}-keep-{short(item)}")
        moved = sum(int(row.get("moved") or 0) for row in result.get("moved") or [])
        entry["transfer"] = f"{short(item)}×{moved} 转入留种仓"
        if moved >= need:
            return host["ref"]
    except (ActionError, BridgeError) as exc:
        entry["transfer_error"] = str(exc)
    holder = max(rows, key=lambda s: count_in(s, item))
    entry["fallback"] = holder["ref"]
    return holder["ref"]


def sell_from_others(rows, keep_ref, item, request_id, apply, entry):
    sold, income, problems = 0, 0, []
    for pos, store in enumerate(rows):
        if store["ref"] == keep_ref:
            continue
        have = count_in(store, item)
        if have <= 0:
            continue
        if not apply:
            sold += have
            continue
        try:
            result = rpc("sr2_sell_plorts", {
                "item": item, "source": "storage", "storage": store["ref"], "dry_run": False,
            }, f"{request_id}-sell-{short(item)}-{pos}")
            sold += sum(int(row.get("sold") or 0) for row in result.get("sold") or [])
            income += sum(int(row.get("income_estimate") or 0) for row in result.get("sold") or [])
            if result.get("failures"):
                problems.append(f"{store['ref'][-12:]} {len(result['failures'])} 条失败")
        except (ActionError, BridgeError) as exc:
            problems.append(f"{store['ref'][-12:]} 卖出失败：{exc}")
    entry["sold"] = sold
    entry["income"] = income
    if problems:
        entry["problems"] = problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="地块筒仓里每种结晶留约 10%，其余卖掉")
    parser.add_argument("--apply", metavar="REQUEST_ID", help="执行；重试沿用同一个 ID")
    parser.add_argument("--reply", action="store_true", help="把摘要发到游戏聊天框")
    parser.add_argument("--include-rare", action="store_true",
                        help="连迷宫稀有结晶（暗影/棱镜/不稳定/稳定）也一起卖；默认跳过")
    args = parser.parse_args(argv)

    rows = silos()
    if not rows:
        raise ActionError("没有读到地块筒仓。")
    totals, where, keeps = plan(rows)
    coins_before = rpc("sr2_currency").get("amount")

    RARE = {"ShadowPlort", "PrismPlort", "UnstablePlort", "StablePlort"}
    skipped = []
    entries = []
    for item in sorted(totals):
        host = where[item]
        entry = {"item": item, "total": totals[item], "keep": keeps[item]}
        if item in RARE and not args.include_rare:
            entry["sold"] = 0
            entry["skipped"] = True
            skipped.append(short(item))
            entries.append(entry)
            continue
        if host is None:
            entry["sold"] = 0
            entry["problems"] = ["没有空槽可以留种，未处理"]
            entries.append(entry)
            continue
        keep_ref = keep_in_place(rows, host, item, keeps[item], args.apply or "", bool(args.apply), entry)
        sell_from_others(rows, keep_ref, item, args.apply or "", bool(args.apply), entry)
        entries.append(entry)

    if args.apply is None:
        print(f"预演 · 共 {len(entries)} 种结晶，只卖地块筒仓里的，收集器里的等整理时另算")
        total_sell = 0
        for entry in entries:
            total_sell += entry.get("sold", 0) - entry.get("need", 0)
            if entry.get("skipped"):
                print(f"  {short(entry['item'])}：共{entry['total']} 稀有结晶，跳过不卖")
                continue
            note = entry.get("transfer") or ""
            sell_now = max(0, entry.get("sold", 0) - entry.get("need", 0))
            print(f"  {short(entry['item'])}：共{entry['total']} 留{entry['keep']} 卖{sell_now}"
                  + (f"（{note}）" if note else ""))
        print(f"合计计划卖出 {total_sell} 颗" + (f"；稀有跳过：{'、'.join(skipped)}" if skipped else "")
              + "；执行请带 --apply <ID>")
        return 0

    after = silos()
    coins_after = rpc("sr2_currency").get("amount")
    kept = totals_of(after)
    parts, problems, fallbacks = [], [], []
    for entry in entries:
        item = short(entry["item"])
        part = f"{item} 留{kept.get(item, 0)}/{entry['total']}"
        if entry.get("sold"):
            part += f" 卖{entry['sold']}+{entry.get('income', 0)}币"
        parts.append(part)
        if entry.get("problems"):
            problems.append(f"{item}：{'、'.join(entry['problems'])}")
        if entry.get("fallback"):
            fallbacks.append(f"{item} 留在 {entry['fallback'][-12:]}")
    print("；".join(parts) + "。")
    if skipped:
        print("稀有结晶跳过不卖：" + "、".join(skipped))
    if coins_before is not None and coins_after is not None:
        print(f"新币 {coins_before} → {coins_after}（+{coins_after - coins_before}）")
    if problems:
        print("异常：" + "；".join(problems))
    if fallbacks:
        print("退化保留（没转进留种仓）：" + "；".join(fallbacks))
    if args.reply:
        sold = sum(entry.get("sold", 0) for entry in entries)
        summary = f"已卖掉 {sold} 颗结晶，新币 {coins_before} → {coins_after}"
        if problems:
            summary += "；有部分失败：" + "；".join(problems)[:120]
        try:
            rpc("sr2_chat_send", {"text": summary[:400]})
        except (ActionError, BridgeError):
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ActionError, BridgeError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        sys.exit(1)
