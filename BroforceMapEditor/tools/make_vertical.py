"""赤色远征 · 纵向开凿版：120×192，整图填满岩石后开凿六层群系。

自下而上：地狱 → 异形巢穴 → 洞窟 → 工厂 → 村寨 → 要塞（顶部终点）。
地形密集（只有开挖出的房间/走廊是空气），无大片空天；不使用雾/力场等粒子装饰避免掉帧。
"""
from __future__ import annotations

import collections
import json
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import map_authoring as author  # noqa: E402

MAP_ID = "red_vertical_01"
W, H = 120, 192
NAME = "赤色远征 · 纵向开凿"
NOTE = ("AI 原创纵向地图：整图填满岩石后开凿，无空天。六层群系自下而上："
        "地狱 → 异形巢穴 → 洞窟 → 工厂 → 村寨 → 要塞顶（撤离）；"
        "楼层间用竖井梯连接，房间内密集布置战斗组、囚笼、物资与装饰。生成器 V5.0，2026-11-15。")

LEGEND = {
    ".": "Empty", "#": "Earth", "S": "Stone", "W": "Wood", "N": "Sand",
    "T": "Steel", "L": "Ladder", "B": "Bridge", "G": "Bridge2", "R": "CaveRock",
    "H": "ThatchRoof", "K": "Barrel", "P": "PropaneBarrel", "U": "Bunker",
    "I": "Pipe", "O": "Roof", "A": "HutScaffolding", "V": "WatchTower",
    "Q": "Statue", "a": "AlienEarth", "l": "AlienLadder", "f": "AlienFlesh",
    "e": "AlienEgg", "x": "HellEarth", "z": "Skulls", "c": "Corpses", "d": "Bones",
    "j": "BackgroundEarth", "k": "BackgroundStone", "w": "WoodBackground",
    "y": "BackgroundFactory",
}

# 层带：(名称, y0, y1, 地面材质, 背景)
BANDS = [
    ("地狱", 0, 30, "x", "k"),
    ("异形巢穴", 31, 63, "a", "k"),
    ("洞窟", 64, 95, "R", "k"),
    ("工厂", 96, 127, "T", "y"),
    ("村寨", 128, 159, "W", "w"),
    ("要塞", 160, 191, "S", "y"),
]


def build():
    grid = [["." for _ in range(W)] for _ in range(H)]
    bg = [["." for _ in range(W)] for _ in range(H)]

    # 1) 整图填满对应材质（密集岩体）
    for title, y0, y1, mat, bgch in BANDS:
        for y in range(y0, y1 + 1):
            for x in range(W):
                grid[y][x] = mat
                bg[y][x] = bgch

    def carve(x0, x1, y0, y1):
        for x in range(max(0, x0), min(W - 1, x1) + 1):
            for y in range(max(0, y0), min(H - 1, y1) + 1):
                grid[y][x] = "."

    def put(x, y, ch):
        if 0 <= x < W and 0 <= y < H:
            grid[y][x] = ch

    floors = []          # (band_index, floor_y, x0, x1)
    shafts = []          # (x, y0, y1)
    # 2) 每层开挖一条走廊 + 若干房间，交替左右方向
    for index, (title, y0, y1, mat, bgch) in enumerate(BANDS):
        floor_y = y0 + 4
        # 走廊：整层横向贯通，高 4 格
        carve(6, W - 7, floor_y + 1, floor_y + 4)
        floors.append((index, floor_y, 6, W - 7))
        # 房间：每层 4 个加高洞穴
        for room in range(4):
            cx = 14 + room * 26
            carve(cx, cx + 14, floor_y + 1, floor_y + 9)
            # 房间顶保留岩体，形成"洞顶"
        # 连接上层竖井：交替在左右端
        if index + 1 < len(BANDS):
            next_floor = BANDS[index + 1][1] + 4
            sx = W - 12 if index % 2 == 0 else 10
            for y in range(floor_y + 1, next_floor + 1):
                put(sx, y, "L")
                put(sx + 1, y, ".")
            shafts.append((sx, floor_y + 1, next_floor))

    # 3) 洞顶材质装饰：异形/地狱层用肉壁/骨岩
    for title, y0, y1, mat, bgch in BANDS:
        if title == "异形巢穴":
            for x in range(6, W - 6, 9):
                put(x, y0 + 18, "f")
        if title == "地狱":
            for x in range(8, W - 8, 7):
                put(x, y0 + 16, "d")

    # 出生：最底层左端房间
    spawn_x, spawn_y = 10, BANDS[0][1] + 5
    carve(spawn_x - 2, spawn_x + 4, spawn_y, spawn_y + 3)

    # 4) 物件
    doodads = []
    SOLID = author.SOLID
    used = set()

    def place(x, y, kind, variation=0, need_solid_under=True):
        if not (0 <= x < W and 0 <= y < H) or (x, y) in used:
            return False
        if grid[y][x] != ".":
            return False
        if need_solid_under and (y < 1 or grid[y - 1][x] not in SOLID):
            return False
        used.add((x, y))
        doodads.append({"x": x, "y": y, "type": kind, "variation": variation})
        return True

    def on_floor(x, floor_y, kind, variation=0, offset=1):
        return place(x, floor_y + offset, kind, variation)

    doodads.append({"x": spawn_x, "y": spawn_y, "type": "SpawnPoint", "variation": 0})
    used.add((spawn_x, spawn_y))

    # 每层：检查点（层入口）+ 战斗组 + 补给 + 装饰 + 囚笼
    decor_by_band = {
        "地狱": ["HellDecoration", "ParallaxDecoration"],
        "异形巢穴": ["AlienFronds", "AlienBackgroundDecoration"],
        "洞窟": ["CaveParallax1", "CaveParallax2"],
        "工厂": ["IndoorDoodad", "HangingDoodads", "ScaffoldingBackground"],
        "村寨": ["TreeBushes", "OutdoorDoodad", "RefugeeProps", "Fence"],
        "要塞": ["WallOfGuns", "Agents", "ZoomAreas"],
    }
    for index, (title, y0, y1, mat, bgch) in enumerate(BANDS):
        floor_y = y0 + 4
        decor = decor_by_band[title]
        # 层入口检查点
        cx = 8 if index % 2 == 0 else W - 10
        on_floor(cx, floor_y, "CheckPoint")
        # 战斗组：房间内 3~4 组
        for room in range(4):
            base = 16 + room * 26
            for j, dx in enumerate((1, 4, 8, 11)):
                on_floor(base + dx, floor_y, "Mook", (index + j) % 3 * 2)
            on_floor(base + 6, floor_y, "AmmoCrate")
            on_floor(base + 3, floor_y + 9, "Mook", 0) if False else None
            # 房间上层平台：2 格高小台
            for dx in (4, 5, 6, 7):
                put(base + dx, floor_y + 5, "#" if title in ("洞窟", "村寨") else "S")
            on_floor(base + 5, floor_y + 5, "Mook", 4)
            # 装饰沿洞顶/墙面
            for dx in (2, 9, 13):
                if grid[floor_y + 8][base + dx] == ".":
                    doodads.append({"x": base + dx, "y": floor_y + 8,
                                    "type": decor[(base + dx) % len(decor)],
                                    "variation": (base + dx) % 4})
        # 囚笼
        cage_x = 20 + (index * 17) % 60
        base = 14 + ((cage_x - 14) // 26) * 26
        put(base + 9, floor_y + 1, ".")
        doodads.append({"x": base + 9, "y": floor_y + 2, "type": "PureEvil" if False else "Cage",
                        "variation": 0})
        # 补给箱
        on_floor(base + 12, floor_y, "Crate")
        on_floor(base + 13, floor_y, "AmmoCrate")

    # 层间竖井顶部放检查点与敌人
    for sx, y0s, y1s in shafts:
        place(sx + 1, y1s - 1, "Mook", 0)

    # 地狱/异形/要塞 Boss
    for index, kind in ((0, "HellBoss"), (1, "AlienBoss"), (5, "PureEvil")):
        y0 = BANDS[index][1]
        floor_y = y0 + 4
        bx = 100 if index % 2 == 0 else 18
        put(bx, floor_y + 1, ".")
        doodads.append({"x": bx, "y": floor_y + 2, "type": kind, "variation": 0})

    # 顶部撤离
    top_floor = BANDS[-1][1] + 4
    goal = {"x": W - 12, "y": top_floor + 1}

    rows = ["".join(grid[y]) for y in range(H - 1, -1, -1)]
    bg_rows = ["".join(bg[y]) for y in range(H - 1, -1, -1)]
    return {
        "schema": 1, "name": NAME, "width": W, "height": H, "description": NOTE,
        "legend": LEGEND, "tiles": rows, "background": bg_rows,
        "doodads": doodads, "goal": goal,
    }


def sanitize(project):
    rows, legend = project["tiles"], project["legend"]
    h, w = project["height"], project["width"]
    grounded = {"Mook", "SpawnPoint", "CheckPoint", "Crate", "AmmoCrate", "PureEvil"}

    def terrain(x, y):
        return legend[rows[h - 1 - y][x]]

    kept, dropped = [], 0
    for d in project["doodads"]:
        x, y = d["x"], d["y"]
        if not (0 <= x < w and 0 <= y < h):
            dropped += 1
            continue
        if d["type"] in grounded:
            if y < 1 or terrain(x, y - 1) not in author.SOLID or terrain(x, y) != "Empty":
                dropped += 1
                continue
        kept.append(d)
    project["doodads"] = kept
    return dropped


def main():
    project = build()
    dropped = sanitize(project)
    kinds = collections.Counter(d["type"] for d in project["doodads"])
    print("dropped:", dropped, "kept:", len(project["doodads"]), "kinds:", len(kinds))
    print(sorted(kinds))
    print("saved:", author.save_project(MAP_ID, project))
    result = author.export_map(MAP_ID, load=True)
    target = Path(r"F:\SteamLibrary\steamapps\common\Broforce\Levels") / (MAP_ID + ".bfc")
    shutil.copyfile(result["path"], target)
    print("export ->", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
