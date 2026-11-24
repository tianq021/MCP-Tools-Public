"""玩家说自然语言，模型按意图选择子命令。输出可直接显示在游戏聊天框。"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
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


def game_time():
    now = rpc("sr2_time")
    minutes = int(now["hour"] * 60) % 1440
    return f"第{now['day']}天 {minutes // 60:02d}:{minutes % 60:02d}" + ("（暂停）" if now["paused"] else "")


def bag():
    pack = rpc("sr2_vacpack")
    slots = [s for s in pack["slots"] if s["unlocked"]]
    items = [f"{s['index']}号 {s['name'] or s['id']}×{s['count']}" for s in slots if s["count"] > 0]
    return f"背包 {len(items)}/{len(slots)} 格" + ("，" + "、".join(items) if items else "")


def status():
    session = rpc("sr2_state")
    phase = session["phase"]
    if phase != "gameplay":
        return {"main_menu": "目前在主菜单。", "loading": "正在加载存档。"}.get(phase, f"当前阶段 {phase}。")
    player = rpc("sr2_player")
    p = player["position"]
    area = player.get("zone") or session.get("scene_group") or "区域未知"
    return (f"{game_time()}，{area}（{p['x']:.1f}, {p['y']:.1f}, {p['z']:.1f}）。"
            f"生命 {player['health']}/{player['max_health']}，能量 {player['energy']}/{player['max_energy']}，"
            f"新币 {player['coins'] if player['coins'] is not None else '未知'}。{bag()}。")


def act(name, params, request_id):
    return rpc(name, {**params, "dry_run": request_id is None}, request_id)


ITEM_ALIASES = {
    "盐水": "DeepBrineCraft", "深卤水": "DeepBrineCraft", "深层盐水": "DeepBrineCraft",
    "粉色结晶": "PinkPlort", "粉红结晶": "PinkPlort", "水滴结晶": "PuddlePlort",
    "蜂蜡": "BuzzWaxCraft", "野生蜂蜜": "WildHoneyCraft", "胡萝卜": "Carrot",
}
PLORT_NAMES = {
    "粉红": "Pink", "粉色": "Pink", "棉花": "Cotton", "荧光": "Phosphor", "磷光": "Phosphor",
    "虎斑": "Tabby", "岩石": "Rock", "钓鱼": "Angler", "安康": "Angler", "天使鱼": "Angler", "蜂蜜": "Honey",
    "蝙蝠": "Batty", "猎手": "Hunter", "猎人": "Hunter", "蝴蝶": "Flutter", "噗啦蝶": "Flutter", "炸弹": "Boom", "爆炸": "Boom", "爆破": "Boom", "水晶": "Crystal",
    "火焰": "Fire", "水滴": "Puddle", "环尾": "Ringtail", "剑齿": "Saber", "旋风": "Dervish",
    "藤蔓": "Tangle", "亢奋": "Hyper", "蛋黄": "Yolky", "双生": "Twin", "昏昏": "Sloomber",
    "猎猫": "Hunter", "爆炸": "Boom",
    "暗影": "Shadow", "棱镜": "Prisma", "黄金": "Gold",
}
ITEM_ALIASES.update({name + "结晶": stem + "Plort" for name, stem in PLORT_NAMES.items()})
MAIN_STORAGE = "plot:plot0031824558/58d5bd4fc903e1c49aba61495aa74014"


def region_home_map():
    """自动识别「某种结晶 → 它的产区筒仓」（tool/zone_router.py，实时读围栏与地块）。

    识别不到时返回空 dict，调用方继续走原来的「本区域筒仓 + 主仓兜底」逻辑，
    所以桥接旧版本 / 场景没加载都不会把主流程弄坏。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import zone_router

        return zone_router.home_map()
    except Exception:  # noqa: BLE001 - 路由只是增强，不允许影响正常整理
        return {}
RANCH_CONTROL_ACTIONS = {
    "feed": ("feeder", "sr2_dispense_feeder_food", "喂食器"),
    "start-plort-collector": ("plort_collector", "sr2_activate_plort_collector", "结晶收集器"),
    "start-resource-collector": ("resource_collector", "sr2_activate_resource_collector", "资源收集器"),
    "silo-next": ("silo_port", "sr2_cycle_silo_port", "筒仓面板"),
}
FEED_SPEED_LABELS = {"slow": "慢", "normal": "普通", "fast": "快"}
FEED_SPEED_ALIASES = {label: speed for speed, label in FEED_SPEED_LABELS.items()}


def display_id(item_id):
    return item_id.rsplit(".", 1)[-1]


def inventory_locations(include_refinery):
    locations, unavailable = [], []
    try:
        for slot in rpc("sr2_vacpack")["slots"]:
            if slot.get("id") and slot["count"] > 0:
                locations.append((f"背包{slot['index']}格", slot))
    except ActionError as error:
        unavailable.append(f"背包（{error}）")
    try:
        for store in rpc("sr2_storage_list")["items"]:
            kind = "筒仓" if store["kind"] == "plot_silo" else "场景容器"
            for slot in store["slots"]:
                if slot.get("id") and slot["count"] > 0:
                    lock = "（锁定）" if slot.get("unlocked") is False else ""
                    locations.append((f"{kind}{store['index']}的{slot['index']}格{lock}", slot))
    except ActionError as error:
        unavailable.append(f"仓库（{error}）")
    if include_refinery:
        try:
            rows = rpc("sr2_refinery")["items"]
            if not rows:
                unavailable.append("精炼炉物品目录")
            # The bridge may list the same refinery type in multiple groups.
            unique = {r["id"]: r for r in rows if r.get("id")}
            locations.extend(("精炼炉", row) for row in unique.values())
        except ActionError as error:
            unavailable.append(f"精炼炉（{error}）")
    return locations, unavailable


def name_matches(rows, query):
    """Prefer a full ID, then an exact title/short name, then a unique substring."""
    folded = query.casefold()
    ids, exact, partial = [], [], []
    for row in rows:
        if query == row["id"]:
            return [row]
        if folded == row["id"].casefold():
            ids.append(row)
        names = [value.casefold() for value in (row["id"], display_id(row["id"]), row.get("name"), row.get("title")) if value]
        if folded in names:
            exact.append(row)
        elif any(folded in name for name in names):
            partial.append(row)
    return ids or exact or partial


def unique_match(rows, query, kind):
    matches = name_matches(rows, query)
    if not matches:
        raise ActionError(f"未找到匹配“{query}”的{kind}。")
    if len(matches) > 1:
        choices = "、".join(f"{r.get('title') or r.get('name') or display_id(r['id'])}（{r['id']}）" for r in matches[:4])
        raise ActionError(f"匹配到 {len(matches)} 种{kind}，请指定完整 ID：{choices}")
    return matches[0]


def quote(args):
    market = rpc("sr2_market")
    search = ITEM_ALIASES.get(args.query, args.query)
    if args.query in PLORT_NAMES:
        search = PLORT_NAMES[args.query] + "Plort"
    row = unique_match(market["prices"], search, "结晶")
    label = row.get("name") or display_id(row["id"])
    if args.query in PLORT_NAMES:
        label = args.query + "结晶"
    elif args.query in ITEM_ALIASES:
        label = args.query
    state = "当前休市，" if market["market_closed"] else ""
    if row["price"] is None:
        return f"{state}{label} 暂无报价。"
    price, change = row["price"], row["change"]
    trend = "涨跌未知" if change is None else ("持平" if change == 0 else f"较上次{change:+d}")
    return f"{state}{label} 每颗 {price} 新币（{trend}），{args.count} 颗按此价估算 {price * args.count} 新币。"


def storage_selector(value):
    value = search_text(value)
    if value in ("main", "主仓", "主仓库"):
        return MAIN_STORAGE
    if value.startswith(("plot:", "scene:")):
        return value
    raise argparse.ArgumentTypeError("请填 main 或 stores 列出的容器标识")


def stores(selector):
    items = rpc("sr2_storage_list")["items"]
    summaries = []
    for store in items:
        key = store.get("selector") or store.get("storage_key")
        if selector and key != selector:
            continue
        label = ("主仓 main" if key == MAIN_STORAGE else
                 f"{'筒仓' if store['kind'] == 'plot_silo' else '场景容器'} {key or '#' + str(store['index'])}")
        empty = sum(s.get("unlocked") is True and s["count"] == 0 for s in store["slots"])
        space = f"空 {empty} 格" if store["writable"] else "槽位容量未知"
        summaries.append(f"{label}（{store['total_items']} 件，{space}）")
        if selector:
            contents = [f"{s['index']}格 {s.get('name') or display_id(s['id'])}×{s['count']}"
                        for s in store["slots"] if s.get("id") and s["count"] > 0]
            return summaries[0] + ("，" + "、".join(contents) if contents else "") + "。"
    if selector:
        raise ActionError("没有找到这个容器，请用 stores 查看当前列表。")
    return "，".join(summaries) + "。" if summaries else "当前没有查到容器。"


def resolve_item_arg(query):
    """把玩家写的物品名解析成桥接认识的名字：先查别名表，再按结晶名补 Plort。"""
    text = (query or "").strip()
    if text in ITEM_ALIASES:
        return ITEM_ALIASES[text]
    stem = PLORT_NAMES.get(text) or PLORT_NAMES.get(text[:-2] if text.endswith("结晶") else text)
    if stem:
        return stem + "Plort"
    return text


def move_amount(args):
    item = resolve_item_arg(args.query)
    if item.isdecimal():
        raise ActionError("请指定物品名称或 ID。")
    direction = args.command.removesuffix("-all")
    target = storage_label(args.storage)
    params = {"direction": direction, "item": item, "quantity": args.count, "storage": args.storage}
    if direction == "transfer":
        if args.source == args.storage:
            raise ActionError("来源和目标是同一个仓库。")
        params["from"] = args.source
        label = f"从{storage_label(args.source)}搬到{target}的{args.query}"
    else:
        label = (f"向{target}存入" if direction == "store" else f"从{target}取回") + args.query
    try:
        result = act("sr2_move_amount", params, args.apply)
    except ActionError as error:
        reason = str(error)
        if reason in ("storage_not_found", "source_storage_not_found"):
            which = "来源" if reason == "source_storage_not_found" else "指定"
            raise ActionError(f"当前没找到{which}仓库，请用 stores 查看容器。") from error
        if direction == "transfer" or args.count == "all":
            if reason in ("bad_item_or_direction", "bad_quantity") or reason.startswith("UNKNOWN_TOOL "):
                raise ActionError("整批存取和仓库间定量搬运需要 0.5.27 或更新的桥接。") from error
        elif reason.startswith("UNKNOWN_TOOL "):
            raise ActionError("定量存取需要 0.5.22 或更新的桥接。") from error
        raise
    planned, moved = result["planned"], result["moved"]
    requested = result["requested"] if args.count == "all" else args.count
    if requested == 0:
        return f"来源中没有可搬的{args.query}。"
    if planned != requested:
        raise ActionError(f"{label}需要 {requested} 件，按现有库存和空间可搬 {planned} 件，本次未搬动。")
    if args.apply is None:
        return f"预演 · 可{label} {planned} 件。"
    if moved != requested or result.get("failure"):
        raise ActionError(f"{label}，已核实搬动 {moved}/{requested} 件（{result.get('failure') or '数量未对上'}）。")
    if direction == "transfer":
        return f"已把{args.query} {moved} 件从{storage_label(args.source)}搬到{target}。"
    return f"已{label} {moved} 件。"


def _origin_distance(origin, position):
    """两个 {x,y,z} 坐标点的距离；缺数据时返回 None。"""
    if not origin or not position:
        return None
    try:
        return ((origin["x"] - position["x"]) ** 2 + (origin["y"] - position["y"]) ** 2
                + (origin["z"] - position["z"]) ** 2) ** 0.5
    except (KeyError, TypeError):
        return None


def consolidate_storage(args):
    params = {"storage": args.storage or ""}
    try:
        data = act("sr2_consolidate_storage", params, args.apply)
    except ActionError as error:
        if str(error).startswith("UNKNOWN_TOOL "):
            raise ActionError("仓库归堆需要 0.5.28 或更新的桥接。") from error
        if str(error) == "plot_storage_not_found":
            raise ActionError("未找到指定的地块筒仓。") from error
        raise
    checked = data["checked_silos"]
    skipped = len(data["skipped_silos"])
    scope = f"检查 {checked} 座筒仓" + (f"，{skipped} 座槽位规则不可用" if skipped else "")
    planned = data["planned_items"]
    if not planned:
        return f"{scope}。可整理的筒仓中没有可合并的同类物品。"
    if args.apply is None:
        return f"预演 · {scope}，可归堆 {planned} 件，腾出 {data['planned_freed_slots']} 格。"
    moved = data["moved_items"]
    freed = data["freed_slots"]
    if data.get("failure") or moved != planned:
        reason = (data.get("failure") or {}).get("reason") or "数量未对上"
        raise ActionError(f"{scope}，已归堆 {moved}/{planned} 件，腾出 {freed} 格（{reason}）。")
    return f"{scope}，已归堆 {moved} 件，腾出 {freed} 格。"


def player_zone():
    """玩家当前所在区域（游戏 zone 的 ReferenceId）；读不到返回 None。"""
    try:
        return rpc("sr2_player").get("zone")
    except (ActionError, BridgeError):
        return None


def silo_order(data, origin=None, zone=None):
    """按 RULES.md「各区域的结晶放进该区域自己的筒仓」给地块筒仓排序。

    桥接 0.5.25+ 给每个已加载容器带 zone，可以按区域精确匹配；旧桥接没有 zone 时
    退回「离来源最近的已加载筒仓」近似（区域内的筒仓一定已加载）。
    顺序：本区域已加载筒仓（由近到远）→ 主仓兜底 → 其它已加载筒仓 → 未加载筒仓
    （未加载筒仓走存档模型，仍可远程写入，放在最后当备用）。
    返回 (storage_key 列表, 是否命中本区域)。"""
    ranked = []
    for store in data.get("items") or []:
        if store.get("kind") != "plot_silo" or not store.get("storage_key"):
            continue
        loaded = bool(store.get("loaded"))
        distance = _origin_distance(origin, store.get("position"))
        if distance is None:
            distance = store.get("distance")
        if distance is None:
            # 未加载筒仓没有坐标：排到已加载筒仓之后，但仍保留为可远程写入的备用。
            distance = 1e12 if not loaded else 1e9
        ranked.append((store["storage_key"], float(distance), store.get("zone"), loaded))
    ranked.sort(key=lambda row: row[1])
    same = [key for key, _, silo_zone, loaded in ranked if loaded and zone and silo_zone == zone]
    others = [key for key, _, silo_zone, loaded in ranked if not (loaded and zone and silo_zone == zone)]
    seen, ordered = set(), []
    for key in same + [MAIN_STORAGE] + others:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered, bool(same)


def region_target(data, source, fixed=None):
    """某个场景容器（收集器）里结晶的目标仓库：优先本区域筒仓，主仓兜底。"""
    if fixed:
        return fixed
    origin = source.get("position") or data.get("player_position")
    order, _ = silo_order(data, origin, source.get("zone"))
    return order[0] if order else MAIN_STORAGE


def scene_plorts(data):
    """当前快照里场景容器（收集器等）中的结晶：[(容器编号, 结晶短名, 数量, 容器行)]。"""
    found = []
    for store in data.get("items") or []:
        if store.get("kind") != "scene_storage":
            continue
        for slot in store.get("slots") or []:
            item_id = slot.get("id") or ""
            if slot.get("count", 0) > 0 and item_id.endswith("Plort"):
                found.append((store["index"], display_id(item_id), slot["count"], store))
    return found


def plan_transfer(index, item, target):
    """用 dry_run 校验某一笔搬运，返回 (可搬数量, 错误类别)。

    错误类别：bad_from=编号指向的容器不对；wrong_container=计划里的不是这个物品；
    no_space=容器身份正确但目标仓库没有兼容空间；empty=没有可搬内容。
    收集器没有稳定 storage_key，sr2_transfer 只能按「到玩家距离」排序的编号定位，
    玩家走动时编号会翻转，所以执行前必须逐笔预演核对。"""
    try:
        data = rpc("sr2_transfer", {"from": str(index), "to": target, "item": item, "dry_run": True},
                   allow_reason=True)
    except ActionError:
        return 0, "bad_from"
    if not data.get("available"):
        return 0, "bad_from"
    plan = data.get("plan") or []
    if not plan:
        return 0, "no_space" if data.get("unplaced") else "empty"
    if any(row.get("name") != item for row in plan):
        return 0, "wrong_container"
    return sum(row.get("move", 0) for row in plan), None


def storage_label(target):
    """把 storage_key 转成玩家能读的名字。"""
    if target == MAIN_STORAGE:
        return "主仓"
    head = target.split("/")[0].replace("plot:", "").replace("scene:", "场景容器 ")
    return f"筒仓 {head}" if target.startswith("plot:") else head


def collect_plorts(args):
    """把收集器里的结晶直接搬进**所在区域**的地块仓库（不经过背包）。

    0.5.25+ 桥接给已加载容器带 zone，按 zone 精确选本区域筒仓；旧桥接退回
    「离收集器最近的已加载筒仓」。可用 --target 强制指定单个目标。"""
    data = rpc("sr2_storage_list")
    fixed = args.target
    jobs = scene_plorts(data)
    if not jobs:
        return "收集器里没有待搬的结晶。"

    # 自动识别每种结晶的产区筒仓（zone_router）：优先放回它自己的区域仓库，
    # 放不下时再按「本区域筒仓 → 主仓 → 其它」兜底。
    homes = region_home_map()

    def target_candidates(job, snapshot):
        if fixed:
            return [fixed]
        origin = job[3].get("position") or snapshot.get("player_position")
        order, _ = silo_order(snapshot, origin, job[3].get("zone"))
        order = order or [MAIN_STORAGE]
        home = homes.get(job[1])
        if home:
            order = [home] + [key for key in order if key != home]
        return order

    if args.apply is None:
        detail = "、".join(f"{item}×{count}→{storage_label(target_candidates(job, data)[0])}"
                          for job in jobs for item, count in [(job[1], job[2])])
        return f"预演 · 可把 {detail}，共 {sum(job[2] for job in jobs)} 件。"

    moved, failed = [], []
    for job in jobs:
        index, item, count, store = job
        chosen, last_reason = None, None
        for _ in range(3):
            for target in target_candidates((index, item, count, store), data):
                planned, reason = plan_transfer(index, item, target)
                if planned:
                    chosen = target
                    break
                last_reason = reason
            if chosen:
                break
            if last_reason == "no_space":
                break                      # 所有筒仓都没空间，重读也没用
            data = rpc("sr2_storage_list")
            again = [row for row in scene_plorts(data) if row[1] == item]
            if not again:
                break
            index, count, store = again[0][0], again[0][2], again[0][3]
        if not chosen:
            failed.append(f"{item}（{'所有筒仓都没空间' if last_reason == 'no_space' else '编号漂移'}，"
                          f"放不下 {count} 颗）")
            continue
        target = chosen
        try:
            # 每笔搬运必须用独立的 request_id：桥接的幂等缓存以 request_id 为键，
            # 同一个 id 配不同参数会返回 REQUEST_ID_CONFLICT。
            result = act("sr2_transfer",
                         {"from": str(index), "to": target, "item": item},
                         f"{args.apply}-{item}-{index}-{hashlib.sha256(target.encode()).hexdigest()[:6]}")
        except ActionError as error:
            failed.append(f"{item}（{error}）")
            continue
        if not result.get("moved_count"):
            reason = result.get("reason") or "数量未对上"
            failed.append(f"{item}（{reason}）")
            continue
        moved.extend(f"{row.get('name')}×{row.get('moved')}→{storage_label(target)}"
                     for row in result["moved"])

    if not moved:
        raise ActionError("一笔也没搬成：" + "、".join(failed))
    summary = f"已把 {'、'.join(moved)}。"
    return summary + (f"未搬成：{'、'.join(failed)}。" if failed else "")


# 2026-11-21 玩家更新：水滴结晶 PuddlePlort 改为和其它结晶一样进仓库，只有粉色结晶固定进精炼炉。
REFINERY_ONLY_PLORTS = ("PinkPlort",)


def tidy_plan():
    """按 RULES.md 给背包分类：结晶（含水滴）进仓、粉红结晶与 *Craft 材料进精炼炉、其它留下。"""
    bag = rpc("sr2_vacpack")
    to_silo, to_refinery, kept = [], [], []
    for slot in bag["slots"]:
        if not slot.get("id") or slot["count"] <= 0:
            continue
        short = display_id(slot["id"])
        row = (short, slot["count"])
        if short in REFINERY_ONLY_PLORTS or short.endswith("Craft"):
            to_refinery.append(row)
        elif short.endswith("Plort"):
            to_silo.append(row)
        else:
            kept.append(row)
    return to_silo, to_refinery, kept


def silo_targets(zone=None):
    """按 RULES.md 返回可写筒仓键（本区域优先），以及是否命中本区域。

    桥接 0.5.25+ 按 zone 精确匹配；旧桥接没有 zone 时按「离玩家最近的已加载筒仓」
    近似。读不到仓库时退回主仓。"""
    try:
        data = rpc("sr2_storage_list")
    except (ActionError, BridgeError):
        return [MAIN_STORAGE], False
    order, has_region = silo_order(data, data.get("player_position"), zone)
    return (order or [MAIN_STORAGE]), has_region


def moved_quantity(result, list_key, field):
    """moved_count / deposited_count 是"条目数"不是"件数"，必须把每条的件数加起来。"""
    return sum(int(row.get(field) or 0) for row in (result.get(list_key) or []))


def tidy_bag(args):
    """整理背包：结晶（含水滴）→本区域筒仓，粉色结晶与 *Craft 材料→精炼炉，水/活体/食物留下。"""
    to_silo, to_refinery, kept = tidy_plan()
    if not (to_silo or to_refinery):
        return "背包不用整理：" + ("、".join(f"{n}×{c}" for n, c in kept) if kept else "空的")
    if args.apply is None:
        parts = []
        if to_silo:
            parts.append("存本区域筒仓（主仓兜底） " + "、".join(f"{n}×{c}" for n, c in to_silo))
        if to_refinery:
            parts.append("进精炼炉 " + "、".join(f"{n}×{c}" for n, c in to_refinery))
        if kept:
            parts.append("留下 " + "、".join(f"{n}×{c}" for n, c in kept))
        return "预演 · " + "；".join(parts) + "。"

    done, failed = [], []
    zone = player_zone()
    targets, has_region = silo_targets(zone)
    homes = region_home_map()
    for item, count in to_silo:
        ordered = targets
        home = homes.get(item)
        if home and home != MAIN_STORAGE:
            # 自动识别的产区筒仓优先（每个区域的结晶回自己仓库）
            ordered = [home] + [key for key in targets if key != home]
        moved, where, last_reason = 0, None, None
        for target in ordered:
            try:
                result = act("sr2_store", {"item": item, "storage": target},
                             f"{args.apply}-silo-{item}-{hashlib.sha256(target.encode()).hexdigest()[:8]}")
            except ActionError as error:
                last_reason = str(error)
                continue
            got = moved_quantity(result, "moved", "moved")
            if got > 0:
                moved, where = got, target
                break
            last_reason = "已满" if result.get("unplaced") else "数量未对上"
        if moved <= 0:
            failed.append(f"{item}×{count}（{last_reason or '没有可写筒仓'}，未搬）")
            continue
        done.append(f"{item}×{moved}→{storage_label(where)}"
                    + (f"（余 {count - moved} 放不下）" if moved < count else ""))
    for item, count in to_refinery:
        try:
            result = act("sr2_refine", {"item": item}, f"{args.apply}-refine-{item}")
        except ActionError as error:
            failed.append(f"{item}（{error}）")
            continue
        deposited = moved_quantity(result, "deposited", "deposited")
        if deposited <= 0:
            failed.append(f"{item}×{count}（精炼炉未接受，未搬）")
            continue
        done.append(f"{item}×{deposited}→精炼炉")
    if not done:
        raise ActionError("一件也没整理成：" + "、".join(failed))
    summary = "；".join(done) + "。"
    if zone and not has_region:
        routed = [item for item, _ in to_silo
                  if homes.get(item) and homes.get(item) != MAIN_STORAGE]
        summary += ("（按产区筒仓归位）" if routed
                    else "（本区域没有已加载筒仓，退回主仓兜底）")
    if kept:
        summary += "留下 " + "、".join(f"{n}×{c}" for n, c in kept) + "。"
    return summary + (f"未成功：{'、'.join(failed)}。" if failed else "")


def forecast(args):
    """按 docs/结晶/市场预测.md 的供需模型估算换价方向。

    公式：A = S + Q；M = 2 - min(A/T, 1)；中性价 C = B*M；
          明日初积压 S_next = 0.75*A；报价区间 = C*[0.42, 1.82]（全市场×品种波动因子）。
    积压 S 无法从单个报价唯一反推，这里按「波动因子=1」的中性假设反推，
    因此结果是区间而不是单点。"""
    data = rpc("sr2_market")
    if data.get("market_closed"):
        return "市场现在休市，等开门再算。"
    query = (args.query or "").strip()
    qty = int(args.count or 0)
    needle = ""
    if query:
        key = query[:-2] if query.endswith("结晶") else query
        stem = PLORT_NAMES.get(key) or PLORT_NAMES.get(query)
        if stem:
            needle = stem
        elif key in ITEM_ALIASES:
            full = ITEM_ALIASES[key]
            needle = full[:-5] if full.endswith("Plort") else full
        else:
            needle = key
    rows = []
    for row in data["prices"]:
        name = row.get("name") or ""
        if needle and needle.lower() not in name.lower():
            continue
        rows.append(row)
    if not rows:
        return f"没找到匹配「{query}」的结晶。"
    if len(rows) > 6:
        rows = rows[:6]

    lines = []
    for row in rows:
        price = row.get("price")
        base = row.get("base_price")
        limit = row.get("full_saturation")
        if not price or not base or not limit:
            lines.append(f"{row.get('name')}：缺少报价或基础价，跳过。")
            continue
        ratio = price / base
        # 中性假设（波动因子=1）反推当前积压；ratio>2 说明波动因子偏正，积压按 0 处理
        backlog = limit * max(0.0, 2.0 - ratio)
        after = backlog + qty
        multiplier = 2.0 - min(after / limit, 1.0)
        neutral = base * multiplier
        low, high = neutral * 0.42, neutral * 1.82
        if ratio > 1.6:
            trend = "偏高（明日回落概率大）"
        elif ratio > 1.15:
            trend = "中高位"
        elif ratio >= 0.95:
            trend = "接近基础价"
        else:
            trend = "偏低（停卖会回升）"
        head = f"{row.get('name')} {price} 新币/颗（{base} 的基础价 {ratio:.2f} 倍）· {trend}"
        if qty:
            head += f"；再卖 {qty} 颗后，明日中性价约 {neutral:.1f}（区间 {low:.1f}~{high:.1f}）"
            if after > limit:
                days = 0
                pending = after
                while pending >= limit and days < 60:
                    pending *= 0.75
                    days += 1
                head += f"，约 {days} 个游戏日后才退出完全饱和"
        else:
            head += f"；当前反推积压约 {backlog:.1f} 颗（阈值 {limit}）"
        lines.append(head)
    tail = "；模型参数见 docs/结晶/市场预测.md，波动因子会让实际报价落在区间内。"
    return "；".join(lines) + tail


def _distance_text(distance):
    return f"（最近 {distance:.1f} 米）" if distance is not None else ""


def ground_materials():
    """本区域地表可直接用真空枪吸的设备材料。

    2 代的设备材料不全是史莱姆/收集器产出：Jellystone、DeepBrine 这类「料点」长在地表，
    靠近后直接吸进背包，不进精炼炉也能看到。核算缺口时把它们单列出来，避免把
    「地上就有」误报成「还缺」。返回 {材料短名: {nearby/ready 数量与最近距离}}。"""
    found = {}

    def bucket(short):
        return found.setdefault(short, {"nearby": 0, "near": None, "ready": 0, "ready_near": None})

    def note_distance(row_bucket, key, distance):
        if distance is None:
            return
        current = row_bucket[key]
        if current is None or distance < current:
            row_bucket[key] = float(distance)

    try:
        groups = rpc("sr2_nearby", {"category": "craft", "radius": 80, "limit": 100}).get("groups") or []
        for row in groups:
            short = display_id(row.get("id") or row.get("name") or "")
            if short:
                info = bucket(short)
                info["nearby"] += int(row.get("count") or 0)
                note_distance(info, "near", row.get("nearest"))
    except Exception:
        # 地面信息是附加提示，桥接不支持或读数异常都不影响 find/recipe 主流程。
        pass
    try:
        player = rpc("sr2_player").get("position")
    except Exception:
        player = None
    try:
        offset = 0
        while True:
            page = rpc("sr2_resource_nodes", {"loaded_only": True, "limit": 100, "offset": offset})
            for row in page.get("items") or []:
                if row.get("state") != "READY":
                    continue
                distance = _origin_distance(player, row.get("position"))
                for ident in row.get("resources_to_spawn") or []:
                    short = display_id(ident)
                    if short:
                        info = bucket(short)
                        info["ready"] += 1
                        note_distance(info, "ready_near", distance)
            following = page.get("next_offset")
            if following is None or following <= offset:
                break
            offset = following
    except Exception:
        pass
    return found


def ground_hint(materials):
    """把地面可吸材料拼成一句短提示；没有可吸材料时返回空串。"""
    surface = ground_materials()
    hints = []
    for material in materials:
        short = display_id(material)
        info = surface.get(short)
        if not info:
            continue
        bits = []
        if info["nearby"]:
            bits.append(f"附近 {info['nearby']}")
        if info["ready"]:
            bits.append(f"本区域料点 {info['ready']}")
        if bits:
            hints.append(f"{short}（{'，'.join(bits)}）")
    return "地表可直接吸：" + "、".join(hints) if hints else ""


def nearby_materials(args):
    """列出附近 / 本区域可直接吸的装备材料，并给出最近距离。"""
    query = (args.query or "").strip()
    want = ITEM_ALIASES.get(query, query)
    surface = ground_materials()
    if not surface:
        return "附近和本区域都没有可直接吸的特殊材料。"

    def matches(short):
        return not want or want.casefold() in short.casefold()

    nearby = [f"{short}×{info['nearby']}{_distance_text(info['near'])}"
              for short, info in sorted(surface.items()) if info["nearby"] and matches(short)]
    ready = [f"{short}×{info['ready']}{_distance_text(info['ready_near'])}"
             for short, info in sorted(surface.items()) if info["ready"] and matches(short)]
    parts = []
    if nearby:
        parts.append(f"{args.radius} 米内可吸：" + "、".join(nearby))
    if ready:
        parts.append("本区域 READY 料点：" + "、".join(ready))
    if not parts:
        return f"没有找到匹配「{query}」的地表材料。"
    return "；".join(parts) + "。"


def resolve_refine_items(queries):
    """把玩家写的结晶名/别名解析成完整物品 ID。"""
    resolved = []
    for query in queries:
        query = (query or "").strip()
        if not query:
            continue
        name = query[:-2] if query.endswith("结晶") else query
        item = PLORT_NAMES.get(name) or PLORT_NAMES.get(query)
        if item:
            resolved.append(item + "Plort")
        elif item is None:
            resolved.append(ITEM_ALIASES.get(query, query))
    return resolved


def refinery_space(short):
    """精炼炉里该物品还剩多少可存空间；读不到时保守返回 999（交给游戏自己拒绝）。"""
    try:
        data = rpc("sr2_refinery")
    except ActionError:
        return 999
    for row in data.get("items") or []:
        if display_id(row.get("id") or "") == short:
            return int(row.get("space") or 0)
    return 999


def refine_from_storage(args):
    """把仓库里的结晶批量搬进精炼炉（仓库→背包→精炼炉，按背包每格上限分批）。

    精炼炉只能从背包接收，所以要循环「取出满一格 → 入炉」；
    每笔写操作用独立 request_id，执行后回读背包与精炼炉。"""
    targets = resolve_refine_items(args.items)
    if not targets:
        raise ActionError("请给出要入炉的结晶名称。")
    if args.apply is None:
        plan = []
        data = rpc("sr2_storage_list")
        for item in targets:
            total = 0
            for store in data.get("items") or []:
                for slot in store.get("slots") or []:
                    if slot.get("id") and display_id(slot["id"]) == display_id(item) and slot.get("count", 0) > 0:
                        total += slot["count"]
            plan.append(f"{display_id(item)}×{total}")
        return "预演 · 可把 " + "、".join(plan) + " 搬进精炼炉。"
    moved = {}
    leftover = {}
    rounds = 0
    for item in targets:
        short = display_id(item)
        for _ in range(80):
            data = rpc("sr2_storage_list")
            container, available = None, 0
            for store in data.get("items") or []:
                for slot in store.get("slots") or []:
                    if slot.get("id") and display_id(slot["id"]) == short and slot.get("count", 0) > available:
                        # 场景容器没有 storage_key，sr2_withdraw 只认「编号」或 storage_key。
                        container = store.get("storage_key") or str(store.get("index"))
                        available = slot["count"]
            if not container or available <= 0:
                break
            space = refinery_space(short)
            if space <= 0:
                leftover[short] = available
                break
            bag = rpc("sr2_vacpack")
            free = [s for s in bag["slots"] if s.get("unlocked") and not s.get("id")]
            per_slot = max([s.get("max") or 40 for s in bag["slots"]] or [40])
            if not free and not any(display_id(s.get("id") or "") == short for s in bag["slots"]):
                raise ActionError(f"背包没有空格，先清理背包再入炉（{short} 还剩 {available}）。")
            amount = min(available, per_slot, space)
            withdrawn = act("sr2_withdraw", {"storage": container, "item": item, "count": str(amount)},
                            f"{args.apply}-refine-wd-{short}-{rounds}")
            rounds += 1
            got = sum(int(row.get("moved") or 0) for row in withdrawn.get("moved") or [])
            if got <= 0:
                break
            refined = act("sr2_refine", {"item": item},
                          f"{args.apply}-refine-in-{short}-{rounds}")
            rounds += 1
            deposited = sum(int(row.get("deposited") or 0) for row in refined.get("deposited") or [])
            moved[short] = moved.get(short, 0) + deposited
            if deposited <= 0:
                leftover[short] = got
                break
    if not moved and not leftover:
        raise ActionError("没有任何结晶进炉（仓库里可能已经没有这些结晶，或精炼炉已经满了）。")
    parts = []
    if moved:
        parts.append("已入炉：" + "、".join(f"{name}×{count}" for name, count in moved.items()))
    if leftover:
        parts.append("精炼炉放不下：" + "、".join(f"{name}×{count}" for name, count in leftover.items())
                     + "（留在仓库/背包，未入炉）")
    return "；".join(parts) + "。"


def refine_from_bag(args):
    item = resolve_refine_items([args.query])[0]
    if item.isdecimal():
        raise ActionError("请指定物品名称或 ID。")
    result = act("sr2_refine", {"item": item}, args.apply)
    plan = result["plan"]
    skipped = result["skipped"]
    planned = sum(row["deposit"] for row in plan)
    if not planned:
        reasons = {
            "not_refinable": f"{args.query}不能存入精炼炉。",
            "refinery_full": f"精炼炉没有可存放{args.query}的空间。",
        }
        raise ActionError(reasons.get(skipped[0]["reason"], f"{args.query}无法入炉。")
                          if skipped else f"背包里没有{args.query}。")
    left = sum(row["count"] - row["deposit"] for row in plan) + sum(row["count"] for row in skipped)
    remainder = f" 另有 {left} 件留在背包。" if left else ""
    if args.apply is None:
        return f"预演 · 可把{args.query} {planned} 件从背包存入精炼炉。{remainder}"
    deposits = result.get("deposited") or []
    reported = sum(row["deposited"] for row in deposits)
    confirmed = (
        not result.get("failures")
        and len(deposits) == len(plan)
        and all(
            done["id"] == proposed["id"]
            and done["deposited"] == proposed["deposit"]
            and done["source_count_after"] == proposed["count"] - done["deposited"]
            and done["space_before"] - done["space_after"] == done["deposited"]
            for proposed, done in zip(plan, deposits)
        )
    )
    if not confirmed:
        raise ActionError(f"{args.query}计划入炉 {planned} 件，桥接报告入炉 {reported} 件，双边数量未完全核对。")
    return f"已把{args.query} {reported} 件从背包存入精炼炉。{remainder}"


def find_item(query):
    locations, unavailable = inventory_locations(True)
    warning = "；未能核对：" + "、".join(unavailable) if unavailable else ""
    indexed = {row["id"]: row for _, row in locations}
    try:
        item = unique_match(indexed.values(), ITEM_ALIASES.get(query, query), "物品")
    except ActionError as error:
        raise ActionError(f"在已查询的库存中，{error}{warning}") from error
    places, total, unknown = [], 0, False
    for label, row in locations:
        if row["id"] != item["id"]:
            continue
        count = row["count"]
        if count is None:
            places.append(f"{label}数量未知")
            unknown = True
        elif count > 0:
            places.append(f"{label}×{count}")
            total += count
    quantity = "已核实" if unknown or unavailable else "已查询库存合计"
    detail = "：" + "，".join(places) if places else ""
    surface = ""
    if display_id(item["id"]).endswith("Craft"):
        info = ground_materials().get(display_id(item["id"]))
        if info and (info["nearby"] or info["ready"]):
            bits = []
            if info["nearby"]:
                bits.append(f"附近 {info['nearby']}")
            if info["ready"]:
                bits.append(f"本区域料点 {info['ready']}")
            surface = "；地表可直接吸：" + "、".join(bits)
    return f"{item.get('name') or display_id(item['id'])}，{quantity} {total} 份{detail}{warning}{surface}。"


def blueprint(query):
    # sr2_blueprints filters only internal names. Match its localized titles here, across all pages.
    offset, catalog = 0, {}
    while True:
        page = rpc("sr2_blueprints", {"offset": offset, "limit": 100})
        for row in page["items"]:
            if row["id"] == query:
                return row
            catalog[row["id"]] = row
        following = page["next_offset"]
        if following is None:
            break
        if following <= offset:
            raise ActionError("蓝图目录分页异常，请重新查询。")
        offset = following
    return unique_match(catalog.values(), query, "蓝图")


def costs_text(costs):
    return "、".join(f"{display_id(c['id'])} {c['stock'] if c['stock'] is not None else '?'}/{c['required']}"
                    for c in costs) or "无费用"


def fabrication(args):
    definition = blueprint(args.query)
    label = definition.get("title") or definition.get("name") or display_id(definition["id"])
    params = {"gadget_id": definition["id"], "count": args.count}
    if args.command == "recipe":
        data = rpc("sr2_gadget_recipe", params)
        costs = data["costs"]
        details = [f"{label}×{args.count}，{'已持有蓝图' if data['blueprint_owned'] else '尚未持有蓝图'}",
                   "现有/所需：" + costs_text(costs)]
        shortages = [c for c in costs if c["missing"] is not None and c["missing"] > 0]
        if shortages:
            details.append("尚缺：" + "、".join(f"{display_id(c['id'])}×{c['missing']}" for c in shortages))
        materials = {c["id"] for c in shortages if c["kind"] == "material"}
        if materials:
            locations, unavailable = inventory_locations(False)
            outside = [f"{place} {display_id(row['id'])}×{row['count']}" for place, row in locations if row["id"] in materials]
            if outside:
                details.append("待入炉：" + "、".join(outside))
            if unavailable:
                details.append("未能核对：" + "、".join(unavailable))
            hint = ground_hint(materials)
            if hint:
                details.append(hint)
        if data["costs_covered"] is True:
            details.append("费用已齐")
        if any(c["missing"] is None for c in costs):
            details.append("部分库存未知")
        return "；".join(details) + "。"

    result = rpc("sr2_craft_gadget", {**params, "dry_run": args.apply is None}, args.apply, allow_reason=True)
    if result.get("reason"):
        reason = result["reason"]
        meaning = {"missing_blueprint": "尚未持有蓝图", "INSUFFICIENT_COST_ITEMS": "材料不足",
                   "INSUFFICIENT_CURRENCY": "货币不足", "MAXIMUM_COUNT_EXCEEDED": "达到制作上限",
                   "fabricator_item_unavailable": "制造分类尚未就绪，可打开制造台后再预演",
                   "inventory_change_unconfirmed": "尚未确认成品入库"}.get(reason, reason)
        info = "；现有/所需：" + costs_text(result["costs_before"]) if "costs_before" in result else ""
        if "stock_after" in result:
            info += f"；成品库存 {result['stock_before']} → {result['stock_after']}"
        raise ActionError(f"{label}×{args.count}，{meaning}{info}。")
    if args.apply is None and result.get("can_craft"):
        return f"预演 · 可制作{label}×{args.count}，成品现存 {result['stock_before']}；现有/所需：{costs_text(result['costs_before'])}。"
    if args.apply and result.get("applied"):
        return f"已制作{label}，成品库存 {result['stock_before']} → {result['stock_after']}（增加 {result['stock_delta']}）。"
    raise ActionError(f"{label}的制作结果尚未确认。")


def single_ranch_control(kind, label):
    found = rpc("sr2_ranch_controls", {"kind": kind, "limit": 4})
    count = found["matched_count"]
    if count == 0:
        raise ActionError(f"当前区域没有已加载的{label}。")
    if count > 1:
        choices = "、".join(row["control_id"] for row in found["items"])
        raise ActionError(f"有 {count} 个{label}，请指定目标。可选设备包括 {choices}")
    return found["items"][0]


def use_ranch_control(args):
    kind, tool, label = RANCH_CONTROL_ACTIONS[args.command]
    control = args.control_id or single_ranch_control(kind, label)["control_id"]
    data = act(tool, {"control_id": control}, args.apply)
    if args.apply is not None and not data.get("applied"):
        raise ActionError(f"{label} {control} 的操作结果未确认。")
    if kind == "feeder":
        if args.apply is None:
            return f"预演 · {control} 将投出一份 {data['food_id']}，现存 {data['food_before']} 份。"
        return f"{control} 已投出一份 {data['food_id']}，剩余 {data['food_after']} 份。"
    if kind == "silo_port":
        if args.apply is None:
            return f"预演 · 筒仓面板 {control} 当前为 {data['slot_before']} 号槽，将切到下一槽。"
        return f"筒仓面板 {control} 已从 {data['slot_before']} 号槽切到 {data['slot_after']} 号槽。"
    if args.apply is None:
        return f"预演 · 将按一次{label} {control}。"
    return f"已启动{label} {control}，物品将在后续游戏帧收集。"


def set_feeder_speed(args):
    plot = args.plot_id or single_ranch_control("feeder", "喂食器")["owner_id"]
    speed = FEED_SPEED_ALIASES.get(args.speed, args.speed)
    data = act("sr2_set_feeder_speed", {"plot_id": plot, "speed": speed}, args.apply)
    before = data["speed_before"].lower()
    target = FEED_SPEED_LABELS[speed]
    if data["already_set"]:
        return f"围栏 {plot} 的喂食速度已是{target}档。"
    if args.apply is None:
        return f"预演 · 围栏 {plot} 的喂食速度将设为{target}档。"
    if not data.get("applied") or (data.get("speed_after") or "").lower() != speed:
        raise ActionError(f"围栏 {plot} 的喂食速度未确认达到{target}档。")
    return f"围栏 {plot} 的喂食速度已从{FEED_SPEED_LABELS.get(before, before)}档调到{target}档。"


def collect_item(args):
    item = ITEM_ALIASES.get(args.query, args.query)
    data = act("sr2_collect", {"item": item, "radius": str(args.radius), "limit": str(args.count)}, args.apply)
    planned = data["plan_count"]
    if not planned:
        return f"{args.radius} 米内没有找到{args.query}。"
    if args.apply is None:
        return f"预演 · {args.radius} 米内找到 {data['found_total']} 件{args.query}，计划收取 {planned} 件。"
    moved = data.get("vacpack_delta")
    warning = data.get("warning") or ""
    if moved != planned or warning:
        confirmed = "背包增量未知" if moved is None else f"背包增加 {moved} 件"
        raise ActionError(f"{args.query}计划收取 {planned} 件，{confirmed}。{warning}")
    return f"已收取{args.query} {moved} 件到背包。"


def set_recipe_tracking(args):
    if args.command == "pin-recipe":
        definition = blueprint(args.query)
        label = definition.get("title") or display_id(definition["id"])
        params = {"action": "pin", "gadget_id": definition["id"]}
        description = f"追踪{label}配方"
        unchanged = f"已在追踪{label}配方。"
    else:
        params = {"action": "clear"}
        description = "取消配方追踪"
        unchanged = "当前没有追踪中的配方。"
    data = act("sr2_set_recipe_pin", params, args.apply)
    if data["already_set"]:
        return unchanged
    if args.apply is None:
        return f"预演 · 将{description}。"
    if not data.get("applied"):
        raise ActionError(f"{description}的结果未确认。")
    return f"已{description}。"


def save_game(args):
    data = rpc("sr2_save", {"dry_run": args.apply is None}, args.apply, allow_reason=True)
    name = data.get("save_name") or "当前存档"
    if args.apply is None:
        return f"预演 · 将保存 {name}。"
    if not data.get("saved") or data.get("invoke_error"):
        reason = data.get("invoke_error") or data.get("reason") or "未确认存档文件已写盘"
        raise ActionError(f"{name}保存未确认（{reason}）。")
    return f"已保存 {name}，最新文件 {data['newest_after']}。"


def perform(args):
    op = args.command
    if op == "clock":
        return datetime.now(timezone(timedelta(hours=8))).strftime("北京时间 %Y-%m-%d %H:%M:%S")
    if op == "time":
        return game_time()
    if op == "bag":
        return bag()
    if op == "status":
        return status()
    if op == "find":
        return find_item(args.query)
    if op == "materials":
        return nearby_materials(args)
    if op == "quote":
        return quote(args)
    if op == "stores":
        return stores(args.storage)
    if op == "consolidate":
        return consolidate_storage(args)
    if op == "plorts":
        return collect_plorts(args)
    if op == "tidy":
        return tidy_bag(args)
    if op == "refine":
        return refine_from_storage(args)
    if op == "refine-bag":
        return refine_from_bag(args)
    if op == "forecast":
        return forecast(args)
    if op in ("store", "take", "store-all", "take-all", "transfer"):
        return move_amount(args)
    if op in ("recipe", "craft"):
        return fabrication(args)
    if op in RANCH_CONTROL_ACTIONS:
        return use_ranch_control(args)
    if op == "feeder-speed":
        return set_feeder_speed(args)
    if op == "collect":
        return collect_item(args)
    if op in ("pin-recipe", "clear-recipe"):
        return set_recipe_tracking(args)
    if op == "save":
        return save_game(args)

    execute = args.apply is not None
    prefix = "" if execute else "预演 · "
    if op in ("heal", "energy"):
        label, tool = ("生命", "sr2_restore_health") if op == "heal" else ("能量", "sr2_restore_energy")
        data = act(tool, {"points": args.points}, args.apply)
        if data["already_full"]:
            return f"{prefix}{label}已满，当前 {data['before']}。"
        after = data["after"] if execute else data["target"]
        return f"{prefix}{label} {data['before']} → {after}，上限 {data['maximum']}。"
    if op == "slot":
        data = act("sr2_select_vacpack_slot", {"index": args.index}, args.apply)
        item = f"{data['item_id']}×{data['count']}" if data.get("item_id") else "空槽"
        if data["already_selected"]:
            return f"{prefix}当前已选 {args.index} 号槽，{item}。"
        after = data["after"] if execute else data["index"]
        return f"{prefix}背包 {data['before']} 号槽 → {after} 号槽，{item}。"

    raise ActionError(f"未知子命令 {op}。")


def nonnegative(value):
    number = int(value)
    if not 0 <= number <= 2147483647:
        raise argparse.ArgumentTypeError("请输入 0 到 2147483647 的整数")
    return number


def action_id(value):
    if not value.strip() or len(value) > 128:
        raise argparse.ArgumentTypeError("请求标识长度应为 1 到 128 个字符")
    return value


def search_text(value):
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError("请输入名称或 ID")
    return value


def batch_count(value):
    count = int(value)
    if not 1 <= count <= 99:
        raise argparse.ArgumentTypeError("制作次数应为 1 到 99")
    return count


def quantity(value):
    count = int(value)
    if not 1 <= count <= 2147483647:
        raise argparse.ArgumentTypeError("数量应为 1 到 2147483647 的整数")
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("status", "存档阶段、游戏时间、玩家与背包摘要"), ("clock", "本机北京时间"),
        ("time", "游戏时间"), ("bag", "背包摘要"), ("heal", "补生命"), ("energy", "补能量"),
        ("slot", "切换背包槽"), ("feed", "让指定喂食器投食一次"),
        ("find", "定位背包、仓库、精炼炉与地表材料"), ("materials", "查附近/本区域可直接吸的材料与距离"),
        ("recipe", "核对整批设备费用与缺料位置"),
        ("craft", "制作设备并核对成品库存"),
        ("quote", "查询结晶市价和整批估值"), ("stores", "列出容器或查看指定仓库的槽位"),
        ("store", "从背包向指定仓库存入定量物品"), ("take", "从指定仓库取回定量物品"),
        ("store-all", "把背包里的指定物品整批存入一座仓库"),
        ("take-all", "把一座仓库里的指定物品整批取回背包"),
        ("transfer", "在两个容器之间直接搬运指定数量的物品"),
        ("consolidate", "合并地块筒仓里物品和外观相同的堆"),
        ("plorts", "把收集器里的结晶搬进各自区域的仓库"),
        ("tidy", "整理背包：结晶进本区域筒仓、粉色/材料进精炼炉"),
        ("refine", "把仓库里的结晶批量放进精炼炉"),
        ("refine-bag", "把背包里的指定物品直接放进精炼炉"),
        ("forecast", "按供需模型估算结晶换价方向"),
        ("start-plort-collector", "启动围栏结晶收集器"),
        ("start-resource-collector", "启动设备型资源收集器"),
        ("silo-next", "切换筒仓面板至下一槽"),
        ("feeder-speed", "调整围栏喂食器的速度档位"),
        ("collect", "把附近指定物品吸进背包"),
        ("pin-recipe", "在 HUD 追踪指定设备配方"),
        ("clear-recipe", "取消当前配方追踪"),
        ("save", "立即保存当前游戏"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--reply", action="store_true", help="将摘要直接回复到游戏聊天框")
        if name in RANCH_CONTROL_ACTIONS or name in (
            "heal", "energy", "slot", "craft", "store", "take", "store-all", "take-all", "transfer", "consolidate",
            "plorts", "tidy", "refine", "refine-bag",
            "feeder-speed", "collect", "pin-recipe", "clear-recipe", "save",
        ):
            sub.add_argument("--apply", metavar="REQUEST_ID", type=action_id, help="实际执行。同一次动作重试沿用原标识")
        if name in ("heal", "energy"):
            sub.add_argument("points", nargs="?", default=0, type=nonnegative, help="补充点数，省略或填 0 为补满")
        elif name == "slot":
            sub.add_argument("index", type=nonnegative, help="背包槽编号，从 0 开始")
        elif name in RANCH_CONTROL_ACTIONS:
            sub.add_argument("control_id", nargs="?", type=search_text, help="现场设备 ID；只有一个同类型设备时可省略")
        elif name == "feeder-speed":
            sub.add_argument("speed", choices=(*FEED_SPEED_LABELS, *FEED_SPEED_ALIASES), help="慢、普通或快")
            sub.add_argument("plot_id", nargs="?", type=search_text, help="完整围栏地块 ID；只有一个喂食器时可省略")
        elif name == "collect":
            sub.add_argument("query", type=search_text, help="物品名称、短名或完整 ID")
            sub.add_argument("count", nargs="?", type=quantity, default=1, help="最多收取件数，默认 1")
            sub.add_argument("--radius", type=quantity, default=20, help="搜索半径（米），默认 20")
        elif name == "pin-recipe":
            sub.add_argument("query", type=search_text, help="已拥有的蓝图名称、短名或完整 ID")
        elif name == "stores":
            sub.add_argument("storage", type=storage_selector, nargs="?", help="main 或容器标识，省略则列出容器")
        elif name == "consolidate":
            sub.add_argument("--storage", type=storage_selector, help="指定一座地块筒仓，省略则检查所有地块筒仓")
        elif name == "transfer":
            sub.add_argument("query", type=search_text, help="物品名称、短名或完整 ID")
            sub.add_argument("count", type=quantity, help="两个容器间搬运的总件数")
            sub.add_argument("--from", dest="source", type=storage_selector, required=True, help="来源容器标识或 main")
            sub.add_argument("--to", dest="storage", type=storage_selector, required=True, help="目标容器标识或 main")
        elif name in ("store-all", "take-all"):
            sub.add_argument("query", type=search_text, help="物品名称、短名或完整 ID")
            sub.set_defaults(count="all")
            sub.add_argument("--storage", type=storage_selector, default=MAIN_STORAGE, help="默认主仓，也可填容器标识")
        elif name in ("store", "take", "quote"):
            sub.add_argument("query", type=search_text, help="物品名称、短名或完整 ID")
            if name == "quote":
                sub.add_argument("count", type=quantity, nargs="?", default=1, help="估值颗数，默认 1")
            else:
                sub.add_argument("count", type=quantity, help="本次搬运的总件数")
                sub.add_argument("--storage", type=storage_selector, default=MAIN_STORAGE, help="默认主仓，也可填 stores 列出的标识")
        elif name == "plorts":
            sub.add_argument("--target", type=storage_selector, default=None,
                             help="可选：强制指定目标仓库；省略则按收集器所在区域自动选本区域筒仓")
        elif name == "materials":
            sub.add_argument("query", type=search_text, nargs="?", default=None,
                             help="材料名称/短名；留空则列出全部")
            sub.add_argument("--radius", type=quantity, default=60, help="附近可吸搜索半径（米），默认 60")
        elif name == "refine":
            sub.add_argument("items", nargs="+", type=search_text,
                             help="结晶名称/别名，可一次给多个（如 荧光 蝙蝠 藤蔓）")
        elif name == "refine-bag":
            sub.add_argument("query", type=search_text, help="把背包里这种物品尽量存入精炼炉")
        elif name == "forecast":
            sub.add_argument("query", type=search_text, nargs="?", default="",
                             help="结晶名称；留空则列出全部（最多 6 种）")
            sub.add_argument("count", type=quantity, nargs="?", default=0,
                             help="计划卖出颗数，用于估算明日中性价")
        elif name in ("find", "recipe", "craft"):
            sub.add_argument("query", type=search_text, help="名称、短名或完整 ID")
            if name != "find":
                sub.add_argument("count", nargs="?", type=batch_count, default=1, help="制作次数，1 到 99")
    args = parser.parse_args(argv)
    try:
        summary = perform(args)
        success = True
    except BridgeError as error:
        print(f"游戏桥接连接失败：{error}", file=sys.stderr)
        return 1
    except ActionError as error:
        summary, success = f"操作未确认 · {error}", False
    print(summary)
    if args.reply:
        # ChatTools.Send accepts 400 UTF-16 code units. Keep surrogate pairs intact.
        encoded = summary.encode("utf-16-le")
        message = summary if len(encoded) <= 800 else encoded[:798].decode("utf-16-le", errors="ignore") + "…"
        key = getattr(args, "apply", None)
        reply_id = "chat-" + hashlib.sha256((key + "\0" + message).encode()).hexdigest() if key else None
        try:
            delivery = rpc("sr2_chat_send", {"text": message}, reply_id)
            if not delivery.get("delivered"):
                raise ActionError("delivery_unconfirmed")
        except (ActionError, BridgeError) as error:
            print(f"游戏内回信未确认 · {error}", file=sys.stderr)
            return 1
    return 0 if success else 1


if __name__ == "__main__":
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    if not sys.stderr.isatty():
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
