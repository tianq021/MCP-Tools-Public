"""赤色峡谷 · 五区远征：320×88，五个结构互不重复的区段。

区段设计（避免同一模板重复）：
A 洞穴登陆(0-59)：低矮洞顶 + 曲折通道，紧凑遭遇
B 废弃工厂(60-129)：三层室内楼层 + 竖井梯，跨层火力
C 峡谷断桥(130-189)：开阔谷地 + 断口木桥 + 高塔狙击 + 滑索
D 村寨(190-249)：木屋 + 茅草屋顶 + 屋顶跳跃路线
E 要塞登山(250-319)：每 2 列升 1 行的阶梯登山，顶部 Boss 与撤离区
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import map_authoring as author  # noqa: E402

MAP_ID = "red_canyon_03"
W, H = 320, 88
NAME = "赤色峡谷 · 五区远征"
NOTE = ("AI 原创地图：赤色峡谷五区远征。320×88，整图连续可游玩；五个结构不同的区段："
        "洞穴登陆（低洞顶曲折通道）→ 废弃工厂（三层室内+竖井）→ 峡谷断桥（断口桥+高塔+滑索）"
        "→ 村寨（木屋屋顶跳跃）→ 要塞登山（阶梯登顶+Boss+撤离）。"
        "敌人按战斗组布置，补给多藏支线；检查点只在区界。生成器 V2.0，2026-11-15。")

LEGEND = {
    ".": "Empty", "#": "Earth", "S": "Stone", "W": "Wood", "N": "Sand",
    "T": "Steel", "L": "Ladder", "B": "Bridge", "R": "CaveRock",
    "H": "ThatchRoof", "K": "Barrel", "P": "PropaneBarrel",
    "c": "BackgroundEarth", "b": "BackgroundStone", "t": "WoodBackground",
    "f": "BackgroundFactory",
}

ZONES = [
    (0, 59, "洞穴登陆", "N", "c"),
    (60, 129, "废弃工厂", "S", "f"),
    (130, 189, "峡谷断桥", "N", "c"),
    (190, 249, "村寨", "W", "t"),
    (250, 319, "要塞登山", "S", "b"),
]


def zone_at(x):
    for left, right, title, floor, bg in ZONES:
        if left <= x <= right:
            return left, right, title, floor, bg
    return 0, W - 1, "?", "S", "b"


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

    # ---- 各区地面高度规划（相邻列最多差 1，保证可走）----
    # A 洞穴：曲折 6/9 两档
    for x in range(0, 60):
        tops[x] = 6 if (x // 12) % 2 == 0 else 9
    # B 工厂：平整 8
    for x in range(60, 130):
        tops[x] = 8
    # C 峡谷：低地 6，边缘抬高
    for x in range(130, 190):
        tops[x] = 6
    # D 村寨：起伏 8/11
    for x in range(190, 250):
        tops[x] = 8 if (x // 10) % 2 == 0 else 11
    # E 要塞：从 8 起每 2 列升 1 行，直到 30
    level = 8
    for x in range(250, 320):
        if x % 2 == 0 and level < 30:
            level += 1
        tops[x] = level
    # 平滑相邻差
    for x in range(1, W):
        if tops[x] - tops[x - 1] > 1:
            tops[x] = tops[x - 1] + 1
        if tops[x - 1] - tops[x] > 1:
            tops[x] = tops[x - 1] - 1

    # ---- 地面与背景 ----
    for x in range(W):
        _, _, _, floor, bgch = zone_at(x)
        top = tops[x]
        for y in range(0, top + 1):
            put(x, y, floor if y < top - 2 else "#")
        for y in range(0, min(H - 1, top + 14)):
            put(x, y, bgch, bg)

    # A 洞穴：低洞顶（顶行随通道变化），局部钟乳石柱
    for x in range(0, 60):
        ceiling = tops[x] + 7
        for y in range(ceiling, min(H, ceiling + 2)):
            put(x, y, "R")
        if x % 17 == 3:
            for y in range(tops[x] + 1, tops[x] + 4):
                put(x, y, "R")

    # B 工厂：三层室内地板 + 两端竖井梯 + 屋顶
    floors_b = [tops[60] + 7, tops[60] + 14]
    for row in floors_b:
        fill(62, 127, row, row, "S")
        fill(64, 125, row + 1, row + 2, "f", bg)
    fill(60, 129, floors_b[-1] + 8, floors_b[-1] + 8, "S")
    for col in (62, 126):
        for y in range(tops[col] + 1, floors_b[-1] + 9):
            put(col, y, "L")
    # 工厂侧壁（室内感）
    fill(60, 60, tops[60] + 1, floors_b[-1] + 8, "S")
    fill(129, 129, tops[129] + 1, floors_b[-1] + 8, "S")

    # C 峡谷：两个 8 列断口 + 桥面，另有两座 6 列高塔
    for gap_left in (142, 166):
        fill(gap_left, gap_left + 7, 0, tops[gap_left] + 1, ".")
        fill(gap_left, gap_left + 7, 8, 8, "B")
        fill(gap_left - 2, gap_left + 9, 14, 14, "S")  # 上方悬空平台，可作为高路线
    for tower in (138, 186):
        fill(tower, tower + 2, 0, 20, "S")

    # D 村寨：木屋（平台+茅草顶），留出屋顶跳跃线
    huts = [(196, 206), (214, 224), (232, 242)]
    for left, right in huts:
        row = tops[left] + 6
        fill(left, right, row, row, "W")
        fill(left, right, row + 1, row + 1, "H")
        fill(left + 1, left + 1, tops[left] + 1, row - 1, "L")
    # 屋顶上方连跳平台
    for left, right in ((210, 213), (228, 231), (246, 248)):
        fill(left, right, tops[left] + 11, tops[left] + 11, "W")

    # E 要塞：阶梯墙 + 顶部门廊
    for x in range(250, 320):
        fill(x, x, 0, tops[x], "S")
    fill(300, 318, tops[310], tops[310], "S")
    fill(300, 318, tops[310] + 6, tops[310] + 6, "T")

    # ---- 出生区：削平并清空，避免左缘出场卡地形 ----
    fill(0, 8, 0, H - 1, ".")
    fill(0, 8, 0, 8, "#")
    for x in range(0, 9):
        tops[x] = 8

    # ---- 物件 ----
    doodads = []
    SOLID = {"#", "S", "W", "N", "T", "R", "H"}
    used = set()

    def clear_cells(x, y, h=1):
        for yy in range(y, y + h):
            put(x, yy, ".")

    def place(x, y, kind, variation=0, need_solid_under=True):
        if not (0 <= x < W and 0 <= y < H):
            return False
        if (x, y) in used:
            return False
        if need_solid_under:
            if y < 1 or grid[y - 1][x] not in SOLID or grid[y][x] != ".":
                return False
        used.add((x, y))
        doodads.append({"x": x, "y": y, "type": kind, "variation": variation})
        return True

    def place_on_ground(x, kind, variation=0):
        top = tops[x]
        clear_cells(x, top + 1)
        return place(x, top + 1, kind, variation)

    # 出生点
    place_on_ground(4, "SpawnPoint")
    # 检查点：只放区界
    for x in (56, 124, 186, 246, 296):
        place_on_ground(x, "CheckPoint")

    # A 洞穴：紧贴洞顶的伏击组 + 通道内掩体
    for x, var in ((14, 0), (18, 4), (24, 0), (30, 6), (36, 0), (42, 4), (48, 6), (53, 0)):
        place_on_ground(x, "Mook", var)
    for x in (20, 34, 50):
        place_on_ground(x, "Crate")
    place_on_ground(27, "AmmoCrate")
    for x in (16, 44):
        put(x, tops[x], "K")

    # B 工厂：楼层间交叉火力
    for row in floors_b:
        for offset, var in ((4, 0), (12, 4), (22, 6), (32, 0), (42, 4)):
            x = 62 + offset
            if grid[row + 1][x] == "." and grid[row][x] in SOLID:
                place(x, row + 1, "Mook", var)
        place(62 + 8, row + 1, "AmmoCrate")
        place(62 + 30, row + 1, "Crate")
    for x in (70, 96, 118):
        place_on_ground(x, "Mook", 6)
    for x in (66, 88, 112):
        put(x, tops[x], "P")
    place(66, floors_b[-1] + 9, "Zipline", 0)

    # C 峡谷：塔上狙击手 + 断口两侧守卫 + 滑索
    for tower in (138, 186):
        place(tower + 1, 21, "Mook", 6)
        place(tower + 1, 12, "AmmoCrate")
    for x in (134, 140, 152, 160, 172, 180):
        place_on_ground(x, "Mook", 4 if x % 3 else 0)
    for x in (148, 176):
        place_on_ground(x, "Crate")
    doodads.append({"x": 150, "y": 15, "type": "Zipline", "variation": 0})
    doodads.append({"x": 174, "y": 15, "type": "Zipline", "variation": 0})

    # D 村寨：屋顶跳跃线上的守卫 + 屋内人质/囚笼
    for left, right in huts:
        place(left + 3, tops[left] + 7, "Mook", 4)   # 屋顶
        place(left + 7, tops[left] + 7, "Mook", 0)
        place(left + 2, tops[left] + 6 - 1, "VillagerCaptured", 0, need_solid_under=False)
        doodads.append({"x": left + 5, "y": tops[left] + 7, "type": "Cage", "variation": 0})
    for x in (200, 218, 236, 246):
        place_on_ground(x, "Mook", 6 if x % 2 else 0)
    for x in (204, 222, 240):
        place_on_ground(x, "AmmoCrate")
    place_on_ground(212, "Vehicle")

    # E 要塞：阶梯上的排枪守卫，顶部 Boss 与撤离
    for index, x in enumerate(range(252, 300, 6)):
        place_on_ground(x, "Mook", 6 if index % 3 == 0 else (4 if index % 2 else 0))
    for x in (262, 282):
        place_on_ground(x, "AmmoCrate")
    for x in (270, 290):
        place_on_ground(x, "Crate")
    top_row = tops[300]
    for x in (304, 308, 312):
        clear_cells(x, top_row + 1)
        place(x, top_row + 1, "Mook", 6)
    clear_cells(310, top_row + 1, 2)
    doodads.append({"x": 310, "y": top_row + 2, "type": "PureEvil", "variation": 0})

    goal_x = W - 6
    goal = {"x": goal_x, "y": tops[goal_x] + 1}

    rows = ["".join(grid[y]) for y in range(H - 1, -1, -1)]
    bg_rows = ["".join(bg[y]) for y in range(H - 1, -1, -1)]
    return {
        "schema": 1, "name": NAME, "width": W, "height": H,
        "description": NOTE, "legend": LEGEND,
        "tiles": rows, "background": bg_rows, "doodads": doodads, "goal": goal,
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
    import collections
    counts = collections.Counter(d["type"] for d in project["doodads"])
    print("dropped:", dropped, "kept:", len(project["doodads"]), dict(counts))
    print("saved:", author.save_project(MAP_ID, project))
    result = author.export_map(MAP_ID, load=True)
    print("export:", result["path"])
    target = Path(r"F:\SteamLibrary\steamapps\common\Broforce\Levels") / (MAP_ID + ".bfc")
    shutil.copyfile(result["path"], target)
    print("copied:", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
