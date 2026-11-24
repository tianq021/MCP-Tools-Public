# -*- coding: utf-8 -*-
"""v0.6.0 文档同步：工具数量、清单、HANDOVER §17。"""
import io
import os

ROOT = r"E:\SlimeRancherMCP"


def patch(path, pairs):
    text = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in text:
            print("WARN not found in", os.path.basename(path), ":", old[:50].replace("\n", " "))
            continue
        text = text.replace(old, new)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("patched:", path)


patch(os.path.join(ROOT, "docs", "START_HERE.md"), [
    ("> 最后更新：2026-11-17（对应桥接 **v0.5.0**，52 个 MCP 工具）",
     "> 最后更新：2026-11-17（对应桥接 **v0.6.0**，59 个 MCP 工具）"),
    ("当前状态：**可用**。桥接 v0.4.2 已在游戏内运行（52 个工具的 v0.5.0 已编译，待游戏退出后部署）。DSH 里注册为 `slime_rancher`。",
     "当前状态：**可用**。游戏内运行的是 v0.4.2（44 工具）；**v0.6.0（59 工具）已编译，待游戏退出后 `build.ps1 -Deploy` 并重载会话**。DSH 里注册为 `slime_rancher`。"),
    ("| `mcp/README.md` | 52 个工具的分类说明、操作示例、结果码约定 |",
     "| `mcp/README.md` | 59 个工具的分类说明、操作示例、结果码约定 |"),
    ("sr_pedia / sr_backup / sr_slimes / sr_corrals / sr_overview`",
     "sr_pedia / sr_backup / sr_slimes / sr_corrals / sr_overview / sr_gordos / sr_pods / sr_doors / sr_saves`"),
    ("sr_silo_port / sr_collect_all / sr_feed`",
     "sr_silo_port / sr_collect_all / sr_feed / sr_harvest_all / sr_sell_all / sr_store_all`"),
])

patch(os.path.join(ROOT, "mcp", "README.md"), [
    ("52 个工具的参数、取值范围和默认值由", "59 个工具的参数、取值范围和默认值由"),
    ("| 一次汇总巡检 | `sr_overview` | |",
     "| 一次汇总巡检 | `sr_overview` | |\n"
     "| 探索：Gordo/宝箱/钥匙门 | `sr_gordos`、`sr_pods`、`sr_doors` | |\n"
     "| 存档管理 | `sr_saves` | |\n"
     "| 批量收获/出售/入仓 | | `sr_harvest_all`、`sr_sell_all`、`sr_store_all` |"),
])

patch(os.path.join(ROOT, "README.md"), [
    ("状态：**v0.5.0，共 52 个 MCP 工具**（32 个查询和 20 个操作）；v0.5.0 扩展工具已编译，待游戏退出后 `-Deploy` 生效。",
     "状态：**v0.6.0，共 59 个 MCP 工具**（36 个查询和 23 个操作）；v0.5.0/v0.6.0 扩展工具已编译，待游戏退出后 `-Deploy` 生效（游戏内当前仍为 v0.4.2 / 44 工具）。"),
])

handover = os.path.join(ROOT, "docs", "HANDOVER.md")
section = """

---

## 17. 第二批扩展工具 v0.6.0（2026-11-17，新增 7 个工具，共 59 个）

动机：补齐「探索规划」和「高频批量管家动作」。

| 新工具 | 类型 | 作用 |
| --- | --- | --- |
| `sr_gordos` | 读 | 全图 Gordo：已吃/目标数量、是否爆、是否掉钥匙、区域/坐标；用于规划钥匙和地图解锁 |
| `sr_pods` | 读 | 全图宝箱：开/未开、蓝图/皮肤、所需升级、区域/坐标 |
| `sr_doors` | 读 | 全图钥匙门：状态、目标区域、进度、门价 |
| `sr_saves` | 读 | 磁盘所有 `.sav` 摘要（天数/新币/百科/版本/保存时间/当前存档），配合 `sr_backup` 核对备份 |
| `sr_harvest_all` | 写 | 批量触发已完成周期的钻机/泵机/蜂房（`Extractor.OnInteract`） |
| `sr_sell_all` | 写 | 按来源（背包/仓储/两者）批量卖同一种结晶，支持 `min_unit_price`、总量上限，逐槽回执与总收入 |
| `sr_store_all` | 写 | 背包批量入仓（PLORT/NON_SLIMES）或入精炼机，逐槽回执 |

实现：`plugin/SlimeRancherBridge/RanchExtras2.cs`（同一 partial class），`RegisterActions()` 里追加 `RegisterExtras2(server)`；定义追加在 `mcp/tools.json`（`bridge_version=0.6.0`）。

数据来源（反编译确认）：
- Gordo：`GameModel.AllGordos()` + `GordoModel.gordoEatenCount/HasPopped()/DropsKey()` + 已加载 `GordoEat`（`targetCount`、`slimeDefinition`）；
- 宝箱：`GameModel.AllPods()` + `TreasurePodModel.state` + `TreasurePod`（`blueprint`/`needsUpgrade`/`requiredUpgrade`）；
- 钥匙门：`GameModel.AllDoors()` + `AccessDoorModel.state` + `AccessDoor`（`lockedRegionId`/`progress`）；
- 存档摘要：`AutoSaveDirector.StorageProvider.GetAvailableGames()` + `AutoSaveDirector.LoadSummary(name)`（`GameData.Summary`：displayName/day/currency/pediaCount/version/saveTimestamp）。

状态：编译通过（139,776 B）。**仍未部署**（游戏在运行）；部署后版本为 0.6.0、59 个工具。
"""

text = io.open(handover, encoding="utf-8").read()
if "## 17. 第二批扩展工具 v0.6.0" not in text:
    io.open(handover, "w", encoding="utf-8", newline="\n").write(text.rstrip() + "\n" + section)
    print("patched:", handover)
else:
    print("HANDOVER already has §17")
