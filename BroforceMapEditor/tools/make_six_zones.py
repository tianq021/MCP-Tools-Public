"""赤色峡谷 · 六区远征：素材最大化 + 结构去重复。

每区使用不同的地形材质与装饰素材组合：
A 洞穴(0-79)    CaveRock/背景洞穴视差/钟乳石/Fog/Skulls/Bones/Boulder/Trap
B 工厂(90-179)  Steel/Bunker/Pipe/BackgroundFactory/IndoorDoodad/Scaffolding/Switch/Alarm/Door
C 峡谷(190-259) Sand/Stone/FallingBlock/Bridge/Bridge2/Parallax/Boulder/Tyre/Zipline
D 村寨(270-339) Wood/ThatchRoof/HutScaffolding/Roof/Beehive/Tree*/OutdoorDoodad/Fence/RefugeeProps
E 墓地(350-399) HellEarth/Skulls/Bones/Corpses/Fog/HellFog/Statue/WatchTower
F 要塞(400-479) Steel/WatchTower/Statue/WallOfGuns/Door/Boss/撤离

结构：洞穴曲折低顶 → 工厂三层竖井 → 峡谷断桥高塔 → 村寨屋顶跳跃 →
      墓地平台墓穴 → 要塞阶梯登顶+双 Boss+撤离。敌人按战斗组，装饰按区铺。
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

MAP_ID = "red_canyon_04"
W, H = 480, 96
NAME = "赤色峡谷 · 六区远征"
NOTE = ("AI 原创大地图：赤色峡谷六区远征。480×96，整图连续可游玩、素材分区差异化："
        "洞穴（洞岩/钟乳石/洞穴视差/骷髅骨堆）→ 工厂（钢材/管道/室内道具/警报开关）→ "
        "峡谷（沙石/坠落块/木桥/视差/滑索）→ 村寨（木屋/茅草/树木/围栏/难民道具）→ "
        "墓地（地狱土/尸骨/雾气/雕像/瞭望塔）→ 要塞（钢材/枪墙/双 Boss/撤离）。"
        "敌人按战斗组布置，检查点只在区界。生成器 V3.0，2026-11-15。")

LEGEND = {
    ".": "Empty", "#": "Earth", "S": "Stone", "W": "Wood", "N": "Sand",
    "T": "Steel", "L": "Ladder", "B": "Bridge", "G": "Bridge2",
    "R": "CaveRock", "H": "ThatchRoof", "K": "Barrel", "P": "PropaneBarrel",
    "U": "Bunker", "I": "Pipe", "F": "FallingBlock", "Y": "Tyre",
    "O": "Roof", "A": "HutScaffolding", "E": "Beehive", "V": "WatchTower",
    "Q": "Statue", "X": "HellEarth", "Z": "Skulls", "C": "Corpses",
    "D": "Bones",
    "c": "BackgroundEarth", "b": "BackgroundStone", "t": "WoodBackground",
    "f": "BackgroundFactory", "k": "BackgroundBunker", "v": "BackgroundStoneDoodads",
}

ZONES = [
    (0, 79, "洞穴", "R", "b", ["CaveParallax1", "CaveParallax2", "CaveParallax3"]),
    (90, 179, "工厂", "T", "f", ["IndoorDoodad", "ScaffoldingBackground", "HangingDoodads",
                                "BackgroundWindowFactory", "VentDoodad", "Effects"]),
    (190, 259, "峡谷", "N", "c", ["Parallax1", "Parallax2", "Parallax3", "SetpieceBackground",
                                "DesertParallax"]),
    (270, 339, "村寨", "W", "t", ["Tree", "TreeBushes", "TreeBackground", "TreeBushForeground",
                                "OutdoorDoodad", "Fence", "RefugeeProps", "ShopProps"]),
    (350, 399, "墓地", "X", "v", ["Fog", "HellFog", "LightFog", "HellDecoration",
                                "ParallaxDecoration", "SetpieceBackground"]),
    (400, 479, "要塞", "S", "b", ["WallOfGuns", "IndoorDoodad", "Alarm", "Switch", "Door",
                                "ZoomAreas", "Agents"]),
]


def zone_at(x):
    for left, right, title, floor, bgch, decor in ZONES:
        if left <= x <= right:
            return left, right, title, floor, bgch, decor
    return 0, W - 1, "?", "S", "b", []


def build():
    grid = [["." for _ in range(W)] for _ in range(H)]
    bg = [["." for _ in range(W)] for _ in range(H)]
    tops = [8] * W

    def put(x, y, ch, layer=grid):
        if 0 <= x < W and 0 <= y < H:
            layer[y][x] = ch

    def fill(x0, x1, y0, y1, ch, layer=grid):
        for x in range(max(0, x0), min(W - 1, x1) + 1):
            for y in range(max(0, y0), min(H - 1, y1) + 1):
                put(x, y, ch, layer)

    # 各区地面轮廓
    for x in range(0, 80):      # 洞穴：曲折
        tops[x] = 6 if (x // 10) % 2 == 0 else 9
    for x in range(90, 180):    # 工厂：平整
        tops[x] = 8
    for x in range(190, 260):   # 峡谷：低地
        tops[x] = 6
    for x in range(270, 340):   # 村寨：缓起伏
        tops[x] = 8 if (x // 12) % 2 == 0 else 10
    level = 8
    for x in range(350, 400):   # 墓地：平台起伏
        tops[x] = 8 + (8 if (x // 8) % 2 else 0)
    for x in range(400, 480):   # 要塞：阶梯登山
        if x % 2 == 0 and level < 34:
            level += 1
        tops[x] = level
    for x in range(1, W):
        tops[x] = max(tops[x - 1] - 1, min(tops[x - 1] + 1, tops[x]))

    # 地面与背景
    for x in range(W):
        _, _, _, floor, bgch, _ = zone_at(x)
        top = tops[x]
        for y in range(0, top + 1):
            put(x, y, floor if y < top - 2 else "#")
        for y in range(0, min(H - 1, top + 16)):
            put(x, y, bgch, bg)

    # A 洞穴：低洞顶 + 钟乳石 + 骨堆装饰
    for x in range(0, 80):
        ceil = tops[x] + 7
        fill(x, x, ceil, ceil + 1, "R")
        if x % 19 == 5:
            fill(x, x, tops[x] + 1, tops[x] + 3, "R")
    for x in range(8, 78, 22):
        fill(x, x + 2, tops[x], tops[x], "D")   # 骨堆
    for x in range(16, 78, 27):
        fill(x, x, tops[x], tops[x], "Z")       # 骷髅

    # B 工厂：三层室内 + 管道 + 竖井
    floors_b = [tops[90] + 7, tops[90] + 14, tops[90] + 21]
    for row in floors_b:
        fill(92, 177, row, row, "T")
        fill(94, 175, row + 1, row + 3, "f", bg)
        if row % 2 == 0:
            fill(100, 170, row, row, "I")       # 管道段
    fill(90, 90, tops[90] + 1, floors_b[-1] + 3, "U")
    fill(179, 179, tops[179] + 1, floors_b[-1] + 3, "U")
    for col in (94, 176):
        for y in range(tops[col] + 1, floors_b[-1] + 4):
            put(col, y, "L")

    # C 峡谷：两个断口 + 桥 + 坠落块区 + 高塔
    for gap in (206, 232):
        fill(gap, gap + 7, 0, tops[gap] + 1, ".")
        fill(gap, gap + 7, 8, 8, "B")
        fill(gap, gap + 7, 9, 9, "G")
    for x in range(196, 254, 3):
        if (x // 3) % 4 == 0:
            put(x, tops[x], "F")                # 坠落块
    for tower in (202, 250):
        fill(tower, tower + 2, 0, 19, "S")

    # D 村寨：木屋 + 茅草顶 + 围栏 + 蜂巢
    for left, right in ((276, 288), (296, 308), (316, 328)):
        row = tops[left] + 6
        fill(left, right, row, row, "W")
        fill(left, right, row + 1, row + 1, "H")
        fill(left + 1, left + 1, tops[left] + 1, row - 1, "L")
        fill(right - 1, right - 1, row + 2, row + 2, "E")
    for x in range(272, 338, 9):
        fill(x, x, tops[x] + 1, tops[x] + 2, "A")   # 脚手架
        fill(x + 3, x + 3, tops[x] + 1, tops[x] + 1, "O")  # 屋顶碎块

    # E 墓地：墓碑雕像 + 骨堆 + 瞭望塔
    for x in range(352, 398, 7):
        put(x, tops[x], "Q")
        put(x + 2, tops[x], "C")
    for tower in (356, 392):
        fill(tower, tower + 1, 0, 16, "V")

    # F 要塞：阶梯墙 + 枪墙底座 + 顶部门廊
    for x in range(400, 480):
        fill(x, x, 0, tops[x], "S")
    fill(456, 470, tops[456], tops[456], "T")
    fill(456, 470, tops[456] + 7, tops[456] + 7, "V")

    # 出生区削平
    fill(0, 8, 0, H - 1, ".")
    fill(0, 8, 0, 8, "#")
    for x in range(0, 9):
        tops[x] = 8

    # ---- 物件 ----
    doodads = []
    SOLID = set("#SWNTRHKUPIOAEVQXDZCGY")
    used = set()

    def place(x, y, kind, variation=0, need_solid_under=True):
        if not (0 <= x < W and 0 <= y < H) or (x, y) in used:
            return False
        if need_solid_under:
            if y < 1 or grid[y - 1][x] not in SOLID or grid[y][x] != ".":
                return False
        used.add((x, y))
        doodads.append({"x": x, "y": y, "type": kind, "variation": variation})
        return True

    def on_ground(x, kind, variation=0):
        top = tops[x]
        put(x, top + 1, ".")
        return place(x, top + 1, kind, variation)

    def decor(x, y, kinds):
        if not kinds:
            return
        kind = kinds[(x * 7 + y) % len(kinds)]
        if grid[y][x] == ".":
            doodads.append({"x": x, "y": y, "type": kind, "variation": (x + y) % 4})

    on_ground(4, "SpawnPoint")
    for x in (84, 184, 264, 344, 404):
        on_ground(x, "CheckPoint")

    # 战斗组：每区 8~14 个敌人，按组而不是等距
    groups = [
        (12, [0, 0, 4]), (26, [0, 6, 0]), (40, [4, 0, 0]), (54, [0, 0, 6]), (66, [0, 4]),
        (98, [0, 0, 4]), (110, [6, 0]), (124, [0, 4, 0]), (140, [0, 0]), (156, [4, 6]),
        (196, [0, 0, 4]), (214, [6, 0]), (226, [0, 4]), (244, [0, 0, 6]),
        (278, [0, 4, 0]), (296, [6, 0]), (314, [0, 0, 4]), (330, [4, 0]),
        (354, [0, 6, 0]), (372, [4, 0]), (386, [0, 0, 4]),
        (410, [0, 4, 6]), (430, [0, 0, 4]), (450, [6, 0, 4]), (466, [0, 0]),
    ]
    for start, variations in groups:
        for offset, var in enumerate(variations):
            x = start + offset * 2
            if x < W:
                on_ground(x, "Mook", var)

    # 玩家看到的"其他素材"：分区装饰铺满
    for x in range(10, W - 6, 6):
        _, _, title, _, _, decor_list = zone_at(x)
        top = tops[x]
        decor(x, top + 1 + (x % 3), decor_list)          # 地面装饰
        if x % 12 == 0:
            decor(x, top + 7, decor_list)                # 高空装饰
    # 特定标志物
    for x in (120, 160): on_ground(x, "IndoorDoodad")
    on_ground(150, "Switch")
    on_ground(170, "Alarm")
    on_ground(210, "Zipline")
    on_ground(238, "Zipline")
    on_ground(302, "VillagerCaptured")
    on_ground(310, "VillagerCaptured")
    on_ground(322, "Cage")
    on_ground(360, "Fog")
    on_ground(384, "HellFog")
    on_ground(446, "WallOfGuns")
    on_ground(452, "WallOfGuns")

    # 补给与载具
    for x in (20, 48, 74, 104, 132, 168, 200, 240, 288, 330, 358, 396, 424, 460):
        on_ground(x, "Crate")
    for x in (34, 88, 146, 218, 282, 348, 408, 440):
        on_ground(x, "AmmoCrate")
    for x in (56, 250, 326):
        on_ground(x, "Vehicle")
    # 爆炸桶（地形）
    for x in (30, 108, 236, 292, 366, 434):
        put(x, tops[x], "K")
    for x in (44, 128, 320, 412):
        put(x, tops[x], "P")

    # 双 Boss + 撤离
    top_row = tops[470]
    for x in (458, 472):
        put(x, top_row + 1, ".")
        doodads.append({"x": x, "y": top_row + 2, "type": "PureEvil", "variation": 0})

    goal = {"x": 476, "y": tops[476] + 1}
    rows = ["".join(grid[y]) for y in range(H - 1, -1, -1)]
    bg_rows = ["".join(bg[y]) for y in range(H - 1, -1, -1)]
    return {
        "schema": 1, "name": NAME, "width": W, "height": H, "description": NOTE,
        "legend": LEGEND, "tiles": rows, "background": bg_rows,
        "doodads": doodads, "goal": goal,
    }


def sanitize(project):
    solid = {"Earth", "Stone", "Wood", "Sand"}
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
            if y < 1 or terrain(x, y - 1) not in solid or terrain(x, y) != "Empty":
                dropped += 1
                continue
        kept.append(d)
    project["doodads"] = kept
    return dropped


def main():
    project = build()
    dropped = sanitize(project)
    used_types = collections.Counter(d["type"] for d in project["doodads"])
    tile_chars = collections.Counter("".join(project["tiles"]))
    print("dropped:", dropped, "kept:", len(project["doodads"]))
    print("doodad 种类:", len(used_types), sorted(used_types))
    print("terrain 种类:", len([c for c in tile_chars if c in LEGEND and LEGEND[c] != "Empty"]))
    print("saved:", author.save_project(MAP_ID, project))
    result = author.export_map(MAP_ID, load=True)
    target = Path(r"F:\SteamLibrary\steamapps\common\Broforce\Levels") / (MAP_ID + ".bfc")
    shutil.copyfile(result["path"], target)
    print("export:", result["path"], "->", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
