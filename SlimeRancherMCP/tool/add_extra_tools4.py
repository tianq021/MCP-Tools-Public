# -*- coding: utf-8 -*-
"""第四批 4 个工具定义 + 文档同步（v0.8.0，共 66 个）。"""
import io
import json
import os

ROOT = r"E:\SlimeRancherMCP"
TOOLS = os.path.join(ROOT, "mcp", "tools.json")

NEW_TOOLS = [
    {
        "name": "sr_watch",
        "title": "告警巡检",
        "description": "一次返回所有值得注意的状态：喂食器空、收集器仓储满、地上结晶、饥饿/野生史莱姆、钻井可收获、无人机电站电量、委托到期、市场闭市、可用钥匙。",
        "kind": "read",
        "args": [],
    },
    {
        "name": "sr_market_advice",
        "title": "市场卖出建议",
        "description": "对每种结晶给出价格/基准价/市场饱和度/变化与建议（SELL_NOW / HOLD / WAIT），用于决定现在卖还是等。",
        "kind": "read",
        "args": [],
    },
    {
        "name": "sr_teleport",
        "title": "传送（作弊）",
        "description": "把玩家传送到坐标或已加载地块（作弊类，用于跨区操作）。默认预演；执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {"name": "plot_id", "type": "str", "required": False, "default": "",
             "description": "目标地块 ID（sr_plots 返回，需已加载）。与 x/z 二选一。"},
            {"name": "x", "type": "int", "required": False, "default": 0,
             "description": "目标世界坐标 X。"},
            {"name": "y", "type": "int", "required": False, "default": -99999,
             "description": "目标 Y；留空则保持当前高度。"},
            {"name": "z", "type": "int", "required": False, "default": 0,
             "description": "目标世界坐标 Z。"},
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填唯一标识。"},
        ],
    },
    {
        "name": "sr_restore_backup",
        "title": "还原备份存档",
        "description": "把 sr_backup 生成的备份目录里的 .sav 还原回存档目录；仅允许在主菜单/未读档时执行，执行前会自动再备份当前存档。默认预演。",
        "kind": "write",
        "args": [
            {"name": "backup_dir", "type": "str", "required": True,
             "description": "备份目录绝对路径（sr_backup 返回的 backup_dir，必须在 backups 下）。"},
            {"name": "dry_run", "type": "bool", "required": False, "default": True,
             "description": "默认预演。"},
            {"name": "request_id", "type": "str", "required": False, "default": "",
             "description": "执行时填唯一标识。"},
        ],
    },
]


def patch(path, pairs):
    text = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in text:
            print("WARN not found in", os.path.basename(path), ":", old[:50].replace("\n", " "))
            continue
        text = text.replace(old, new)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("patched:", path)


def main() -> None:
    with open(TOOLS, "r", encoding="utf-8") as f:
        spec = json.load(f)
    existing = {t["name"] for t in spec["tools"]}
    added = []
    for tool in NEW_TOOLS:
        if tool["name"] in existing:
            continue
        spec["tools"].append(tool)
        added.append(tool["name"])
    spec["bridge_version"] = "0.8.0"
    with open(TOOLS, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    tools = spec["tools"]
    reads = [t for t in tools if t.get("kind") == "read"]
    writes = [t for t in tools if t.get("kind") == "write"]
    print("added:", added)
    print("total=%d read=%d write=%d" % (len(tools), len(reads), len(writes)))

    patch(os.path.join(ROOT, "docs", "START_HERE.md"), [
        ("> 最后更新：2026-11-17（对应桥接 **v0.7.0**，62 个 MCP 工具）",
         "> 最后更新：2026-11-17（对应桥接 **v0.8.0**，66 个 MCP 工具）"),
        ("当前状态：**可用**。游戏内当前运行 v0.4.2（44 工具）；**v0.7.0（62 工具）已编译，待游戏退出后 `build.ps1 -Deploy` 并重载 MCP 会话生效**。DSH 里注册为 `slime_rancher`。",
         "当前状态：**可用**。游戏内当前运行 v0.4.2（44 工具）；**v0.8.0（66 工具）已编译并部署到磁盘，需重启游戏 + 重载 MCP 会话生效**。DSH 里注册为 `slime_rancher`。"),
        ("| `mcp/README.md` | 62 个工具的分类说明、操作示例、结果码约定 |",
         "| `mcp/README.md` | 66 个工具的分类说明、操作示例、结果码约定 |"),
        ("sr_slime_info / sr_exchange_pending`",
         "sr_slime_info / sr_exchange_pending / sr_watch / sr_market_advice`"),
        ("sr_store_all / sr_exchange_accept`",
         "sr_store_all / sr_exchange_accept / sr_teleport / sr_restore_backup`"),
    ])

    patch(os.path.join(ROOT, "mcp", "README.md"), [
        ("62 个工具的参数、取值范围和默认值由", "66 个工具的参数、取值范围和默认值由"),
        ("| 委托接取 | `sr_exchange_pending` | `sr_exchange_accept` |",
         "| 委托接取 | `sr_exchange_pending` | `sr_exchange_accept` |\n"
         "| 巡检/卖出建议 | `sr_watch`、`sr_market_advice` | |\n"
         "| 作弊/回滚 | | `sr_teleport`、`sr_restore_backup` |"),
    ])

    patch(os.path.join(ROOT, "README.md"), [
        ("状态：**v0.7.0，共 62 个 MCP 工具**（38 个查询和 24 个操作）；v0.5–v0.7 扩展工具已编译，待游戏退出后 -Deploy 生效（游戏内当前仍为 v0.4.2 / 44 工具）。",
         "状态：**v0.8.0，共 66 个 MCP 工具**（40 个查询和 26 个操作）；v0.5–v0.8 扩展工具已编译并部署到磁盘，需重启游戏 + 重载会话生效（游戏内当前进程仍为 v0.4.2 / 44 工具）。"),
    ])

    handover = os.path.join(ROOT, "docs", "HANDOVER.md")
    section = """

---

## 19. 第四批扩展工具 v0.8.0（2026-11-17，新增 4 个工具，共 66 个）

| 新工具 | 类型 | 作用 |
| --- | --- | --- |
| `sr_watch` | 读 | 告警巡检：喂食器空、收集器仓满、地上结晶数、饥饿/feral 数、钻井可收获、无人机电站电量（`DroneStationBattery.HasAny/Time`）、委托到期、市场闭市、可用钥匙 |
| `sr_market_advice` | 读 | 每种结晶：现价/基准价（`EconomyDirector.baseValueMap`）/饱和度（`WorldModel.marketSaturation`，反射取得）/日变化 + 建议（SELL_NOW / HOLD / WAIT） |
| `sr_teleport` | 写（作弊） | 传送玩家到已加载地块或坐标（`player.transform.position` + `Physics.SyncTransforms`）；默认预演 |
| `sr_restore_backup` | 写 | 从 `backups/` 还原 `.sav`；**仅主菜单/未读档**时允许，执行前自动再备份一份到 `before-restore_<时间戳>` |

实现：`plugin/SlimeRancherBridge/RanchExtras4.cs`；`build.ps1` 新增 `UnityEngine.PhysicsModule.dll` 引用。
状态：编译通过（154,112 B），**已部署到磁盘**（游戏进程仍在运行旧代码，需重启生效）。
"""
    text = io.open(handover, encoding="utf-8").read()
    if "## 19. 第四批扩展工具 v0.8.0" not in text:
        io.open(handover, "w", encoding="utf-8", newline="\n").write(text.rstrip() + "\n" + section)
        print("patched:", handover)
    else:
        print("HANDOVER already has §19")


if __name__ == "__main__":
    main()
