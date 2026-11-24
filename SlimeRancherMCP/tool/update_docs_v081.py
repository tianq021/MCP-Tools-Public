# -*- coding: utf-8 -*-
"""v0.8.1 文档：运行验证记录（§20）+ 版本/状态同步。"""
import io
import os

ROOT = r"E:\SlimeRancherMCP"


def patch(path, pairs):
    text = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in text:
            print("WARN not found in", os.path.basename(path), ":", old[:60].replace("\n", " "))
            continue
        text = text.replace(old, new)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("patched:", path)


patch(os.path.join(ROOT, "docs", "START_HERE.md"), [
    ("> 最后更新：2026-11-17（对应桥接 **v0.8.0**，66 个 MCP 工具）",
     "> 最后更新：2026-11-17（对应桥接 **v0.8.1**，66 个 MCP 工具；v0.8.1 为验证后修复版，待游戏退出后部署）"),
    ("当前状态：**可用**。游戏内当前运行 v0.4.2（44 工具）；**v0.8.0（66 工具）已编译并部署到磁盘，需重启游戏 + 重载 MCP 会话生效**。DSH 里注册为 `slime_rancher`。",
     "当前状态：**可用（v0.8.0 已实测）**。游戏内运行 v0.8.0、66 个工具；**v0.8.1 修复版已编译，因游戏进程锁定 dll 暂未部署**（退出游戏后 `build.ps1 -Deploy`）。MCP 会话进程仍只注册 44 个旧工具，新工具需重载会话或先用 `tool/bridge_cli.py`。"),
])

patch(os.path.join(ROOT, "README.md"), [
    ("状态：**v0.8.0，共 66 个 MCP 工具**（40 个查询和 26 个操作）；v0.5–v0.8 扩展工具已编译并部署到磁盘，需重启游戏 + 重载会话生效（游戏内当前进程仍为 v0.4.2 / 44 工具）。",
     "状态：**v0.8.1，共 66 个 MCP 工具**（40 个查询和 26 个操作）；v0.8.0 已在游戏内实测通过（66 工具全部注册），v0.8.1 为修复版、待游戏退出后部署；MCP 会话需重载才能看到 66 个工具。"),
])

handover = os.path.join(ROOT, "docs", "HANDOVER.md")
section = """

---

## 20. v0.8.0 运行时验证 + v0.8.1 修复（2026-11-17 13:44–13:47）

### 20.1 验证环境

游戏重启后加载 **v0.8.0**：`sr_health` 返回 `bridge_version=0.8.0`，`tools` 列表 **66 项**（与 tools.json 一致）；存档世界 day 8 / 18:26 / 4193 新币 / RANCH。

> 注意：DSH 里的 MCP server 进程仍注册着会话启动时的 44 个工具（它只在进程启动时读 tools.json）。所以本轮验证用 `tool/bridge_cli.py` 直连 HTTP；要在我这里直接看到 66 个工具需重载 MCP 会话。

### 20.2 只读工具验证（全部 ok）

| 工具 | 耗时 | 结果摘要 |
| --- | --- | --- |
| `sr_watch` | 47ms | 3 条告警：69 只饥饿、95 颗地上结晶、40 只 feral |
| `sr_market_advice` | 6ms | 17 种结晶报价；藤蔓 132/基准 50、量子 104/40… 多数 SELL_NOW，饱和度 0.067 |
| `sr_saves` | 28ms | 5 个磁盘存档（均为 day 7——当前 day 8 尚未落盘）；`current` 取到游戏名 `20250628093547_1` |
| `sr_gordos` | 19ms | 56 个 Gordo（2 个已爆）；当前区域没有已加载 Gordo，故无坐标 |
| `sr_pods` | 13ms | 181 个宝箱：6 开 / 175 锁 |
| `sr_doors` | 12ms | 16 个钥匙门：11 锁 / 5 开（已加载的显示 RANCH-GROTTO / OVERGROWTH） |
| `sr_corrals` | 52ms | 4 个围栏；plot1858116711：**无收集器 + 37 颗地上结晶 + 饥饿**；plot1796182480：4 颗地上 |
| `sr_slimes` | 4027ms ⚠️ | 453 只内存史莱姆；发现性能与归属问题（见 20.4） |
| `sr_slime_info` | 13ms | PINK_SLIME：饮食 FRUIT/VEGGIES/MEAT，产出 PINK_PLORT（Favorites 为空，粉红史莱姆数据即如此） |
| `sr_overview` | 76ms | 汇总正常；告警 `HUNGRY_SLIMES:69`、`FERAL_SLIMES:40` |

### 20.3 写工具/边界验证（dry_run 或无害）

| 工具 | 结果 |
| --- | --- |
| `sr_backup` | ✅ 真实备份成功：`backups\\verify-v08_20261117_134516`（5 个 .sav + SHA256） |
| `sr_save`（dry_run） | ✅ 列出磁盘 5 个存档，未触发保存 |
| `sr_collect_all`（dry_run） | ✅ 列出 2 个已加载收集器 |
| `sr_feed` | ✅ 正确报 `TARGET_UNAVAILABLE`（该存档没有喂食器） |
| `sr_harvest_all`（dry_run） | ✅ 0 台钻井（未放置） |
| `sr_sell_all`（dry_run） | ✅ 预演正常（背包/仓里没有粉红结晶，available=0） |
| `sr_store_all`（dry_run） | ✅ 预演正常（背包无可入库物品） |
| `sr_exchange_pending` | ✅ 待选 `[ogden, viktor]`（2 个待选 → 需要 rancher_id） |
| `sr_teleport`（dry_run） | ⚠️ 目标地块处于休眠 → 正确报 `TARGET_UNAVAILABLE`（可改用坐标） |
| `sr_restore_backup` | ✅ 正确拒绝：`MUST_BE_AT_MENU` |

### 20.4 验证发现的问题与 v0.8.1 修复

| # | 问题 | 修复 |
| --- | --- | --- |
| 1 | `sr_slimes` 耗时 4027ms：每个 actor 都重新 `FindObjectsOfType<LandPlotLocation>()` | 预计算围栏列表一次（`CorralLocations`），改用 `NearestCorral` |
| 2 | 野外史莱姆被错误归属到"最近围栏"（如 549 距离也标 corral） | 距离 > 25 时归属置 null |
| 3 | `sr_saves` 的 `current` 永远 false：`SavedGame.GetName()` 返回游戏名（`20250628093547_1`），而磁盘文件是 `<游戏名>_<序号>` | 改为前缀匹配，输出 `current`/`same_game` |
| 4 | `sr_silo_port` 只接受活动地块；地块休眠时报 `TARGET_UNAVAILABLE` | 改用 `Resources.FindObjectsOfTypeAll<SiloStorage>()` + `GetComponentsInChildren(..., true)`，休眠筒仓也能切仓口（模型值持久，唤醒后生效） |

v0.8.1 已编译（155,648 B）。**部署被阻塞**：游戏进程正在运行并已加载 dll，Windows 报 `file with a user-mapped section open`，无法覆盖 → 退出游戏后执行 `build.ps1 -Deploy` 即可。

### 20.5 本轮运行数据要点（给管家的输入）

- 内存史莱姆 453 只，类型最多：PINK 136、PINK_ROCK_LARGO 40、ROCK 37、BOOM 36；
- 69 只饥饿（hunger ≥ 0.85）、40 只 feral；
- 95 颗结晶掉在地上；围栏 plot1858116711 没有收集器（建议建一个或手动清）；
- 磁盘存档全是 day 7（当前 day 8 未保存）→ 需要时用 `sr_save` 落盘，或先 `sr_backup`；
- 待选委托 2 个（ogden / viktor），需要 `sr_exchange_accept` 指定 rancher_id。
"""
text = io.open(handover, encoding="utf-8").read()
if "## 20. v0.8.0 运行时验证" not in text:
    io.open(handover, "w", encoding="utf-8", newline="\n").write(text.rstrip() + "\n" + section)
    print("patched:", handover)
else:
    print("HANDOVER already has §20")
