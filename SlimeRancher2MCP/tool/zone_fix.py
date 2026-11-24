"""把地块筒仓里的结晶搬到「产区筒仓」：每种结晶按产出它的围栏/设备就近归位。

用法（项目根目录下运行）：
    python -B tool/zone_fix.py                  # 预演
    python -B tool/zone_fix.py --apply <ID>     # 执行；重试沿用同一 ID
    python -B tool/zone_fix.py --apply <ID> --reply   # 顺便把摘要发到游戏聊天框

产区口径（2026-11-24 按地块坐标 + 围栏史莱姆种类核定）：
  主仓 plot0031824558：蜂蜜、水晶、猎人、爆破（西侧围栏 HoneyCrystal / BoomHunter）
  2 号 plot0199756409：蝙蝠、磷光、缠结（北侧围栏 BattyPhosphor / PhosphorTangle）
  3 号 plot0525538627：水滴、火焰（池塘 + 焚化炉）
  4 号 plot1790050753：噗啦蝶、虎斑（泳池区围栏 FlutterTabby）
  5 号 plot1825556077：钓鱼、岩石、环尾（东侧围栏 AnglerRock / RingtailRock）

除下面的兜底表外，也可以用 zone_router 自动识别（读围栏史莱姆产物 + 地块坐标，
按「离产区最近的筒仓」决定归属）；识别成功时优先用自动结果，避免地块 ID 变了就失效。

没登记的结晶不动，避免瞎搬。
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp"))
from bridge_client import BridgeError, call

sys.path.insert(0, str(Path(__file__).resolve().parent))

SUFFIX = "/58d5bd4fc903e1c49aba61495aa74014"
MAIN = "plot:plot0031824558" + SUFFIX
NORTH = "plot:plot0199756409" + SUFFIX
GULLY = "plot:plot0525538627" + SUFFIX
POOLS = "plot:plot1790050753" + SUFFIX
EAST = "plot:plot1825556077" + SUFFIX

HOME = {
    "HoneyPlort": MAIN, "CrystalPlort": MAIN, "HunterPlort": MAIN, "BoomPlort": MAIN,
    "BattyPlort": NORTH, "PhosphorPlort": NORTH, "TanglePlort": NORTH,
    "PuddlePlort": GULLY, "FirePlort": GULLY,
    "FlutterPlort": POOLS, "TabbyPlort": POOLS,
    "AnglerPlort": EAST, "RockPlort": EAST, "RingtailPlort": EAST,
}
LABEL = {MAIN: "主仓", NORTH: "2号", GULLY: "3号岩沟", POOLS: "4号泳池", EAST: "5号东侧"}


def home_map():
    """优先用 zone_router 自动识别产区，失败再退回上面的兜底表。"""
    try:
        import zone_router

        detected = zone_router.home_map()
        if detected:
            return detected, "自动识别"
    except Exception:  # noqa: BLE001 - 自动识别失败不影响兜底表
        pass
    return dict(HOME), "兜底表"


class ActionError(Exception):
    pass


def rpc(name, args=None, request_id=None):
    response = call(name, args or {}, request_id)
    if not response.get("ok"):
        error = response.get("error") or {}
        raise ActionError(f"{error.get('code', 'bridge_error')} {error.get('message', '')}".strip())
    data = response["data"]
    if data.get("available") is False:
        raise ActionError(str(data.get("reason", "unavailable")))
    return data


def short(name):
    return (name or "").replace("IdentifiableType.", "")


def silos():
    data = rpc("sr2_storage_list")
    rows = []
    for store in data.get("items") or []:
        key = store.get("storage_key")
        if store.get("kind") != "plot_silo" or not key:
            continue
        rows.append({
            "ref": key,
            "slots": [{"index": s["index"], "id": short(s.get("id")), "count": int(s.get("count") or 0)}
                      for s in store.get("slots") or []
                      if s.get("id") and s.get("unlocked") and int(s.get("count") or 0) > 0],
        })
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="按产区把筒仓里的结晶归位")
    parser.add_argument("--apply", metavar="REQUEST_ID")
    parser.add_argument("--reply", action="store_true")
    args = parser.parse_args(argv)

    rows = silos()
    mapping, source = home_map()
    moves = []
    for store in rows:
        for slot in store["slots"]:
            home = mapping.get(slot["id"])
            if home and home != store["ref"]:
                moves.append((slot["id"], slot["count"], store["ref"], home))

    if not moves:
        print(f"结晶都已经在自己的产区筒仓里（归属来源：{source}）。")
        return 0

    if args.apply is None:
        print(f"预演 · 需要归位（归属来源：{source}）：")
        for item, count, src, dst in moves:
            print(f"  {item}×{count}  {LABEL.get(src, src[-12:])} → {LABEL.get(dst, dst[-12:])}")
        return 0

    done, failed = 0, []
    for idx, (item, count, src, dst) in enumerate(moves):
        try:
            result = rpc("sr2_transfer", {"from": src, "to": dst, "item": item,
                                          "count": str(count), "dry_run": False},
                         f"{args.apply}-zf-{idx}")
            moved = sum(int(row.get("moved") or 0) for row in result.get("moved") or [])
            done += moved
            if moved < count:
                failed.append(f"{item} 只搬了 {moved}/{count}")
        except (ActionError, BridgeError) as exc:
            failed.append(f"{item}：{exc}")

    after = silos()
    left = [(s["id"], s["count"], store["ref"]) for store in after for s in store["slots"]
            if mapping.get(s["id"]) and mapping[s["id"]] != store["ref"]]
    print(f"已归位 {done} 件。" + (f" 仍在错位：{'、'.join(f'{i}×{c}@{LABEL.get(r, r[-8:])}' for i, c, r in left)}" if left else " 全部到位。"))
    if failed:
        print("异常：" + "；".join(failed))
    if args.reply:
        text = f"结晶按产区归位：搬了 {done} 件" + (f"，还有 {len(left)} 处没搬动" if left else "，全部到位")
        try:
            rpc("sr2_chat_send", {"text": text[:400]})
        except (ActionError, BridgeError):
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ActionError, BridgeError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        sys.exit(1)
