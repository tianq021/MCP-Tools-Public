# -*- coding: utf-8 -*-
"""第三批 3 个工具定义 + 文档同步（v0.7.0，共 62 个）。"""
import io
import json
import os

ROOT = r"E:\SlimeRancherMCP"
TOOLS = os.path.join(ROOT, "mcp", "tools.json")

NEW_TOOLS = [
    {
        "name": "sr_slime_info",
        "title": "史莱姆饮食与产出",
        "description": "查询史莱姆定义：饮食大类、最爱食物、额外可吃食物、产出结晶、是否 Largo/基础史莱姆、最爱玩具。用于规划投喂与围栏搭配。",
        "kind": "read",
        "args": [
            {"name": "item_id", "type": "str", "required": False, "default": "",
             "description": "史莱姆 item_id（如 PINK_SLIME）；留空列出全部。"},
            {"name": "include_largo", "type": "bool", "required": False, "default": True,
             "description": "是否包含 Largo 组合。"},
        ],
    },
    {
        "name": "sr_exchange_pending",
        "title": "待选委托",
        "description": "查看当前待选委托人（pending_ranchers）与已有委托；判断是否需要接受、是否可自动选择。",
        "kind": "read",
        "args": [],
    },
    {
        "name": "sr_exchange_accept",
        "title": "接受/选择委托",
        "description": "接受当期委托；有多个待选委托人时必须传 rancher_id。默认预演；执行需 dry_run=false + request_id。",
        "kind": "write",
        "args": [
            {"name": "rancher_id", "type": "str", "required": False, "default": "",
             "description": "待选委托人 id（sr_exchange_pending 返回）；只有一个时可留空。"},
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
    spec["bridge_version"] = "0.7.0"
    with open(TOOLS, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    tools = spec["tools"]
    reads = [t for t in tools if t.get("kind") == "read"]
    writes = [t for t in tools if t.get("kind") == "write"]
    print("added:", added)
    print("total=%d read=%d write=%d" % (len(tools), len(reads), len(writes)))

    patch(os.path.join(ROOT, "docs", "START_HERE.md"), [
        ("> 最后更新：2026-11-17（对应桥接 **v0.6.0**，59 个 MCP 工具）",
         "> 最后更新：2026-11-17（对应桥接 **v0.7.0**，62 个 MCP 工具）"),
        ("当前状态：**可用**。游戏内当前运行 v0.4.2（44 工具）；**v0.6.0（59 工具）已编译，待游戏退出后 `build.ps1 -Deploy` 并重载 MCP 会话生效**。DSH 里注册为 `slime_rancher`。",
         "当前状态：**可用**。游戏内当前运行 v0.4.2（44 工具）；**v0.7.0（62 工具）已编译，待游戏退出后 `build.ps1 -Deploy` 并重载 MCP 会话生效**。DSH 里注册为 `slime_rancher`。"),
        ("| `mcp/README.md` | 59 个工具的分类说明、操作示例、结果码约定 |",
         "| `mcp/README.md` | 62 个工具的分类说明、操作示例、结果码约定 |"),
        ("sr_gordos / sr_pods / sr_doors / sr_saves`",
         "sr_gordos / sr_pods / sr_doors / sr_saves / sr_slime_info / sr_exchange_pending`"),
        ("sr_harvest_all / sr_sell_all / sr_store_all`",
         "sr_harvest_all / sr_sell_all / sr_store_all / sr_exchange_accept`"),
    ])

    patch(os.path.join(ROOT, "mcp", "README.md"), [
        ("59 个工具的参数、取值范围和默认值由", "62 个工具的参数、取值范围和默认值由"),
        ("| 批量收获/出售/入仓 | | `sr_harvest_all`、`sr_sell_all`、`sr_store_all` |",
         "| 批量收获/出售/入仓 | | `sr_harvest_all`、`sr_sell_all`、`sr_store_all` |\n"
         "| 史莱姆百科 | `sr_slime_info` | |\n"
         "| 委托接取 | `sr_exchange_pending` | `sr_exchange_accept` |"),
    ])

    patch(os.path.join(ROOT, "README.md"), [
        ("状态：**v0.6.0，共 59 个 MCP 工具**（36 个查询和 23 个操作）；v0.5.0/v0.6.0 扩展工具已编译，待游戏退出后 -Deploy 生效（游戏内当前仍为 v0.4.2 / 44 工具）。",
         "状态：**v0.7.0，共 62 个 MCP 工具**（37 个查询和 25 个操作）；v0.5–v0.7 扩展工具已编译，待游戏退出后 -Deploy 生效（游戏内当前仍为 v0.4.2 / 44 工具）。"),
    ])

    handover = os.path.join(ROOT, "docs", "HANDOVER.md")
    section = """

---

## 18. 第三批扩展工具 v0.7.0（2026-11-17，新增 3 个工具，共 62 个）

| 新工具 | 类型 | 作用 |
| --- | --- | --- |
| `sr_slime_info` | 读 | 史莱姆定义：饮食大类、最爱食物、额外食物、产出、Largo 组合、最爱玩具（来自 `GameContext.SlimeDefinitions` + `SlimeDefinition.Diet`） |
| `sr_exchange_pending` | 读 | 待选委托人 `pending_ranchers` + 已有委托；判断能否自动选择（经反射读取 `ExchangeDirector` 私有 `worldModel.pendingOfferRancherIds`） |
| `sr_exchange_accept` | 写 | 接受当期委托：`SelectDailyOffer(rancher, true)` 或单个待选时 `TryToAcceptNewOffer()`；多待选必须传 `rancher_id`，否则明确报错 |

数据来源（反编译确认）：
- `SlimeDefinition`：`IdentifiableId / Name / IsLargo / BaseSlimes / Diet(Favorites, AdditionalFoods, MajorFoodGroups, Produces, FavoriteProductionCount) / FavoriteToys`；
- `SlimeDefinitions.Slimes` + `GetSlimeByIdentifiableId(id)`；
- `WorldModel.pendingOfferRancherIds / currOffers`（`GameModel` 不继承 WorldModel，故用反射取 `ExchangeDirector.worldModel` 私有字段）；
- `ExchangeDirector.SelectDailyOffer / TryToAcceptNewOffer / GetOfferId / GetOfferRancherId`。

状态：编译通过（144,896 B）。**未部署**（游戏在运行）。部署后版本 0.7.0、62 个工具。
"""
    text = io.open(handover, encoding="utf-8").read()
    if "## 18. 第三批扩展工具 v0.7.0" not in text:
        io.open(handover, "w", encoding="utf-8", newline="\n").write(text.rstrip() + "\n" + section)
        print("patched:", handover)
    else:
        print("HANDOVER already has §18")


if __name__ == "__main__":
    main()
