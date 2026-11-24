"""生成 docs/08_定义_方块与敌人.md：方块属性（实测）+ 敌人属性（实测）。"""
import json
from pathlib import Path

ROOT = Path(r"E:\BroforceMapEditor")
cat = json.loads((ROOT / "research/catalog.json").read_text(encoding="utf-8"))
defs = json.loads((ROOT / "research/definitions.json").read_text(encoding="utf-8"))
attrs_raw = json.loads((ROOT / "evidence/terrain_attrs_raw.json").read_text(encoding="utf-8"))
enemy_health = json.loads((ROOT / "evidence/enemy_health.json").read_text(encoding="utf-8"))

fg = {r["name"]: r["foreground"] for r in defs["terrain"]}
gt = {r["name"]: r["groundName"] for r in defs["terrain"]}
names = sorted(cat["TerrainType"].items(), key=lambda kv: kv[1])

# 每格探针按值顺序：x = 2 + index，y = 1
attrs = {}
for index, (name, value) in enumerate(names):
    entry = attrs_raw.get(f"{2 + index},1")
    if not entry:
        attrs[name] = None
        continue
    terrain, solid, ladder, quicksand, destroyed = entry.split(",")
    attrs[name] = {"solid": solid == "1", "ladder": ladder == "1",
                   "quicksand": quicksand == "1", "empty": destroyed == "1"}

lines = [
    "# 定义：方块（地形）与敌人",
    "",
    "日期：2026-11-15｜游戏构建 `12964083`（Broforce 1.0.3148）",
    "",
    "坠落、连爆、装死亡灵及中立机关的后续查询见 [裂谷回声·机制与路线](09_裂谷回声_机制与路线.md)。",
    "",
    "**数据来源（全部运行时实测）**",
    "",
    "| 项目 | 方法 | 证据文件 |",
    "|---|---|---|",
    "| 方块：可站立/梯子/流沙 | 桥接调用 `Map.IsBlockSolid / IsBlockLadder / IsBlockQuicksand / IsBlockDestroyed` 逐格实测 | `evidence/terrain_attrs_raw.json` |",
    "| 方块：前景/背景 | 反演 `MapData.IsForegroundType`（IL） | `research/definitions.json` |",
    "| 敌人：类型 | 按 `DoodadType × variation` 摆放，读取运行时对象名 | `evidence/enemy_defs_matrix.json` |",
    "| 敌人：血量 | 同上，读 `health` 字段 | `evidence/enemy_health.json` |",
    "",
    "---",
    "",
    "## 一、方块属性（86 种，逐格实测）",
    "",
    "| 值 | 名称 | 前景 | 可站立 | 梯子 | 流沙 | 备注 |",
    "|---|---|---|---|---|---|---|",
]
for name, value in names:
    a = attrs.get(name) or {}
    note = ""
    if a.get("empty"):
        note = "空气/背景"
    elif a.get("ladder"):
        note = "可攀爬"
    elif a.get("quicksand"):
        note = "流沙（下陷）"
    elif a.get("solid"):
        note = "实心"
    lines.append(
        f"| {value} | {name} | {'是' if fg.get(name) else '否'} | "
        f"{'✅' if a.get('solid') else '—'} | {'✅' if a.get('ladder') else '—'} | "
        f"{'✅' if a.get('quicksand') else '—'} | {note} |"
    )

lines += [
    "",
    "统计：**实心 63 种 / 梯子 5 种（Ladder, AlienLadder, MetalLadder, CityLadder, DesertLadder）"
    "/ 流沙 1 种（Quicksand）**，其余为空气或纯背景。",
    "",
    "已知但**尚未逐项实测**的方块属性（需要你确认是否要查）：",
    "",
    "- 可破坏性：哪些地形能被 `Collapse` 塌方触发器/爆炸摧毁（`Map.IsBlockDestroyed` 只反映“当前是否已被摧毁”，不说明初始可破坏集合）。",
    "- 爆炸物：`Barrel` / `PropaneBarrel` / `AlienEggExplosive` / `AlienExplodingFlesh` / `BuriedRocket` 的爆炸半径与伤害。",
    "- 伤害判定：`AlienBarbShooter`（棘刺）是否持续伤害、伤害数值。",
    "- 特殊效果：`FallingBlock`（坠落时机/伤害）、`Quicksand`（下陷速度）、`Door`（开关条件）、`Vault`（跳跃/滑行）。",
    "",
    "---",
    "",
    "## 二、敌人属性（实测类名 + 血量）",
    "",
    "### Mook（8 种变体全部有效）",
    "",
    "| variation | 敌人 | 血量 | 行为备注 |",
    "|---|---|---|---|",
]
mook_notes = {
    "ZMook": "普通射击兵",
    "ZMookSuicide": "自爆兵",
    "ZMookRiotShield": "防暴盾（正面抗弹）",
    "ZMookBigGuy": "大块头近战/重击",
    "ZMookScout": "侦察兵（快速）",
    "ZMookDog": "军犬（扑咬）",
    "ZMookArmouredGuy": "重甲兵",
    "ZMook Grenadier": "掷弹兵（抛掷爆炸物）",
}
for key in [f"Mook|{i}" for i in range(8)]:
    entries = enemy_health.get(key) or []
    for entry in entries:
        name = entry["name"]
        lines.append(f"| {key.split('|')[1]} | {name} | {entry['health']:g} | {mook_notes.get(name, '')} |")

lines += ["", "### Alien（异形）", "", "| variation | 敌人 | 血量 | 行为备注 |", "|---|---|---|---|"]
alien_notes = {
    "ZAlienFaceHugger": "抱脸虫（扑脸）",
    "ZAlienXenomorph": "异形（近战突进）",
    "ZAlienBrute": "异形壮汉（高血量）",
    "ZAlienMelter": "喷酸异形（远程酸液）",
    "ZAlienMosquito": "异形蚊（飞行骚扰）",
}
for key in [f"Alien|{i}" for i in range(8)]:
    for entry in enemy_health.get(key) or []:
        name = entry["name"]
        lines.append(f"| {key.split('|')[1]} | {name} | {entry['health']:g} | {alien_notes.get(name, '')} |")

lines += ["", "### HellEnemy（地狱）", "", "| variation | 敌人 | 血量 | 行为备注 |", "|---|---|---|---|"]
hell_notes = {
    "ZHellDog": "地狱犬（扑咬）",
    "ZMookUndead": "亡灵兵",
    "ZMookUndeadStartDead": "装死亡灵（伏击，初始 0 血）",
    "ZMookWarlock": "术士兵（远程法术）",
    "ZMookHellBoomer": "地狱爆破兵（高血量自爆）",
    "ZMookUndeadSuicide": "亡灵自爆兵",
    "ZHellBigGuy": "地狱大块头（高血量近战）",
}
for key in [f"HellEnemy|{i}" for i in range(8)]:
    for entry in enemy_health.get(key) or []:
        name = entry["name"]
        lines.append(f"| {key.split('|')[1]} | {name} | {entry['health']:g} | {hell_notes.get(name, '')} |")

lines += [
    "",
    "### Boss / 特殊单位",
    "",
    "| 素材 | variation | 敌人 | 血量 |",
    "|---|---|---|---|",
]
for key, entries in enemy_health.items():
    kind = key.split("|")[0]
    if kind in ("Mook", "Alien", "HellEnemy"):
        continue
    for entry in entries:
        lines.append(f"| {kind} | {key.split('|')[1]} | {entry['name']} | {entry['health']:g} |")

lines += [
    "",
    "注意事项：",
    "",
    "- `PureEvil` 的 Boss 类型**不完全由 variation 固定**：两轮实测中 variation=0/4 出现过 `ZSatan`，"
    "variation=2/5/6/7 出现过 `ZMookGeneral`，个别变体两轮结果不同 → 存在随机或上下文依赖。",
    "- `Agents` 的友/敌关系**两轮结果不同**（同一 variation 先后出现 Friend 与 Enemy）→ 阵营由运行时决定，不能当地图设计依据。",
    "- `Miniboss`：两次实测 variation=2 与 7 都刷出 `DolfLundgrenSoldier`（110 血），其它变体未生成。",
    "- `MookDoor` / `AlienSpawner`：首次探针未观察到刷怪。后续检查确认，两者有内建的玩家距离、画面位置或计时条件，也可通过原生触发器控制。",
    "",
    "尚未实测的敌人属性：伤害数值、攻击间隔/射程、移动速度、掉落物、是否对特定武器免疫。",
    "",
    "---",
    "",
    "## 三、两条关键工程结论（实测）",
    "",
    "### 1. 英雄不是从 SpawnPoint 出生，而是直升机从地图左上角投放",
    "",
    "- 卡住时 `player_pos` = `x=-39.4, y=188.5` → 格 `(-2,12)`，`solidHere=true`（在地图外/岩体里）。",
    "- 因此在“整图填满岩石”的纵向地图里必然卡死；解法是**左缘留一条从顶到底的降落空井**。",
    "- 修复后实测：`cell (6,3)`、`solidHere=false`、`solidBelow=true` → 正常站在地面（`red_vertical_07`）。",
    "",
    "### 2. 终点触发已验证",
    "",
    "`TriggerType.Area` + `TriggerActionType.LevelEvent:Extraction`：进入区域后 `state.lastCompletedMap` 被写入并返回主菜单"
    "（实测写入 `赤色远征 · 纵向开凿 D`）。生成器 `map_authoring.export_map` 会按项目 `goal` 自动写入。",
]

target = ROOT / "docs/08_定义_方块与敌人.md"
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("written:", target)
print("terrain rows:", len(names))
print("enemy rows:", sum(len(v) for v in enemy_health.values()))
