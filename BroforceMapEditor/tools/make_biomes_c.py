"""赤色远征 · 六生物群系：丛林 / 工厂 / 异形巢穴 / 地狱 / 村寨 / 要塞。

每群系手绘结构（非循环模板）：
  丛林(0-79)      阶梯峡谷 + 树冠 + 木桥
  工厂(80-159)    三层厂房 + 管道 + 竖井
  异形巢穴(160-239) 有机隧道：AlienEarth/AlienFlesh 洞壁、AlienEgg 卵群、
                    AlienLadder/ AlienBridge 通道、AlienTurrets/AlienSpawner、AlienBoss
  地狱(240-319)   下行阶梯地狱：HellEarth 地面、Skulls/Bones/Corpses 柱、
                    ForceFieldHell 屏障、HellTurrets、HellFog、HellBoss
  村寨(320-379)   木屋 + 茅草顶 + 屋顶跳跃 + 难民
  要塞(380-439)   斜坡要塞 + 枪墙 + 雕像 + PureEvil + 撤离
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

MAP_ID = "red_expedition_03"
W, H = 440, 96
NAME = "赤色远征 · 六群系"
NOTE = ("AI 原创地图：六生物群系远征。440×96，整图连续可游玩；六个手绘结构的群系："
        "丛林阶梯峡谷 → 工厂三层厂房 → 异形巢穴（洞壁/卵群/异形炮塔/异形 Boss）→ "
        "地狱下行阶梯（骨柱/力场屏障/地狱炮塔/地狱 Boss）→ 村寨屋顶 → 要塞斜坡+枪墙+撤离。"
        "每群系专属地形与装饰，异形与地狱场景首次真正启用。生成器 V4.0，2026-11-15。")

LEGEND = {
    ".": "Empty", "#": "Earth", "S": "Stone", "W": "Wood", "N": "Sand",
    "T": "Steel", "L": "Ladder", "B": "Bridge", "G": "Bridge2",
    "R": "CaveRock", "H": "ThatchRoof", "K": "Barrel", "P": "PropaneBarrel",
    "U": "Bunker", "I": "Pipe", "M": "FallingBlock", "Y": "Tyre",
    "O": "Roof", "A": "HutScaffolding", "E": "Beehive", "V": "WatchTower",
    "Q": "Statue",
    "a": "AlienEarth", "l": "AlienLadder", "g": "AlienBridge", "f": "AlienFlesh",
    "e": "AlienEgg", "b": "AlienBarbShooter", "t": "AlienEarthBehind",
    "x": "HellEarth", "z": "Skulls", "c": "Corpses", "d": "Bones",
    "j": "BackgroundEarth", "k": "BackgroundStone", "w": "WoodBackground",
    "y": "BackgroundFactory", "u": "BackgroundBunker",
}

ZONES = [
    (0, 79, "丛林", "N", "j"),
    (80, 159, "工厂", "T", "y"),
    (160, 239, "异形巢穴", "a", "k"),
    (240, 319, "地狱", "x", "k"),
    (320, 379, "村寨", "W", "w"),
    (380, 439, "要塞", "S", "u"),
]


def zone_at(x):
    for left, right, title, floor, bgch in ZONES:
        if left <= x <= right:
            return left, right, title, floor, bgch
    return 0, W - 1, "?", "S", "k"


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

    # ---------- 1. 丛林：三段阶梯峡谷 + 两处树冠平台 ----------
    for x in range(0, 80):
        phase = (x % 20)
        tops[x] = 6 + min(4, phase // 5)
    for x in range(1, 80):
        tops[x] = max(tops[x - 1] - 1, min(tops[x - 1] + 1, tops[x]))
    for x in range(0, 80):
        for y in range(tops[x] + 1):
            put(x, y, "N" if y < tops[x] - 1 else "#")
    fill(28, 34, 6, 6, "#")     # 悬空树冠平台
    fill(56, 62, 9, 9, "#")
    for col in (27, 35, 55, 63):
        for y in range(tops[col] + 1, 11):
            put(col, y, "L")

    # ---------- 2. 工厂：三层厂房 + 管道 + 两端竖井 ----------
    for x in range(80, 160):
        tops[x] = 8
    for row in (8, 15, 22, 29):
        fill(82, 157, row, row, "T")
    fill(82, 157, 26, 26, "I")
    fill(80, 80, 9, 30, "U")
    fill(159, 159, 9, 30, "U")
    for col in (84, 156):
        for y in range(9, 31):
            put(col, y, "L")
    for x in range(80, 160):
        for y in range(0, 8):
            put(x, y, "T" if y < 3 else "#")

    # ---------- 3. 异形巢穴：有机隧道 + 卵群 + 竖井 ----------
    for x in range(160, 240):
        # 有机起伏地面
        tops[x] = 7 + ((x * 3) % 5)
    for x in range(161, 240):
        tops[x] = max(tops[x - 1] - 1, min(tops[x - 1] + 1, tops[x]))
    for x in range(160, 240):
        for y in range(tops[x] + 1):
            put(x, y, "a" if y > 2 else "f")      # 地面异形土，深处异形肉壁
        # 异形洞顶（不规则的肉壁）
        ceil = tops[x] + 5 + (x % 4)
        for y in range(ceil, min(H, ceil + 2)):
            put(x, y, "f")
    # 卵群
    for x in range(166, 232, 11):
        put(x, tops[x], "e")
        put(x + 1, tops[x], "e")
    # 石笋状异形柱
    for x in range(172, 236, 15):
        for y in range(tops[x] + 1, tops[x] + 4):
            put(x, y, "f")
    # 竖井与有机桥
    for col in (190, 214):
        for y in range(tops[col] + 1, tops[col] + 9):
            put(col, y, "l")
        fill(col + 2, col + 8, tops[col] + 8, tops[col] + 8, "g")
    # 棘刺墙
    for x in range(198, 206):
        put(x, tops[x] + 4, "b")

    # ---------- 4. 地狱：下行阶梯 + 骨柱 + 屏障 ----------
    level = 26
    for x in range(240, 320):
        if x % 3 == 0 and level > 10:
            level -= 1
        tops[x] = level
    for x in range(241, 320):
        tops[x] = max(tops[x - 1] - 1, min(tops[x - 1] + 1, tops[x]))
    for x in range(240, 320):
        for y in range(tops[x] + 1):
            put(x, y, "x" if y < tops[x] - 1 else "d")
    # 骨堆/骷髅/尸体装饰地形
    for x in range(242, 318, 6):
        put(x, tops[x], "z" if x % 12 else "c")
    # 骨柱
    for x in range(250, 316, 18):
        for y in range(tops[x] + 1, tops[x] + 5):
            put(x, y, "d")
    # 地狱洞顶
    for x in range(240, 320):
        ceil = tops[x] + 8
        for y in range(ceil, min(H, ceil + 2)):
            put(x, y, "f")

    # ---------- 5. 村寨：木屋 + 屋顶跳跃 ----------
    for x in range(320, 380):
        tops[x] = 8 if (x // 10) % 2 == 0 else 10
    for x in range(321, 380):
        tops[x] = max(tops[x - 1] - 1, min(tops[x - 1] + 1, tops[x]))
    for x in range(320, 380):
        for y in range(tops[x] + 1):
            put(x, y, "W" if y < tops[x] - 1 else "#")
    for left, right in ((324, 334), (344, 354), (364, 374)):
        row = tops[left] + 5
        fill(left, right, row, row, "W")
        fill(left, right, row + 1, row + 1, "H")
        fill(left + 1, left + 1, tops[left] + 1, row - 1, "L")
    fill(338, 341, 13, 13, "W")
    fill(358, 361, 15, 15, "W")

    # ---------- 6. 要塞：斜坡 + 枪墙 + 门廊 ----------
    level = 10
    for x in range(380, 440):
        if x % 2 == 0 and level < 28:
            level += 1
        tops[x] = level
    for x in range(381, 440):
        tops[x] = max(tops[x - 1] - 1, min(tops[x - 1] + 1, tops[x]))
    for x in range(380, 440):
        for y in range(tops[x] + 1):
            put(x, y, "S" if y < tops[x] - 2 else "T")
    fill(416, 430, tops[420] + 7, tops[420] + 7, "T")
    fill(384, 434, tops[440 - 1] + 0, tops[440 - 1] + 0, "S")

    # 背景层
    for x in range(W):
        _, _, _, _, bgch = zone_at(x)
        top = min(H - 1, tops[x] + 12)
        for y in range(0, top + 1):
            put(x, y, bgch, bg)

    # 出生区
    fill(0, 8, 0, H - 1, ".")
    fill(0, 8, 0, 8, "#")
    for x in range(0, 9):
        tops[x] = 8

    # ---------- 物件 ----------
    doodads = []
    SOLID = set("#SWNTRHKUPIOAEVQXDZCGYalfegbtxzcd")
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

    on_ground(4, "SpawnPoint")
    for x in (76, 156, 236, 316, 376):
        on_ground(x, "CheckPoint")

    # 敌人：按群系战斗组
    enemy_groups = [
        (12, [0, 4, 0]), (24, [6, 0]), (40, [0, 0, 4]), (52, [4, 0]), (66, [0, 6, 0]),
        (88, [0, 4]), (102, [6, 0, 0]), (120, [0, 4]), (136, [0, 0, 6]), (150, [4, 0]),
        (166, [7, 0, 7]), (178, [0, 7]), (194, [7, 7, 0]), (208, [0, 7]), (224, [7, 0, 0]),
        (246, [9, 0]), (258, [0, 9, 0]), (274, [9, 0]), (292, [0, 9]), (308, [9, 9]),
        (326, [0, 4]), (338, [6, 0, 0]), (352, [0, 4]), (366, [0, 0, 6]),
        (386, [0, 4]), (400, [6, 0]), (414, [0, 4, 0]), (428, [6, 0]),
    ]
    for start, variations in enemy_groups:
        for offset, var in enumerate(variations):
            x = start + offset * 2
            if x < W - 2:
                on_ground(x, "Mook", var)

    # 群系专属敌人
    for x in (170, 186, 202, 218, 230):
        on_ground(x, "Alien", 0)
    for x in (248, 262, 280, 300, 312):
        on_ground(x, "HellEnemy", 0)

    # 丛林装饰
    for x in range(10, 78, 7):
        if x % 14 == 0:
            doodads.append({"x": x, "y": tops[x] + 2, "type": "Tree", "variation": (x // 7) % 3})
        doodads.append({"x": x + 1, "y": tops[x] + 1, "type": "TreeBushes", "variation": x % 2})
    for x in (30, 58):
        doodads.append({"x": x, "y": 10, "type": "TreeBushForeground", "variation": 0})
    # 工厂装饰
    for row in (15, 22, 29):
        for x in (86, 96, 116, 136, 152):
            doodads.append({"x": x, "y": row + 1, "type": "IndoorDoodad", "variation": x % 4})
        doodads.append({"x": 90, "y": row + 1, "type": "HangingDoodads", "variation": 0})
    doodads.append({"x": 110, "y": 9, "type": "Switch", "variation": 0})
    doodads.append({"x": 130, "y": 9, "type": "Alarm", "variation": 0})
    doodads.append({"x": 148, "y": 9, "type": "Door", "variation": 0})
    # 异形装饰
    for x in (164, 182, 200, 218, 234):
        doodads.append({"x": x, "y": tops[x] + 1, "type": "AlienFronds", "variation": x % 3})
        doodads.append({"x": x + 1, "y": tops[x] + 5, "type": "AlienBackgroundDecoration", "variation": 0})
    doodads.append({"x": 196, "y": tops[196] + 2, "type": "AlienTurrets", "variation": 0})
    doodads.append({"x": 220, "y": tops[220] + 2, "type": "AlienSpawner", "variation": 0})
    doodads.append({"x": 232, "y": tops[232] + 1, "type": "AlienCage", "variation": 0})
    # 地狱装饰
    for x in (244, 258, 272, 288, 304, 316):
        doodads.append({"x": x, "y": tops[x] + 1, "type": "HellDecoration", "variation": x % 3})
        doodads.append({"x": x + 2, "y": tops[x] + 6, "type": "HellFog", "variation": 0})
    doodads.append({"x": 268, "y": tops[268] + 2, "type": "HellTurrets", "variation": 0})
    doodads.append({"x": 300, "y": tops[300] + 2, "type": "ForceFieldHell", "variation": 0})
    # 村寨装饰
    for left, right in ((324, 334), (344, 354), (364, 374)):
        doodads.append({"x": left, "y": tops[left] + 1, "type": "OutdoorDoodad", "variation": 0})
        doodads.append({"x": right - 2, "y": tops[left] + 6, "type": "RefugeeProps", "variation": 0})
        doodads.append({"x": right - 4, "y": tops[left] + 6, "type": "Fence", "variation": 0})
    for x in (330, 350, 370):
        on_ground(x, "VillagerCaptured")
    # 要塞装饰
    for x in (390, 404, 424):
        doodads.append({"x": x, "y": tops[x] + 1, "type": "WallOfGuns", "variation": 0})
    doodads.append({"x": 400, "y": tops[400] + 1, "type": "Door", "variation": 0})
    doodads.append({"x": 410, "y": tops[410] + 1, "type": "Alarm", "variation": 0})

    # 补给
    for x in (18, 46, 70, 92, 126, 148, 174, 210, 228, 252, 286, 310,
              328, 360, 392, 418, 432):
        on_ground(x, "Crate")
    for x in (32, 60, 100, 140, 190, 218, 262, 296, 342, 386, 408, 430):
        on_ground(x, "AmmoCrate")
    for x in (54, 230, 352):
        on_ground(x, "Vehicle")

    # Boss：异形 Boss / 地狱 Boss / 要塞 PureEvil
    for x, kind in ((236, "AlienBoss"), (314, "HellBoss"), (434, "PureEvil")):
        put(x, tops[x] + 1, ".")
        doodads.append({"x": x, "y": tops[x] + 2, "type": kind, "variation": 0})

    goal = {"x": 435, "y": tops[435] + 1}
    rows = ["".join(grid[y]) for y in range(H - 1, -1, -1)]
    bg_rows = ["".join(bg[y]) for y in range(H - 1, -1, -1)]
    return {
        "schema": 1, "name": NAME, "width": W, "height": H, "description": NOTE,
        "legend": LEGEND, "tiles": rows, "background": bg_rows,
        "doodads": doodads, "goal": goal,
    }


def sanitize(project):
    solid = author.SOLID
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
    kinds = collections.Counter(d["type"] for d in project["doodads"])
    print("dropped:", dropped, "kept:", len(project["doodads"]), "doodad kinds:", len(kinds))
    print(sorted(kinds))
    print("saved:", author.save_project(MAP_ID, project))
    result = author.export_map(MAP_ID, load=True)
    target = Path(r"F:\SteamLibrary\steamapps\common\Broforce\Levels") / (MAP_ID + ".bfc")
    shutil.copyfile(result["path"], target)
    print("export ->", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
