"""把 research/dumps/enums_raw.txt 转成素材清单：research/catalog.json + docs/04_可放置素材清单.md。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(r"E:\BroforceMapEditor")
RAW = ROOT / "research" / "dumps" / "enums_raw.txt"
JSON_OUT = ROOT / "research" / "catalog.json"
MD_OUT = ROOT / "docs" / "04_可放置素材清单.md"

TITLES = {
    "TerrainType": "地形块（可摆放的地面/墙/装饰地形）",
    "DoodadType": "物件/敌人/装饰/机关（可放置 Doodad）",
    "TriggerType": "触发器条件类型",
    "TriggerActionType": "触发器动作类型",
    "EnemyActionType": "敌人行为动作（编队 AI 用）",
    "CharacterCommandType": "角色指令类型",
    "HeroSpawnMode": "英雄出场方式",
    "CameraFollowMode": "镜头跟随模式",
    "GameMode": "游戏模式",
    "LevelTheme": "关卡主题",
    "MusicType": "音乐类型",
    "WeatherType": "天气类型",
    "AmbienceType": "环境氛围",
    "RainType": "雨类型",
    "GroundType": "地面类型",
    "FlexPowerMapType": "摆动作能力映射",
    "DifficultyWorkshopTag": "工坊难度标签",
    "GameplayWorkshopTag": "工坊玩法标签",
    "HeroType": "可玩英雄",
    "MapLoadMode": "地图加载模式",
}

blocks: dict[str, list[tuple[str, int]]] = {}
current = None
pattern = re.compile(r"^FIELD public static \S+ (\w+) = (-?\d+)$")
for line in RAW.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line.startswith("=== ") and line.endswith(" ==="):
        current = line[4:-4]
        blocks.setdefault(current, [])
        continue
    if current is None:
        continue
    match = pattern.match(line)
    if match:
        name, value = match.group(1), int(match.group(2))
        if name == "value__":
            continue
        entry = (name, value)
        if entry not in blocks[current]:
            blocks[current].append(entry)

for values in blocks.values():
    values.sort(key=lambda item: item[1])

catalog = {name: {key: value for key, value in values} for name, values in blocks.items()}
JSON_OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

lines = ["# 可放置素材清单", "", "来源：`Assembly-CSharp.dll` 枚举反射（构建 `12964083`），由 `tools/build_catalog.py` 生成。", ""]
lines.append("| 类别 | 数量 |")
lines.append("|---|---|")
for name, values in blocks.items():
    lines.append(f"| {name} | {len(values)} |")
lines.append("")
for name, values in blocks.items():
    lines.append(f"## {name} — {TITLES.get(name, name)}（{len(values)}）")
    lines.append("")
    if not values:
        lines.append("（未能解析，检查 enums_raw.txt）")
        lines.append("")
        continue
    lines.append("| 值 | 名称 |")
    lines.append("|---|---|")
    for key, value in values:
        lines.append(f"| {value} | {key} |")
    lines.append("")

MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("wrote", JSON_OUT.name, "and", MD_OUT.name)
for name, values in blocks.items():
    print(f"  {name}: {len(values)}")
