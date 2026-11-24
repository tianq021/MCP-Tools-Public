"""原创地图 v3：修 v2 的摆放/桥/装饰问题，中文命名。

v2 问题与对策：
- 敌人/人质/箱子半埋在方块里 → 地表物件统一放到「地面顶行 + 3」行（游戏贴图以顶部为锚点）
- 桥没连起来 → 桥面统一用 TerrainType.Bridge2
- 树/围栏/脚手架方向怪 → 先移除，只保留背景视差层
- 箱子堆一起掉落 → 物件分散摆放，且都落到地面偏移行
- 方块冗余 → 去掉岩石天花板与多余装饰填充
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import bridge_client as bridge  # noqa: E402

DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BroforceMapEditor"
IPC = DATA / "ipc"
MAPS = DATA / "maps"

WIDTH, HEIGHT = 120, 40
NAME = "AI丛林远征02"
AUTHOR = "BROFORCEMAPBRIDGE"
NOTE = ("AI 原创地图 #3：丛林远征 02。120x40，三层平台 + 梯子 + 3 个深坑木桥；"
        "地表物件按“地面+3 行”摆放，桥面用 Bridge2；生成器 V0.3，2026-11-15。")

LEGEND = {"#": "Earth", "=": "Stone", "W": "Wood", "S": "Sand",
          "L": "Ladder", "B": "Bridge2", "B1": "Bridge", ".": "Empty"}

GROUND_ROWS = (0, 1, 2)          # 地板占 0..2 行，顶行 = 2
SURFACE = GROUND_ROWS[1] + 1     # 地表顶行 = 2
STAND = SURFACE + 3              # 物件摆放行（顶部锚点修正）


def build_rows() -> list[str]:
    grid = [["." for _ in range(WIDTH)] for _ in range(HEIGHT)]

    def fill(c0: int, c1: int, r0: int, r1: int, ch: str) -> None:
        for row in range(max(0, r0), min(HEIGHT - 1, r1) + 1):
            for col in range(max(0, c0), min(WIDTH - 1, c1) + 1):
                grid[row][col] = ch

    fill(0, WIDTH - 1, 0, 2, "#")
    fill(20, 30, 0, 1, "S")
    fill(80, 92, 0, 1, "S")

    pits = [(44, 47), (66, 69), (96, 99)]
    for left, right in pits:
        fill(left, right, 0, 2, ".")
        fill(left, right, 7, 7, "B")   # Bridge2 桥面

    fill(0, 0, 0, HEIGHT - 1, "=")
    fill(WIDTH - 1, WIDTH - 1, 0, HEIGHT - 1, "=")

    tiers = [(10, 18, 7), (24, 34, 11), (38, 50, 7),
             (56, 64, 12), (72, 84, 8), (88, 100, 13), (104, 114, 7)]
    for left, right, row in tiers:
        fill(left, right, row, row, "#")
    ladders = [(18, 3, 7), (34, 3, 11), (50, 3, 7), (64, 3, 12),
               (84, 3, 8), (100, 3, 13), (114, 3, 7)]
    for col, r0, r1 in ladders:
        fill(col, col, r0, r1, "L")

    rows = []
    for row in range(HEIGHT - 1, -1, -1):
        rows.append("".join(grid[row]))
    return rows


def build_doodads() -> list[str]:
    lines: list[str] = []

    def add(x: int, y: int, kind: str, variation: int = 0) -> None:
        lines.append(f"{x},{y},{kind},{variation}")

    # 背景视差（无碰撞，只做氛围）
    for x in range(6, WIDTH - 6, 14):
        add(x, 6, "Parallax1", x % 3)
        add(x + 5, 6, "Parallax2", x % 4)

    # 出生点 / 检查点（地表偏移行）
    add(3, STAND, "SpawnPoint", 0)
    for x in (30, 62, 92):
        add(x, STAND, "CheckPoint", 0)

    # 走廊敌人（间隔拉开，避免堆叠）
    for x in (16, 26, 36, 54, 58, 76, 88, 104, 110):
        add(x, STAND, "Mook", x % 2)
    # 平台敌人
    for x, row in ((14, 10), (28, 14), (44, 10), (60, 15), (78, 11), (94, 16), (108, 10)):
        add(x, row + 3, "Mook", x % 2)

    # 物资（分散，落到地表偏移行）
    for x in (12, 40, 74, 102):
        add(x, STAND, "Crate", 0)
    for x in (22, 68):
        add(x, STAND, "AmmoCrate", 0)
    # 可救援：人质与囚笼
    add(48, STAND, "VillagerCaptured", 0)
    add(50, STAND, "VillagerCaptured", 0)
    add(66 + 1, 16, "Cage", 0)

    # 终点：撤离载具
    add(WIDTH - 6, STAND, "Vehicle", 0)
    return lines


def main() -> int:
    rows = build_rows()
    tiles_path = IPC / "tiles_original_03.txt"
    doodads_path = IPC / "doodads_original_03.txt"
    IPC.mkdir(parents=True, exist_ok=True)
    tiles_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    doodads_path.write_text("\n".join(build_doodads()) + "\n", encoding="utf-8")
    legend = ",".join(f"{char}:{name}" for char, name in LEGEND.items())

    bridge.ensure_ready()
    print("new_map:", bridge.send_command({
        "op": "new_map", "name": NAME, "author": AUTHOR, "description": NOTE,
        "width": WIDTH, "height": HEIGHT, "theme": 0, "music": 3, "weather": 2,
        "camera": 0, "spawn": 0,
    }))
    print("set_tiles:", bridge.send_command({"op": "set_tiles", "tiles": str(tiles_path), "legend": legend}))
    print("add_doodads:", bridge.send_command({"op": "add_doodads", "doodads": str(doodads_path)}))
    target = MAPS / "AI丛林远征02.bfc"
    print("save:", bridge.send_command({"op": "save_bfg", "path": str(target), "copy_to_levels": "1"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
