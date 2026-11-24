"""Build the playable campaign and a small, separately loaded mechanics test map."""

from map_authoring import save_project


LEGEND = {
    ".": "Empty", "e": "Earth", "s": "Stone", "w": "Wood", "b": "Bridge",
    "r": "ThatchRoof", "c": "CaveRock", "d": "CaveEarth", "m": "Metal",
    "p": "MetalBridge", "j": "CityBrick", "o": "CityRoad", "u": "OilPipe",
    "f": "FallingBlock", "B": "Barrel", "P": "PropaneBarrel", "O": "OilTank",
    "a": "AlienEarth", "A": "AlienFlesh", "E": "AlienEggExplosive",
    "i": "AlienLadder", "h": "HellRock", "H": "HellEarth", "k": "Skulls",
    "q": "Quicksand", "g": "SacredTemple", "G": "SacredTempleGold",
    "l": "Ladder", "L": "MetalLadder", "t": "WoodBackground",
    "x": "BackgroundStone", "z": "BackgroundFactory", "n": "AlienEarthBehind",
    "v": "BackgroundVault", "y": "BackgroundBunker", "+": "DesertBackgroundBrick",
}


class Layout:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.tiles = [["."] * width for _ in range(height)]
        self.background = [["."] * width for _ in range(height)]
        self.floor = [0] * width
        self.doodads = []
        self.triggers = []

    def rect(self, x1, y1, x2, y2, material, background=False):
        assert 0 <= x1 <= x2 < self.width and 0 <= y1 <= y2 < self.height
        grid = self.background if background else self.tiles
        row = [material] * (x2 - x1 + 1)
        for y in range(y1, y2 + 1):
            grid[y][x1:x2 + 1] = row

    def ground(self, x1, x2, start, end, material, surface=None):
        for x in range(x1, x2 + 1):
            y = round(start + (end - start) * (x - x1) / max(1, x2 - x1))
            self.rect(x, 0, x, y, material)
            self.tiles[y][x] = surface or material
            self.floor[x] = y

    def place(self, x, kind, variation=0, y=None):
        y = self.floor[x] + 1 if y is None else y
        assert self.tiles[y][x] == ".", (kind, x, y, self.tiles[y][x])
        self.doodads.append(dict(x=x, y=y, type=kind, variation=variation))

    def troops(self, kind, positions):
        for x, variant in positions:
            self.place(x, kind, variant)

    def collapse(self, bounds, target, trigger_type=0):
        x1, y1, x2, y2 = bounds
        self.triggers.append(dict(type=trigger_type, blx=x1, bly=y1, urx=x2,
                                  ury=y2, action="Collapse", target_x=target[0],
                                  target_y=target[1]))

    def project(self, name, description, goal, use_background=True):
        def rows(grid):
            return ["".join(row) for row in reversed(grid)]

        project = dict(schema=1, name=name, description=description, width=self.width,
                       height=self.height, legend=LEGEND, tiles=rows(self.tiles),
                       doodads=self.doodads, triggers=self.triggers,
                       goal=dict(x=goal[0], y=goal[1]))
        if use_background:
            project["background"] = rows(self.background)
        return project


def mechanics_probe():
    m = Layout(112, 28)
    m.ground(0, 111, 3, 3, "s")
    m.place(6, "SpawnPoint")
    m.rect(21, 11, 21, 11, "f")
    m.rect(20, 3, 22, 3, "d")
    m.collapse((12, 4, 16, 8), (21, 11))
    m.rect(40, 4, 40, 4, "P")
    m.rect(42, 4, 42, 4, "B")
    m.rect(44, 4, 44, 4, "E")
    m.rect(43, 3, 46, 3, "e")
    m.collapse((30, 4, 34, 8), (40, 4))
    m.place(58, "HellEnemy", 2)
    m.place(69, "Cage")
    m.place(71, "AmmoCrate")
    m.place(74, "CheckPoint")
    m.place(92, "PureEvil")
    m.rect(103, 4, 103, 4, "e")
    m.collapse((86, 4, 101, 10), (103, 4), trigger_type=9)
    return m.project("机制测试 · 裂谷回声", "坠落块、连爆、装死亡灵、检查点和区域清敌探针。",
                     (107, 4), use_background=False)


def echo_rift():
    m = Layout(456, 72)
    # 河岸村落，留出直升机落点和一条宽路。高处是哨楼和可选救援路径。
    m.ground(0, 22, 5, 5, "e")
    m.ground(23, 64, 5, 9, "e")
    m.rect(28, 8, 44, 14, "t", background=True)
    m.rect(44, 10, 55, 19, "t", background=True)
    m.rect(28, 13, 43, 13, "w")
    m.rect(27, 16, 42, 16, "r")
    m.rect(44, 17, 56, 17, "w")
    m.rect(49, 21, 57, 21, "r")
    m.rect(29, 7, 29, 13, "l")
    m.rect(41, 14, 41, 17, "l")
    m.rect(42, 17, 43, 17, "b")
    m.rect(55, 10, 55, 17, "l")
    m.place(6, "SpawnPoint")
    m.place(18, "CheckPoint")
    m.place(11, "Tree")
    m.place(22, "TreeBushes")
    m.place(34, "Cage", y=14)
    m.place(51, "AmmoCrate", y=18)
    m.place(48, "Mook", 4, y=18)
    m.troops("Mook", [(25, 0), (45, 0), (58, 2), (62, 0)])

    # 采石场。走隧道时可触发吊石，地表维修栈道可绕过并找到补给。
    m.ground(65, 95, 9, 7, "d")
    m.ground(96, 133, 7, 9, "d")
    for left, right, y, top in [(72, 80, 21, 25), (81, 95, 18, 29),
                                (96, 110, 17, 30), (111, 122, 20, 27), (123, 128, 23, 25)]:
        m.rect(left, y, right, top, "c")
        m.rect(left, 8, right, y - 1, "x", background=True)
    m.rect(68, 10, 68, 24, "L")
    m.rect(69, 24, 71, 24, "p")
    m.rect(81, 26, 81, 29, "L")
    m.rect(96, 30, 96, 30, "L")
    m.rect(111, 30, 124, 30, "p")
    m.rect(125, 23, 125, 30, "L")
    m.rect(129, 10, 129, 24, "L")
    m.rect(126, 24, 128, 24, "p")
    m.rect(88, 16, 89, 16, "f")
    m.rect(104, 15, 104, 15, "f")
    m.collapse((78, 9, 81, 14), (88, 16))
    m.collapse((96, 8, 99, 13), (104, 15))
    m.place(70, "CheckPoint")
    m.place(101, "Cage", y=31)
    m.place(117, "AmmoCrate", y=31)
    m.place(111, "Crate")
    m.troops("Mook", [(83, 0), (92, 3), (105, 0), (119, 4), (128, 2)])

    # 炼油厂。罐区与输油管位于地面，泵房住人，桥上提供射击角度。
    m.ground(134, 213, 9, 9, "j", "o")
    m.rect(139, 10, 160, 23, "z", background=True)
    m.rect(164, 10, 190, 30, "z", background=True)
    m.rect(194, 10, 210, 18, "y", background=True)
    m.rect(139, 17, 155, 17, "p")
    m.rect(145, 10, 145, 17, "L")
    m.rect(151, 22, 168, 22, "p")
    m.rect(151, 18, 151, 22, "L")
    m.rect(169, 22, 191, 22, "u")
    m.rect(175, 23, 175, 31, "u")
    m.rect(183, 23, 183, 34, "u")
    m.rect(191, 10, 191, 22, "L")
    m.rect(196, 17, 210, 17, "p")
    m.rect(203, 10, 203, 17, "L")
    m.rect(164, 10, 164, 10, "P")
    m.rect(166, 10, 166, 10, "B")
    m.rect(168, 10, 168, 10, "B")
    m.rect(178, 10, 178, 10, "O")
    m.rect(188, 10, 188, 10, "P")
    m.collapse((157, 10, 161, 14), (164, 10))
    m.place(137, "CheckPoint")
    m.place(142, "Villager")
    m.place(149, "Cage", y=18)
    m.place(159, "AmmoCrate", y=23)
    m.place(205, "AmmoCrate", y=18)
    m.place(208, "Crate")
    m.troops("Mook", [(147, 0), (157, 2), (172, 6), (180, 1), (199, 7), (211, 0)])
    m.place(200, "Mook", 4, y=18)

    # 异形巢穴，低谷有孵化区，顶上较窄的脊骨通道通向医疗储藏间。
    m.ground(214, 241, 9, 5, "a")
    m.ground(242, 268, 5, 9, "a")
    m.ground(269, 294, 9, 14, "a")
    m.rect(222, 6, 290, 32, "n", background=True)
    m.rect(224, 15, 228, 18, "A")
    m.rect(229, 18, 245, 19, "A")
    m.rect(246, 20, 256, 23, "A")
    m.rect(258, 21, 274, 22, "A")
    m.rect(275, 23, 288, 24, "A")
    m.rect(225, 9, 225, 18, "i")
    m.rect(248, 20, 248, 23, "i")
    m.rect(257, 20, 257, 22, "i")
    m.rect(275, 23, 275, 24, "i")
    m.rect(287, 14, 287, 24, "i")
    for x in (235, 237, 251, 254, 265, 279):
        y = m.floor[x] + 1
        m.rect(x, y, x, y, "E")
    m.place(215, "CheckPoint")
    m.place(223, "AlienFronds")
    m.place(242, "AlienFronds")
    m.place(270, "AlienFronds")
    m.place(241, "Alien", 4, y=20)
    m.place(250, "Cage", y=24)
    m.place(266, "AmmoCrate", y=23)
    m.troops("Alien", [(231, 0), (239, 1), (247, 3), (261, 0), (273, 2), (289, 1)])

    # 墓道的流沙有上方石桥。装死亡灵放在玩家必经的平地上。
    m.ground(295, 318, 14, 13, "h", "H")
    m.ground(319, 336, 13, 13, "h", "q")
    m.ground(337, 364, 13, 24, "h", "H")
    m.rect(301, 14, 318, 31, "v", background=True)
    m.rect(302, 27, 318, 29, "h")
    m.rect(314, 19, 342, 19, "g")
    m.rect(315, 14, 315, 19, "l")
    m.rect(341, 16, 341, 19, "l")
    m.rect(350, 26, 359, 26, "G")
    m.rect(352, 20, 352, 26, "l")
    m.place(298, "CheckPoint")
    m.place(312, "HellDecoration")
    m.place(322, "HellDecoration", y=20)
    m.place(331, "AmmoCrate", y=20)
    m.place(356, "Cage", y=27)
    m.troops("HellEnemy", [(304, 2), (311, 0), (317, 2), (337, 3), (346, 2), (357, 6)])

    # 要塞与开阔的撤离台。单个 PureEvil 放在最后的独立战斗区。
    m.ground(365, 455, 24, 24, "j", "g")
    m.rect(366, 25, 390, 39, "y", background=True)
    m.rect(396, 25, 435, 44, "+", background=True)
    m.rect(372, 31, 394, 31, "p")
    m.rect(374, 25, 374, 31, "L")
    m.rect(381, 37, 404, 37, "G")
    m.rect(389, 32, 389, 37, "L")
    m.rect(400, 25, 400, 37, "L")
    m.rect(407, 39, 429, 41, "g")
    m.rect(411, 31, 416, 31, "g")
    m.rect(424, 33, 432, 33, "G")
    m.rect(434, 25, 434, 33, "L")
    m.rect(441, 24, 455, 24, "p")
    m.place(368, "CheckPoint")
    m.place(378, "AmmoCrate", y=32)
    m.place(385, "Crate")
    m.place(398, "CheckPoint")
    m.place(405, "AmmoCrate")
    m.place(418, "PureEvil", 0)
    m.troops("Mook", [(371, 2), (380, 6), (392, 7)])
    m.place(384, "Mook", 0, y=38)

    return m.project("裂谷回声", "河岸救援、采石场落石、炼油厂连爆、巢穴孵化区、墓道伏击与要塞撤离。"
                     "流沙可从上方石桥绕过。击败撒旦后，向右进入撤离台。", (450, 25))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    map_id, layout = ("supermap_mechanics_probe", mechanics_probe) if args.probe else ("裂谷回声", echo_rift)
    print(save_project(map_id, layout()))
