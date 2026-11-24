# -*- coding: utf-8 -*-
"""把 7 个扩展工具定义追加到 mcp/tools.json（幂等：已存在则跳过）。"""
import json

PATH = r"E:\SlimeRancherMCP\mcp\tools.json"

NEW_TOOLS = [
    {
        "name": "sr_backup",
        "title": "备份存档",
        "description": "把当前 .sav 复制到 backups 目录并回传 SHA256（不依赖游戏是否读档，主菜单也可用）。",
        "kind": "read",
        "args": [
            {"name": "label", "type": "str", "required": False, "default": "auto",
             "description": "备份目录前缀，例如 manual、before-sell。"}
        ],
    },
    {
        "name": "sr_save",
        "title": "立即保存游戏",
        "description": "调用游戏原生保存（存档+档案）并回读保存文件哈希。默认预演，只列磁盘现状；执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演，不写入存档。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填本次动作唯一标识；重试保留原值。"},
        ],
    },
    {
        "name": "sr_silo_port",
        "title": "切换筒仓仓口槽位",
        "description": "把筒仓仓口切换到指定内部槽（等于在游戏里按仓口按钮切槽），让该槽的结晶在仓口可见/可取出。默认预演，执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {"name": "plot_id", "type": "str", "required": False, "default": "",
             "description": "筒仓地块 ID（sr_plots 返回）。与 storage_id 二选一。"},
            {"name": "storage_id", "type": "int", "required": False, "default": 0,
             "description": "筒仓 storage_id（sr_storage 返回）。与 plot_id 二选一。"},
            {"name": "slot", "type": "int", "required": False, "default": -1,
             "description": "目标内部槽号；留空(-1)只列出仓口与可访问槽。"},
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填唯一标识。"},
        ],
    },
    {
        "name": "sr_slimes",
        "title": "史莱姆清单",
        "description": "列出内存中的史莱姆/大型/巨型：种类、位置、区域集、饥饿/恐惧/躁动、是否野生狂暴、最近围栏地块与距离。按玩家距离排序分页。",
        "kind": "read",
        "args": [
            {"name": "radius", "type": "int", "required": False, "default": 0,
             "description": "以玩家为中心的半径；0 = 不过滤（全部内存史莱姆）。"},
            {"name": "plot_id", "type": "str", "required": False, "default": "",
             "description": "只看某个围栏附近的史莱姆（最近围栏 = 该地块且距离 ≤25）。"},
            {"name": "include_feral", "type": "bool", "required": False, "default": True,
             "description": "是否包含野生狂暴（feral）个体。"},
            {"name": "offset", "type": "int", "required": False, "default": 0,
             "description": "分页偏移。"},
            {"name": "limit", "type": "int", "required": False, "default": 60,
             "description": "单页条数 1-200。"},
        ],
    },
    {
        "name": "sr_corrals",
        "title": "围栏体检",
        "description": "逐个围栏汇总：附近史莱姆数量/种类/饥饿/feral、地上结晶、收集器、喂食器、仓储与告警（喂食器空/结晶落地/无收集器/仓满等）。",
        "kind": "read",
        "args": [
            {"name": "plot_id", "type": "str", "required": False, "default": "",
             "description": "只看指定围栏地块；留空看全部。"},
            {"name": "radius", "type": "int", "required": False, "default": 14,
             "description": "判定“在围栏内”的半径，3-60。"},
        ],
    },
    {
        "name": "sr_collect_all",
        "title": "批量触发收集器",
        "description": "对已加载的结晶收集器批量触发原生吸附（等于逐个走进围栏触发）。默认预演只列目标；执行需 dry_run=false + request_id。吸附是动画过程，稍后用 sr_collectors/sr_storage 查入库。",
        "kind": "write",
        "args": [
            {"name": "plot_id", "type": "str", "required": False, "default": "",
             "description": "只处理指定围栏；留空处理全部已加载收集器。"},
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填唯一标识。"},
        ],
    },
    {
        "name": "sr_feed",
        "title": "给喂食器补料",
        "description": "从其他 FOOD 仓储（或背包）把指定食物搬进喂食器，按目标容量整批搬运并回读。默认预演；执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {"name": "feeder_id", "type": "int", "required": False, "default": -1,
             "description": "sr_feeders 返回的 feeder_id。"},
            {"name": "plot_id", "type": "str", "required": False, "default": "",
             "description": "或按围栏地块找该围栏的喂食器。"},
            {"name": "item_id", "type": "str", "required": False, "default": "",
             "description": "食物 ID；留空则用喂食器现有食物或最近的 FOOD 仓储里最多的食物。"},
            {"name": "count", "type": "int", "required": False, "default": -1,
             "description": "搬运数量；留空(-1)=按目标剩余容量补满。"},
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填唯一标识。"},
        ],
    },
    {
        "name": "sr_overview",
        "title": "牧场总览",
        "description": "一次汇总：玩家/时间/市场（含最高最低价）/委托/史莱姆统计/仓储分布/围栏与设备数量/告警清单。适合每次对话或定时巡检先调用。",
        "kind": "read",
        "args": [],
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
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    print("added:", added)
    print("total tools:", len(spec["tools"]))


if __name__ == "__main__":
    main()
