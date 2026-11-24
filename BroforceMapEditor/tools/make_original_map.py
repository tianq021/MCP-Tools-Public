"""生成第一张原创地图（120x40）并把地块/物件交给桥接。

用法：python tools/make_original_map.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import bridge_client as bridge  # noqa: E402

IPC = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BroforceMapEditor" / "ipc"
MAPS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BroforceMapEditor" / "maps"

WIDTH, HEIGHT = 120, 40
NAME = "AI ORIGINAL 01"
AUTHOR = "BROFORCEMAPBRIDGE"
NOTE = ("AI 原创地图 #1：丛林峡谷。120x40，底部走廊 + 8 段平台与梯子，中途有深坑要跳过；"
        "放有普通兵/箱子/弹药箱/检查点/囚笼。生成器 V0.1，2026-11-15。")

LEGEND = {"#": "Earth", "=": "Stone", "L": "Ladder", "B": "Bridge", "S": "Sand", ".": "Empty"}


def build_rows() -> list[str]:
    """返回从上到下的字符行（row 0 = 底部，写文件时反转）。"""
    grid = [["." for _ in range(WIDTH)] for _ in range(HEIGHT)]  # grid[row][col]

    def fill(col_from: int, col_to: int, row_from: int, row_to: int, char: str) -> None:
        for row in range(max(0, row_from), min(HEIGHT - 1, row_to) + 1):
            for col in range(max(0, col_from), min(WIDTH - 1, col_to) + 1):
                grid[row][col] = char

    # 地板（底部 2 行），两个深坑：40..44、70..73
    fill(0, WIDTH - 1, 0, 1, "#")
    fill(40, 44, 0, 1, ".")
    fill(70, 73, 0, 1, ".")
    # 坑上方的桥
    fill(40, 44, 7, 7, "B")
    fill(70, 73, 12, 12, "B")
    # 左右边界墙
    fill(0, 0, 0, HEIGHT - 1, "=")
    fill(WIDTH - 1, WIDTH - 1, 0, HEIGHT - 1, "=")
    # 平台（1 行厚）：(起点, 终点, 行)
    platforms = [
        (10, 18, 6), (24, 32, 10), (38, 46, 7), (52, 60, 12),
        (66, 76, 16), (84, 92, 9), (96, 104, 20), (108, 114, 6),
    ]
    for left, right, row in platforms:
        fill(left, right, row, row, "#")
    # 梯子：从地板接到平台
    ladders = [(18, 2, 6), (32, 2, 10), (46, 7, 7), (60, 2, 12),
               (76, 2, 16), (92, 2, 9), (104, 2, 20), (114, 2, 6)]
    for col, row_from, row_to in ladders:
        fill(col, col, row_from, row_to, "L")
    # 顶部装饰石
    fill(20, 24, HEIGHT - 2, HEIGHT - 2, "=")
    fill(88, 92, HEIGHT - 2, HEIGHT - 2, "=")

    rows_top_down = []
    for row in range(HEIGHT - 1, -1, -1):
        rows_top_down.append("".join(grid[row]))
    return rows_top_down


def build_doodads() -> list[str]:
    lines: list[str] = []

    def add(x: int, y: int, doodad: str, variation: int = 0) -> None:
        lines.append(f"{x},{y},{doodad},{variation}")

    # 出生点（英雄从卡车/直升机出场，落在地板走廊）
    add(3, 2, "SpawnPoint", 0)
    # 检查点
    add(30, 2, "CheckPoint", 0)
    add(55, 2, "CheckPoint", 0)
    add(95, 2, "CheckPoint", 0)
    # 敌人：走廊 + 平台
    corridor = [20, 27, 35, 48, 58, 64, 80, 88, 100, 110]
    for x in corridor:
        add(x, 2, "Mook", 0)
    platform_enemies = [(14, 7), (28, 11), (56, 13), (70, 17), (88, 10), (110, 7)]
    for x, y in platform_enemies:
        add(x, y, "Mook", 0)
    # 物资
    for x in (12, 44, 90):
        add(x, 2, "Crate", 0)
    for x in (33, 75):
        add(x, 2, "AmmoCrate", 0)
    # 囚笼（救人）
    add(60, 13, "Cage", 0)
    # 结尾区域放一个路牌/效果做视觉地标
    add(112, 2, "Signpost", 0)
    return lines


def main() -> int:
    rows = build_rows()
    tiles_path = IPC / "tiles_original_01.txt"
    doodads_path = IPC / "doodads_original_01.txt"
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
    print("set_tiles:", bridge.send_command({
        "op": "set_tiles", "tiles": str(tiles_path), "legend": legend,
    }))
    print("add_doodads:", bridge.send_command({
        "op": "add_doodads", "doodads": str(doodads_path),
    }))
    target = MAPS / "ai_original_01.bfc"
    print("save:", bridge.send_command({
        "op": "save_bfg", "path": str(target), "copy_to_levels": "1",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
