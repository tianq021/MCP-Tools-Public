"""赤色峡谷 · 大远征：300×80 连续可游玩区域，四区地貌 + 支线高台 + 完整终点撤离。

坐标约定：tiles 行从上到下；doodad 的 y 从底部向上。地表物件放在「地面上方一格」。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import map_authoring as author  # noqa: E402

MAP_ID = "red_canyon_02"
W, H = 300, 80
NAME = "赤色峡谷 · 大远征 B"
NOTE = ("AI 原创大地图：赤色峡谷大远征。300×80，整张图连续可游玩（无空白天际）；"
        "四区地貌：红岩峡谷 → 石造要塞 → 木寨村落 → 钢铁堡垒；每区带高台支线与梯子；"
        "约 200 个元素：60+ 敌人、9 检查点、9 囚笼、40+ 补给箱、载具、滑索、爆炸桶；"
        "最右侧为撤离区，进入即过关。生成器 V1.0，2026-11-15。")

LEGEND = {
    ".": "Empty", "#": "Earth", "S": "Stone", "W": "Wood", "N": "Sand",
    "T": "Steel", "L": "Ladder", "B": "Bridge", "R": "CaveRock",
    "c": "BackgroundEarth", "b": "BackgroundStone", "t": "WoodBackground",
    "f": "BackgroundFactory", "K": "Barrel", "P": "PropaneBarrel",
}

# 四区：起始列、地面材质、上层材质、背景材质
ZONES = [
    (0, 74, "N", "#", "c"),    # 红岩峡谷（沙地）
    (75, 149, "S", "#", "b"),  # 石造要塞
    (150, 224, "W", "#", "t"), # 木寨村落
    (225, 299, "S", "#", "f"), # 钢铁堡垒
]


def zone_of(x: int):
    for left, right, floor, upper, bg in ZONES:
        if left <= x <= right:
            return floor, upper, bg
    return "S", "#", "b"


def main_tops() -> list[int]:
    """主路线地面高度：平缓起伏，相邻列最多差 1 行，保证可步行通过。"""
    tops = [8] * W
    # 几段台地与谷地（平滑过渡）
    features = [
        (20, 34, 12), (40, 52, 6), (78, 96, 13), (104, 118, 7),
        (150, 168, 11), (178, 192, 6), (228, 248, 14), (262, 280, 8),
    ]
    for left, right, peak in features:
        span = max(1, (right - left) // 2)
        for x in range(left, right + 1):
            level = peak - min(peak - 6, abs(x - (left + span)) // 2)
            tops[x] = level
    for x in range(1, W):
        tops[x] = max(tops[x - 1] - 1, min(tops[x - 1] + 1, tops[x]))
    return tops


def build():
    tops = main_tops()
    grid = [["." for _ in range(W)] for _ in range(H)]
    bg = [["." for _ in range(W)] for _ in range(H)]

    def put(x: int, y: int, ch: str, layer=grid):
        if 0 <= x < W and 0 <= y < H:
            layer[y][x] = ch

    # 地基 + 层级材质 + 背景
    for x in range(W):
        floor, upper, bgch = zone_of(x)
        top = tops[x]
        for y in range(0, top + 1):
            put(x, y, upper if y > top - 3 else floor)
        for y in range(0, min(H - 1, top + 10)):
            put(x, y, bgch, bg)
    # 出生区：从第 0 列开始削平并清空上方（避免从地图左缘出场时卡进地形）
    spawn_top = 8
    for x in range(0, 10):
        for y in range(0, H):
            put(x, y, ".")
        for y in range(0, spawn_top + 1):
            put(x, y, "#")
    for y in range(spawn_top + 1, min(H, spawn_top + 10)):
        for x in range(0, 10):
            put(x, y, ".")

    # 两处峡谷断口，用木桥连接（桥下是深坑，桥面可通行）
    for left, right in ((122, 126), (236, 240)):
        for x in range(left, right + 1):
            for y in range(0, tops[x] + 1):
                put(x, y, ".")
            put(x, tops[x] + 1, "B")

    shelves = []
    for left, right, row in (
        (26, 44, 16), (58, 72, 14), (86, 104, 18), (112, 130, 16),
        (156, 176, 17), (186, 204, 14), (232, 252, 18), (262, 282, 15),
    ):
        material = zone_of((left + right) // 2)[1]
        for x in range(left, right + 1):
            put(x, row, material)
            put(x, rows_bg := row, zone_of(x)[2], bg)
        # 两端梯子接到地面
        for x in (left - 1, right + 1):
            top = tops[x]
            for y in range(top + 1, row + 2):
                put(x, y, "L")
        shelves.append((left, right, row))

    rows = ["".join(grid[y]) for y in range(H - 1, -1, -1)]
    bg_rows = ["".join(bg[y]) for y in range(H - 1, -1, -1)]

    doodads = []

    def clear_above(x: int, y: int, height: int = 1):
        for r in range(y, min(H, y + height)):
            put(x, r, ".")

    SOLID_CHARS = set("#SWN")

    def ground(x: int, kind: str, variation: int = 0, support_row: int | None = None):
        top = tops[x] if support_row is None else support_row
        y = top + 1
        if not (0 <= y < H):
            return
        # 必须站在实心地形正上方、且上方一格为空（断口/桥面不放物件）
        if grid[top][x] not in SOLID_CHARS or grid[y][x] != ".":
            return
        doodads.append({"x": x, "y": y, "type": kind, "variation": variation})

    # 出生点：削平区顶行 +2 行，避开贴图中心锚点导致的半格埋入
    doodads.append({"x": 4, "y": 9, "type": "SpawnPoint", "variation": 0})
    # 检查点沿主路线
    for x in (20, 55, 90, 125, 160, 195, 230, 262, 292):
        ground(x, "CheckPoint")
    # 敌人：地面 40 + 高台 20
    ground_enemies = [10, 16, 24, 32, 40, 48, 60, 68, 78, 88, 96, 108, 118,
                      128, 136, 146, 156, 166, 174, 184, 192, 202, 210, 216,
                      224, 232, 244, 252, 258, 268, 276, 284, 288]
    for index, x in enumerate(ground_enemies):
        ground(x, "Mook", 6 if index % 7 == 0 else (4 if index % 3 == 0 else 0))
    for index, (left, right, row) in enumerate(shelves):
        for offset in (4, (right - left) // 2, right - left - 4):
            x = left + offset
            grid[row + 1][x] = "."
            doodads.append({"x": x, "y": row + 1, "type": "Mook",
                            "variation": 4 if index % 2 else 0})
        # 高台补给
        x = left + 6
        grid[row + 1][x] = "."
        doodads.append({"x": x, "y": row + 1, "type": "AmmoCrate", "variation": 0})
        x = right - 6
        grid[row + 1][x] = "."
        doodads.append({"x": x, "y": row + 1, "type": "Crate", "variation": 0})

    # 补给与救援
    for x in (14, 36, 52, 74, 100, 116, 140, 152, 170, 190, 206, 220,
              242, 256, 274, 286):
        ground(x, "Crate")
    for x in (28, 62, 84, 110, 148, 178, 212, 246, 280):
        ground(x, "AmmoCrate")
    for x in (46, 106, 164, 200, 238, 296):
        ground(x, "VillagerCaptured")
    # 囚笼：需要 3 列支撑与上方净空
    for x in (34, 72, 132, 172, 214, 254):
        base = max(tops[x:x + 3])
        for c in range(x, x + 3):
            for y in range(tops[c] + 1, base + 3):
                put(c, y, ".")
            put(c, base, zone_of(c)[0])
            put(c, base + 1, ".")
        doodads.append({"x": x, "y": base + 2, "type": "Cage", "variation": 0})
    # 载具与滑索
    for x in (66, 188, 290):
        ground(x, "Vehicle")
    for x, row in ((40, 16), (100, 18), (170, 17), (246, 18)):
        doodads.append({"x": x, "y": row + 2, "type": "Zipline", "variation": 0})
    # 爆炸桶（地形块，放在地表凹位）
    for x in (22, 58, 92, 138, 158, 204, 234, 268):
        put(x, tops[x], "K")
    for x in (26, 96, 162, 240):
        put(x, tops[x], "P")

    goal_x = W - 8
    goal = {"x": goal_x, "y": tops[goal_x] + 1}

    project = {
        "schema": 1,
        "name": NAME,
        "width": W,
        "height": H,
        "description": NOTE,
        "legend": LEGEND,
        "tiles": rows,
        "background": bg_rows,
        "doodads": doodads,
        "goal": goal,
    }
    return project


def sanitize(project):
    """剔除不满足“实心支撑 + 上方净空”的落点，返回被剔除数量。"""
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


def main() -> int:
    project = build()
    dropped = sanitize(project)
    print("sanitized: dropped", dropped, "doodads; kept", len(project["doodads"]))
    saved = author.save_project(MAP_ID, project)
    print("saved:", saved)
    result = author.export_map(MAP_ID, load=True)
    print("export path:", result["path"])
    print("receipts:", json.dumps(result["receipts"][-3:], ensure_ascii=False))
    # 同时放进游戏 Levels，方便在「自定义关卡」里直接看到
    target = Path(r"F:\SteamLibrary\steamapps\common\Broforce\Levels") / (MAP_ID + ".bfc")
    shutil.copyfile(result["path"], target)
    print("copied to game Levels:", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
