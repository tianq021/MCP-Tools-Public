# -*- coding: utf-8 -*-
"""记录 v0.8.1 部署完成 + 退出时自动保存的观察。"""
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
    ("当前状态：**可用（v0.8.0 已实测）**。游戏内运行 v0.8.0、66 个工具；**v0.8.1 修复版已编译，因游戏进程锁定 dll 暂未部署**（退出游戏后 `build.ps1 -Deploy`）。MCP 会话进程仍只注册 44 个旧工具，新工具需重载会话或先用 `tool/bridge_cli.py`。",
     "当前状态：**可用（v0.8.1 已部署，待启动验证）**。v0.8.0 已在游戏内实测 66 工具全部注册；v0.8.1 修复版（sr_slimes 性能/围栏归属、sr_saves 当前槽、sr_silo_port 休眠筒仓）已部署到磁盘，**下次启动游戏生效**。MCP 会话进程仍只注册 44 个旧工具，需重载会话才能看到 66 个。"),
])

patch(os.path.join(ROOT, "README.md"), [
    ("状态：**v0.8.1，共 66 个 MCP 工具**（40 个查询和 26 个操作）；v0.8.0 已在游戏内实测通过（66 工具全部注册），v0.8.1 为修复版、待游戏退出后部署；MCP 会话需重载才能看到 66 个工具。",
     "状态：**v0.8.1，共 66 个 MCP 工具**（40 个查询和 26 个操作）；v0.8.0 已在游戏内实测通过，v0.8.1 修复版已部署（下次启动生效）；MCP 会话需重载才能看到 66 个工具。"),
])

handover = os.path.join(ROOT, "docs", "HANDOVER.md")
append = """

### 20.6 v0.8.1 部署与存档观察（2026-11-17 13:49）

- 游戏已退出 → 覆盖 dll 成功：`SlimeRancherBridge.dll` **155,648 B**，dist 与已部署 SHA256 一致（`99C571829206…`），`modinfo.json` = 0.8.1；
- 退出时游戏**自动保存**：`20250628093547_1_3.sav` 更新到 13:49:16（360,416 B，day 8）→ 之前"搬仓 + 卖 60 辐射 / 59 水晶"的结果已落盘；
- 盘上共 5 个存档（`_0/_1/_2/_3/_5`），已额外备份到 `backups/after-exit_20261117_134940/`；
- 下次启动即可验证 4 个修复：`sr_slimes` 耗时、野外史莱姆围栏归属、`sr_saves` 的 `current` 标记、`sr_silo_port` 对休眠筒仓的支持。
"""
text = io.open(handover, encoding="utf-8").read()
if "### 20.6 v0.8.1 部署与存档观察" not in text:
    io.open(handover, "w", encoding="utf-8", newline="\n").write(text.rstrip() + "\n" + append)
    print("patched:", handover)
else:
    print("HANDOVER already has 20.6")
