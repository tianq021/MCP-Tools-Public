# -*- coding: utf-8 -*-
"""终版文档同步：v0.8.1 / 66 工具 / 实测快照 / 部署说明。"""
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


# ---------------- START_HERE ----------------
snapshot_old = """## 7. 当前存档进度（2026-11-17 快照，供参考）

- 第 8 天，**4193 新币**，0 钥匙；
- 已解锁区域：牧场（含 Lab/Grotto/Overgrowth）、礁石、采石场、苔藓；未解锁沙漠、遗迹、荒野、DLC 区域；
- 设施：4 个围栏（3 个带集晶器）、8 个花园、3 个筒仓（各只开 3 格）、1 个池塘；
- 精炼机材料：水晶 73、辐射 73、荧光 46、喵喵 46；
- 已完成：Quarry/Moss/Lab/Grotto/Overgrowth 解锁、5 位 NPC 首次委托、2 个史莱姆门；
- 待办方向：买无人机蓝图（2000）、扩筒仓、解锁沙漠/遗迹、刷黄金史莱姆（Hat Trick）。"""

snapshot_new = """## 7. 当前存档进度（v0.8.0 实测快照，2026-11-17 13:44；会随游戏变化）

> 数据来自 `sr_overview` / `sr_watch` / `sr_saves` 实测。**每次操作前重新查，不要直接把这里当实时数据。**

- 游戏内：第 8 天 18:26，**4193 新币**，0 钥匙，位置 RANCH；
- 磁盘存档：5 个槽（`_0/_1/_2/_3/_5`）；`_3` 在 13:49 退出时自动保存为 day 8（360 KB），其余为 day 7；
- 已解锁区域：牧场（含 Lab/Grotto/Overgrowth）、礁石、采石场、苔藓；未解锁沙漠、遗迹、荒野、DLC；
- 设施：4 个围栏（3 个已加载；`plot1858116711` **没有集晶器**、37 颗结晶落地）、8 个花园、3 个筒仓（每个 3/12 格可用）、1 个池塘；没有喂食器、无人机、钻井；
- 史莱姆：内存 453 只（PINK 136 最多）；**69 只饥饿（hunger ≥0.85）**、40 只野生狂暴；
- 地上的结晶：95 颗；
- 探索：Gordo 56（2 已爆）、宝箱 181（6 开）、钥匙门 16（11 锁 / 5 开）；
- 市场：17 种结晶在售；藤蔓 132 / 马赛克 116 / 量子 104 偏高，多数提示 SELL_NOW；
- 待选委托：2 个（ogden / viktor），需要 `sr_exchange_accept` 指定 `rancher_id`；
- 精炼机材料：水晶 73、辐射 73、荧光 46、喵喵 46；
- 待办方向：补集晶器/喂食、买无人机蓝图（2000）、扩筒仓、解锁沙漠/遗迹、刷黄金史莱姆（Hat Trick）。"""

patch(os.path.join(ROOT, "docs", "START_HERE.md"), [
    ("> 最后更新：2026-11-17（对应桥接 **v0.8.1**，66 个 MCP 工具；v0.8.1 为验证后修复版，待游戏退出后部署）",
     "> 最后更新：2026-11-17（对应桥接 **v0.8.1**，66 个 MCP 工具；已部署，下次启动游戏生效）"),
    ("当前状态：**可用（v0.8.1 已部署，待启动验证）**。v0.8.0 已在游戏内实测 66 工具全部注册；v0.8.1 修复版（sr_slimes 性能/围栏归属、sr_saves 当前槽、sr_silo_port 休眠筒仓）已部署到磁盘，**下次启动游戏生效**。MCP 会话进程仍只注册 44 个旧工具，需重载会话才能看到 66 个。",
     "当前状态：**可用（v0.8.1 已部署）**。v0.8.0 已在游戏内实测 66 工具全部注册；v0.8.1 修复版（sr_slimes 性能/围栏归属、sr_saves 当前槽、sr_silo_port 休眠筒仓）已部署到磁盘，下次启动游戏生效。MCP 会话进程只在启动时读 `tools.json`，需重载会话才能看到 66 个工具；未重载时可继续用 `tool/bridge_cli.py`。"),
    (snapshot_old, snapshot_new),
])

# ---------------- HANDOVER ----------------
patch(os.path.join(ROOT, "docs", "HANDOVER.md"), [
    ("> 编写时间：2026-11-17 10:25（Asia/Shanghai），最后更新：2026-11-17（桥接 v0.4.2）",
     "> 编写时间：2026-11-17 10:25（Asia/Shanghai），最后更新：2026-11-17 13:50（桥接 **v0.8.1**，66 个 MCP 工具）"),
    ("> 当前状态：**阶段 0–2 已完成 + 工具扩展 v0.4.2（44 个 MCP 工具）**。本文档保存完整过程记录：逆向结论、API 地图、通道设计、踩坑与各阶段验证结果（§13–§15）。",
     "> 当前状态：**阶段 0–2 已完成，工具扩展到 v0.8.1（66 个 MCP 工具，已部署）**。本文档保存完整过程记录：逆向结论、API 地图、通道设计、踩坑与各批次工具、运行验证（§13–§20）。"),
    ("现在有 44 个 MCP 工具。查询类 28 个，",
     "现在有 44 个 MCP 工具（v0.4.0 当时；后续扩到 66，见 §16–§19）。查询类 28 个，"),
])

# ---------------- AGENTS.md ----------------
patch(os.path.join(ROOT, "AGENTS.md"), [
    ("- MCP：`slime_rancher`，44 个工具，工具清单在 `mcp/tools.json`。",
     "- MCP：`slime_rancher`，**66 个工具**（40 查询 + 26 写），工具清单在 `mcp/tools.json`；桥接当前 **v0.8.1** 已部署（游戏启动后生效）。"),
])

# ---------------- mcp/README 追加两节 ----------------
mcp_readme = os.path.join(ROOT, "mcp", "README.md")
extra = """

## 版本与部署（2026-11-17）

- 桥接当前：**v0.8.1**，66 个工具（40 查询 + 26 写）。
- v0.8.0 已在游戏内实测（66 工具全部注册、只读/写工具 dry_run 验证通过）；v0.8.1 为修复版：
  `sr_slimes` 性能、野外史莱姆围栏归属、`sr_saves` 当前槽标记、`sr_silo_port` 支持休眠筒仓。
- 部署：**游戏退出后**执行 `plugin/SlimeRancherBridge/build.ps1 -Deploy`。
  游戏运行中覆盖 dll 会失败：`file with a user-mapped section open`。
- MCP server 只在进程启动时读取 `tools.json`：新增/修改工具后需要**重载会话**；
  未重载时可先用 `python -B tool/bridge_cli.py call <tool>` 直连 HTTP 验证。
- 版本核对：`sr_health` 的 `bridge_version` 与 `tools` 列表长度。

## v0.5–v0.8 新增工具速查（相对 v0.4 的 44 个）

| 工具 | 一句话 |
| --- | --- |
| `sr_backup` | 备份 .sav 到 `backups/` 并回传 SHA256（主菜单也能用） |
| `sr_save` | 调游戏原生保存并回读文件哈希（默认预演） |
| `sr_saves` | 列磁盘存档摘要（天数/新币/百科/时间/当前槽） |
| `sr_restore_backup` | 从备份还原 .sav（仅主菜单；先自动再备份当前档） |
| `sr_silo_port` | 切换筒仓仓口显示槽（含休眠筒仓） |
| `sr_slimes` | 内存史莱姆清单（情绪/feral/最近围栏） |
| `sr_slime_info` | 史莱姆饮食、最爱食物、产出 |
| `sr_corrals` | 围栏体检（史莱姆/地上结晶/收集器/喂食器/告警） |
| `sr_feed` | 从 FOOD 仓搬食物进喂食器 |
| `sr_collect_all` | 批量触发收集器 |
| `sr_harvest_all` | 批量触发钻井/泵机/蜂房产出 |
| `sr_sell_all` | 按来源批量卖同一种结晶（最低单价/上限/逐槽回执） |
| `sr_store_all` | 背包批量入仓或入精炼机 |
| `sr_gordos` | 全图 Gordo 状态（已吃/是否爆/是否掉钥匙） |
| `sr_pods` | 全图宝箱开/锁与蓝图 |
| `sr_doors` | 全图钥匙门状态与目标区域 |
| `sr_exchange_pending` | 待选委托人列表 |
| `sr_exchange_accept` | 接受/选择当期委托 |
| `sr_watch` | 一次返回全部告警（喂食/结晶/饥饿/电量/委托/市场） |
| `sr_market_advice` | 价格/基准/饱和度 + SELL_NOW / HOLD / WAIT 建议 |
| `sr_teleport` | 传送到坐标或已加载地块（作弊类，需显式执行） |
| `sr_overview` | 一次汇总：玩家/时间/市场/委托/史莱姆/仓储/设备/告警 |
"""
text = io.open(mcp_readme, encoding="utf-8").read()
if "## 版本与部署（2026-11-17）" not in text:
    io.open(mcp_readme, "w", encoding="utf-8", newline="\n").write(text.rstrip() + "\n" + extra)
    print("patched:", mcp_readme)
else:
    print("mcp/README already has 版本与部署")
