# -*- coding: utf-8 -*-
"""更新文档里的工具清单（START_HERE / mcp README / HANDOVER §16）。"""
import io
import os

ROOT = r"E:\SlimeRancherMCP"


def patch(path: str, pairs: list[tuple[str, str]]) -> None:
    text = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in text:
            print("WARN not found:", path, old[:60].replace("\n", " "))
            continue
        text = text.replace(old, new)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("patched:", path)


patch(
    os.path.join(ROOT, "docs", "START_HERE.md"),
    [
        (
            "sr_exchange / sr_progress / sr_mail / sr_pedia`",
            "sr_exchange / sr_progress / sr_mail / sr_pedia / sr_backup / sr_slimes / sr_corrals / sr_overview`",
        ),
        (
            "sr_program_drone / sr_recharge_drone / sr_deliver`",
            "sr_program_drone / sr_recharge_drone / sr_deliver / sr_save / sr_silo_port / sr_collect_all / sr_feed`",
        ),
    ],
)

patch(
    os.path.join(ROOT, "mcp", "README.md"),
    [
        (
            "| 委托、玩家升级、记录 | `sr_exchange`、`sr_player_shop`、`sr_progress`、`sr_mail`、`sr_pedia` | `sr_deliver`、`sr_buy_player_upgrade` |",
            "| 委托、玩家升级、记录 | `sr_exchange`、`sr_player_shop`、`sr_progress`、`sr_mail`、`sr_pedia` | `sr_deliver`、`sr_buy_player_upgrade` |\n"
            "| 存档与备份 | `sr_backup` | `sr_save` |\n"
            "| 围栏、史莱姆体检 | `sr_corrals`、`sr_slimes` | `sr_collect_all`、`sr_feed` |\n"
            "| 筒仓仓口可见性 | `sr_storage`（看 ports/accessible） | `sr_silo_port` |\n"
            "| 一次汇总巡检 | `sr_overview` | |",
        )
    ],
)

handover = os.path.join(ROOT, "docs", "HANDOVER.md")
section = """

---

## 16. 扩展工具 v0.5.0（2026-11-17，新增 8 个工具，共 52 个）

动机：补齐「以防万一」的安全能力、围栏/史莱姆视角、以及高频管家动作。

| 新工具 | 类型 | 作用 |
| --- | --- | --- |
| `sr_backup` | 读（文件） | 把当前 `.sav` 复制到 `backups/auto_<时间戳>/` 并回传 SHA256；不依赖游戏是否读档 |
| `sr_save` | 写 | 调 `AutoSaveDirector.SaveAllNow()` 原生保存，再回读文件清单与哈希；默认预演 |
| `sr_silo_port` | 写 | 切换筒仓仓口显示的内部槽（`SiloStorageActivator` + `LandPlotModel.siloStorageIndices`），解决「东西在仓里但仓口看不到」 |
| `sr_slimes` | 读 | 枚举 `GameModel.AllActors()` 里的史莱姆/大型/巨型：位置、区域集、饥饿/恐惧/躁动、feral、最近围栏；含休眠个体（不依赖 active） |
| `sr_corrals` | 读 | 围栏体检：附近史莱姆、地上结晶（含休眠实体）、收集器、喂食器、仓储与告警 |
| `sr_collect_all` | 写 | 批量触发已加载收集器 `StartCollection()` |
| `sr_feed` | 写 | 从 FOOD 仓储/背包把食物搬进喂食器（复用已验证的 `sr_move` 逻辑，先给后扣） |
| `sr_overview` | 读 | 一次汇总玩家/时间/市场/委托/史莱姆/仓储/设备/告警 |

实现：新增 `plugin/SlimeRancherBridge/RanchExtras.cs`（同一 `RanchTools` partial class，复用现有 helper），在 `RegisterActions()` 里调用 `RegisterExtras(server)`；定义追加在 `mcp/tools.json`。
编译已通过（125,952 B）。**部署状态：未部署**——游戏正在运行，按约定不擅自关闭；下次游戏退出后执行 `build.ps1 -Deploy`，并重载 MCP 会话即可看到 52 个工具。

注意：
- `sr_save` / `sr_silo_port` / `sr_collect_all` / `sr_feed` 都是写工具，遵守 `dry_run=true` 默认 + `request_id` + 回读对账；
- `sr_save` 允许在暂停状态执行（存盘不依赖模拟推进），其余写工具仍受 `GAME_PAUSED` 保护；
- 新增了对 `UnityEngine.AnimationModule.dll` 的编译引用（仓口动画 `SetInteger`）。
"""

text = io.open(handover, encoding="utf-8").read()
if "## 16. 扩展工具 v0.5.0" not in text:
    text = text.rstrip() + "\n" + section
    io.open(handover, "w", encoding="utf-8", newline="\n").write(text)
    print("patched:", handover)
else:
    print("HANDOVER already has §16")
