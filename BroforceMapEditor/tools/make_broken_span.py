"""Author the Broken Span campaign as a single quarry with three elevations."""

from make_supermap import Layout
from map_authoring import save_project


MAP_ID = "裂谷回声_断桥"


def broken_span():
    m = Layout(212, 92)

    # An upper road and two bridge heads cut into the same quarry walls.
    m.ground(0, 53, 60, 61, "c", "s")
    m.ground(54, 140, 10, 10, "d", "m")
    m.ground(141, 169, 10, 24, "c", "s")
    m.ground(170, 183, 24, 40, "c", "s")
    m.ground(184, 211, 40, 40, "c", "s")
    m.rect(54, 61, 88, 61, "p")
    m.rect(57, 57, 63, 60, "m")
    m.rect(58, 55, 58, 57, "m")
    m.rect(62, 56, 62, 57, "m")
    m.rect(115, 61, 154, 61, "p")
    m.rect(143, 57, 145, 60, "m")
    m.rect(152, 55, 154, 60, "m")
    m.rect(155, 57, 169, 61, "c")

    # A tall slate face, a watchhouse and the left maintenance recess.
    m.rect(12, 62, 34, 71, "y", background=True)
    m.rect(14, 71, 35, 71, "m")
    m.rect(21, 62, 21, 71, "L")
    m.rect(10, 70, 14, 70, "p")
    m.rect(35, 66, 50, 66, "p")
    m.rect(47, 62, 47, 66, "L")
    m.rect(42, 40, 55, 49, ".")
    m.rect(42, 40, 56, 49, "y", background=True)
    m.rect(50, 39, 60, 39, "p")
    m.rect(54, 40, 54, 60, "L")
    m.rect(53, 52, 62, 52, "p")
    m.rect(54, 52, 54, 52, "L")
    m.rect(47, 44, 51, 44, "m")
    m.rect(49, 40, 49, 44, "L")
    m.place(6, "SpawnPoint")
    m.place(23, "CheckPoint")
    m.place(19, "Villager")
    m.troops("Mook", [(28, 0), (38, 2), (45, 4)])
    m.place(42, "AmmoCrate", y=67)
    m.place(51, "Cage", y=40)
    m.place(46, "AmmoCrate", y=40)

    # The deck fractures from the front edge inwards. The terraces below catch the drop.
    m.rect(72, 62, 72, 62, "B")
    m.rect(77, 62, 77, 62, "P")
    m.rect(83, 62, 83, 62, "B")
    m.collapse((68, 62, 73, 68), (72, 61))
    for left, right, y in [(67, 81, 53), (71, 88, 45), (78, 96, 37),
                           (87, 105, 29), (94, 115, 21), (106, 121, 14)]:
        m.rect(left, y, right, y, "p")
        m.rect(left + 1, y - 2, left + 2, y - 1, "m")
        m.rect(left + 2, y - 5, right - 1, y - 1, "x", background=True)
    m.rect(63, 39, 68, 39, "p")
    m.rect(63, 32, 76, 32, "p")
    m.rect(67, 32, 67, 39, "L")
    m.rect(74, 22, 84, 22, "p")
    m.rect(76, 23, 76, 32, "L")
    m.rect(81, 11, 81, 22, "L")
    m.rect(67, 46, 67, 52, "L")
    m.place(74, "AmmoCrate", y=54)
    m.place(83, "Mook", 0, y=46)
    m.place(92, "Mook", 5, y=30)
    m.place(101, "Mook", 2, y=22)

    # Bottom loading bay. The freight wall can be breached at ground level or bypassed above.
    m.rect(88, 11, 134, 18, "z", background=True)
    m.rect(115, 19, 142, 29, "z", background=True)
    m.rect(96, 15, 105, 15, "p")
    m.rect(99, 11, 99, 15, "L")
    m.rect(120, 22, 137, 22, "p")
    m.rect(122, 11, 122, 22, "L")
    m.rect(136, 11, 136, 22, "L")
    m.rect(126, 11, 129, 15, "e")
    m.rect(124, 11, 124, 11, "B")
    m.rect(131, 11, 131, 11, "P")
    m.place(112, "CheckPoint")
    m.place(117, "AmmoCrate")
    m.place(120, "Mook", 6)
    m.place(133, "Mook", 7, y=23)
    m.troops("HellEnemy", [(105, 2), (110, 2), (133, 2)])
    m.place(140, "Mook", 2)

    # A winding haul road and one lift shaft with landings provide the way back above the quarry.
    m.rect(146, 13, 170, 33, "x", background=True)
    m.rect(146, 30, 166, 31, "c")
    m.rect(153, 27, 154, 29, "m")
    m.rect(169, 24, 169, 43, "L")
    m.rect(162, 35, 168, 35, "p")
    m.rect(151, 43, 168, 43, "p")
    m.rect(151, 44, 169, 57, "z", background=True)
    m.rect(155, 51, 166, 51, "p")
    m.rect(156, 44, 156, 51, "L")
    m.rect(163, 52, 163, 61, "L")
    m.rect(167, 60, 173, 62, "c")
    m.rect(174, 62, 193, 62, "m")
    m.rect(193, 63, 208, 63, "g")
    m.rect(208, 64, 211, 64, "p")
    m.rect(172, 63, 197, 72, "y", background=True)
    m.rect(182, 72, 193, 72, "p")
    m.rect(184, 63, 184, 72, "L")
    m.rect(187, 66, 190, 66, "m")
    m.rect(196, 69, 201, 69, "p")
    m.place(152, "CheckPoint")
    m.place(162, "Cage", y=44)
    m.place(160, "AmmoCrate", y=52)
    m.place(150, "Mook", 3)
    m.place(158, "Mook", 4)
    m.place(170, "Mook", 0)
    m.place(167, "Mook", 0, y=44)
    m.place(173, "CheckPoint", y=63)
    m.place(177, "AmmoCrate", y=63)
    m.place(178, "Mook", 2, y=63)
    m.place(188, "Mook", 4, y=67)
    m.place(197, "PureEvil", y=64)

    project = m.project("裂谷回声·断桥", "穿过断桥后，沿井壁的落脚台下到装卸区。井底土墙可破坏，也可以从上方栈道绕过。沿对岸的运输坡道和梯井爬上去，击败撒旦后向右撤离。", (207, 64))
    project["cascade"] = {"trigger": 0, "actions": [
        {"x": x, "y": 61, "delay": round(0.08 * index, 2)}
        for index, x in enumerate((73, 74, 75, 76, 77, 78, 79, 80, 81, 82), 1)
    ] + [{"x": 77, "y": 62, "delay": 0.32}]}
    return project


if __name__ == "__main__":
    print(save_project(MAP_ID, broken_span()))
