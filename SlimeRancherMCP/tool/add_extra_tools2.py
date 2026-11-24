# -*- coding: utf-8 -*-
"""追加第二批 7 个工具定义（幂等）。"""
import json

PATH = r"E:\SlimeRancherMCP\mcp\tools.json"

NEW_TOOLS = [
    {
        "name": "sr_gordos",
        "title": "Gordo 巨型史莱姆",
        "description": "列出全图 Gordo：是否已爆、已吃数量/目标数量、是否掉钥匙、所在区域与位置（已加载的才带坐标）。用于规划钥匙与地图解锁。",
        "kind": "read",
        "args": [
            {"name": "zone", "type": "str", "required": False, "default": "",
             "description": "按区域筛选（sr_zones 的 zone）。"},
            {"name": "radius", "type": "int", "required": False, "default": 0,
             "description": "以玩家为中心半径；0 = 不过滤。"},
            {"name": "include_popped", "type": "bool", "required": False, "default": True,
             "description": "是否包含已经爆开的 Gordo。"},
        ],
    },
    {
        "name": "sr_pods",
        "title": "宝箱 Treasure Pod",
        "description": "列出全图宝箱：开/未开、是否已加载、坐标区域、内含蓝图/皮肤、所需升级。用于清图与蓝图收集。",
        "kind": "read",
        "args": [
            {"name": "zone", "type": "str", "required": False, "default": "",
             "description": "按区域筛选。"},
            {"name": "include_open", "type": "bool", "required": False, "default": True,
             "description": "是否包含已开启的宝箱。"},
        ],
    },
    {
        "name": "sr_doors",
        "title": "钥匙门 Access Door",
        "description": "列出全图钥匙门：状态（LOCKED/OPEN/CLOSED）、解锁目标区域、所需进度、门价与位置。用于判断下一个解锁目标。",
        "kind": "read",
        "args": [
            {"name": "zone", "type": "str", "required": False, "default": "",
             "description": "按区域筛选。"},
        ],
    },
    {
        "name": "sr_saves",
        "title": "存档列表与摘要",
        "description": "读取磁盘上所有 .sav：槽名、天数、新币、百科数、版本、保存时间，并标记当前存档；不依赖游戏读档状态。配合 sr_backup 做备份核对。",
        "kind": "read",
        "args": [],
    },
    {
        "name": "sr_harvest_all",
        "title": "批量收获钻井",
        "description": "对所有已完成周期的钻井/泵机/蜂房触发原生吐料（OnInteract）。默认预演只列可收获目标；执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {"name": "zone", "type": "str", "required": False, "default": "",
             "description": "只处理指定区域。"},
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填唯一标识。"},
        ],
    },
    {
        "name": "sr_sell_all",
        "title": "批量出售某种结晶",
        "description": "按来源（背包/仓储/两者）批量出售同一种结晶，支持最低单价与总量上限，逐槽返回成交明细与总收入。默认预演；执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {"name": "item_id", "type": "str", "required": True,
             "description": "结晶 item_id，例如 PINK_PLORT。"},
            {"name": "scope", "type": "str", "required": False, "default": "both",
             "description": "来源范围：vacpack / storage / both。"},
            {"name": "count", "type": "int", "required": False, "default": -1,
             "description": "最多出售数量；-1 = 全部。"},
            {"name": "min_unit_price", "type": "int", "required": False, "default": 0,
             "description": "低于该单价则拒绝出售。"},
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填唯一标识。"},
        ],
    },
    {
        "name": "sr_store_all",
        "title": "背包批量入仓/入精炼机",
        "description": "把背包里的结晶/材料批量搬进对应仓储（mode=storage）或精炼机（mode=refinery），逐槽返回结果。默认预演；执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {"name": "mode", "type": "str", "required": False, "default": "storage",
             "description": "storage = 搬进 PLORT/NON_SLIMES 仓储；refinery = 存进精炼机。"},
            {"name": "max_per_item", "type": "int", "required": False, "default": 999,
             "description": "每种物品最多搬多少。"},
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填唯一标识。"},
        ],
    },
]


def main() -> None:
    with open(PATH, "r", encoding="utf-8") as f:
        spec = json.load(f)
    existing = {t["name"] for t in spec["tools"]}
    added = []
    for tool in NEW_TOOLS:
        if tool["name"] in existing:
            continue
        spec["tools"].append(tool)
        added.append(tool["name"])
    spec["bridge_version"] = "0.6.0"
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    print("added:", added)
    print("total tools:", len(spec["tools"]))


if __name__ == "__main__":
    main()
