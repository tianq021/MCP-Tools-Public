"""生成第二张原创地图（160x50，丛林峡谷 v2）：地形分层 + 背景/装饰 + 敌人 + 终点载具。"""
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

WIDTH, HEIGHT = 160, 50
NAME = "AI 丛林远征 01"
AUTHOR = "BROFORCEMAPBRIDGE"
NOTE = ("AI 原创地图 #2：丛林远征。160x50，三层平台 + 4 个深坑与桥；"
        "背景视差/树木/脚手架/围栏装饰；敌人 24、检查点 4、囚笼 2、箱子/弹药箱若干、"
        "终点停放载具作为撤离点。生成器 V0.2，2026-11-15。")

LEGEND = {"#": "Earth", "=": "Stone", "W": "Wood", "S": "Sand",
          "L": "Ladder", "B": "Bridge", "C": "CaveRock", ".": "Empty"}


def build_rows() -> list[str]:
    grid = [["." for _ in range(WIDTH)] for _ in range(HEIGHT)]

    def fill(c0: int, c1: int, r0: int, r1: int, ch: str) -> None:
        for row in range(max(0, r0), min(HEIGHT - 1, r1) + 1):
            for col in range(max(0, c0), min(WIDTH - 1, c1) + 1):
                grid[row][col] = ch

    # 地面：底部 3 行 Earth，夹杂 Sand
    fill(0, WIDTH - 1, 0, 2, "#")
    fill(30, 45, 0, 1, "S")
    fill(100, 120, 0, 1, "S")
    # 深坑（4 个）
    pits = [(55, 59), (78, 82), (118, 122), (140, 144)]
    for left, right in pits:
        fill(left, right, 0, 2, ".")
    # 坑上的桥
    for left, right in pits:
        fill(left, right, 7, 7, "B")

    # 左右边界墙
    fill(0, 0, 0, HEIGHT - 1, "=")
    fill(WIDTH - 1, WIDTH - 1, 0, HEIGHT - 1, "=")
    # 顶部岩石天花板（局部）
    fill(0, 40, HEIGHT - 1, HEIGHT - 1, "C")
    fill(120, WIDTH - 1, HEIGHT - 1, HEIGHT - 1, "C")

    # 三层平台（厚度 1~2）
    tiers = [
        (12, 26, 8), (34, 50, 12), (62, 76, 8), (84, 100, 14),
        (104, 118, 10), (124, 138, 16), (146, 156, 8),
    ]
    for left, right, row in tiers:
        fill(left, right, row, row + 1, "#")
    # 高层木制平台
    fill(20, 30, 20, 20, "W")
    fill(70, 82, 22, 22, "W")
    fill(126, 140, 24, 24, "W")
    # 梯子：地面 -> 各层
    ladders = [(26, 3, 8), (50, 3, 12), (76, 3, 8), (100, 3, 14),
               (118, 3, 10), (138, 3, 16), (156, 3, 8),
               (30, 9, 20), (82, 9, 22), (140, 17, 24)]
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

    # 背景视差层（视觉丰富度）
    for x in range(4, WIDTH - 4, 12):
        add(x, 3, "Parallax1", x % 3)
        add(x + 4, 3, "Parallax2", x % 4)
        add(x + 8, 3, "Parallax3", x % 2)
    # 树木/灌木/背景树
    for x in range(8, WIDTH - 6, 16):
        add(x, 3, "Tree", x % 3)
        add(x + 6, 3, "TreeBushes", x % 2)
        add(x + 2, 3, "TreeBackground", x % 2)
        add(x + 9, 3, "TreeBushForeground", 0)
    # 室内外道具、围栏、脚手架、悬挂物
    for x in range(16, WIDTH - 10, 20):
        add(x, 3, "OutdoorDoodad", x % 3)
        add(x + 5, 3, "IndoorDoodad", x % 4)
        add(x + 10, 3, "Fence", 0)
        add(x + 3, 9, "Scaffolding", 0)
        add(x + 7, 9, "ScaffoldingBackground", 0)
        add(x + 12, 9, "HangingDoodads", 0)
    # 出生点与检查点
    add(3, 3, "SpawnPoint", 0)
    for x in (40, 90, 130):
        add(x, 3, "CheckPoint", 0)
    # 敌人：走廊 14 + 平台 10
    for x in (18, 24, 33, 44, 52, 65, 72, 86, 96, 108, 116, 128, 136, 150):
        add(x, 3, "Mook", x % 3)
    for x, y in ((20, 9), (40, 13), (66, 9), (90, 15), (110, 11), (130, 17),
                 (24, 21), (74, 23), (132, 25), (150, 9)):
        add(x, y, "Mook", x % 3)
    # 物资 / 可救援
    for x in (14, 48, 88, 132):
        add(x, 3, "Crate", 0)
    for x in (36, 76, 112):
        add(x, 3, "AmmoCrate", 0)
    add(68, 9, "Cage", 0)
    add(128, 17, "Cage", 0)
    add(56, 3, "VillagerCaptured", 0)
    add(122, 3, "VillagerCaptured", 0)
    add(104, 3, "Vehicle", 0)      # 载具：疑似撤离/交通
    add(154, 3, "Vehicle", 1)      # 终点撤离点候选
    # Signpost 需要填写文字属性，否则游戏显示 "ENTER TEXT UNDER DOODAD PROPERTIES!"，暂不放。
    return lines


def main() -> int:
    rows = build_rows()
    tiles_path = IPC / "tiles_original_02.txt"
    doodads_path = IPC / "doodads_original_02.txt"
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
    target = MAPS / "ai_original_02.bfc"
    print("save:", bridge.send_command({"op": "save_bfg", "path": str(target), "copy_to_levels": "1"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
