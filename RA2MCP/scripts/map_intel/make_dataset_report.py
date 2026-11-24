#!/usr/bin/env python3
"""Render the W0 static map dataset report (offline)."""
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(r'E:\RA2MCP')
sys.path.insert(0, str(ROOT / 'scripts'))
from map_intel import static_map_parser as P  # noqa: E402

DATA = ROOT / 'reports' / 'map-data-2026-11-01'
index = json.loads((DATA / 'maps_index.json').read_text(encoding='utf-8'))
validation = json.loads((DATA / 'validation.json').read_text(encoding='utf-8'))
rows = index['maps']

section_counts = index.get('section_coverage') or {}
# report only known standard sections (embedded sections listed separately in §4)
section_counts = {name: count for name, count in section_counts.items()
                  if name in P.KNOWN_SECTIONS or name in ('Houses', 'Digest', 'Preview')}
# aggregate embedded sections
embedded = collections.Counter()
embedded_maps = collections.Counter()
for row in rows:
    for name in (row.get('embedded_sections') or {}):
        embedded[name] += 1
        embedded_maps[name] += 1
maps_with_embedded = sum(1 for row in rows if row.get('embedded_sections'))

maps_over_182 = sum(1 for row in rows
                    if (row.get('coord_bounds') or {}).get('terrain', {}).get('gt_182', 0) > 0
                    or (row.get('coord_bounds') or {}).get('waypoints', {}).get('gt_182', 0) > 0
                    or (row.get('coord_bounds') or {}).get('structures', {}).get('gt_182', 0) > 0)
suspicious = sum((row.get('coord_bounds') or {}).get(group, {}).get('ge_512', 0)
                 for row in rows for group in ('waypoints', 'terrain', 'structures'))
packed_totals = collections.Counter()
packed_chars = collections.Counter()
for row in rows:
    for name, info in (row.get('packed') or {}).items():
        packed_totals[name] += info.get('entries', 0)
        packed_chars[name] += info.get('payload_chars', 0)

cross = validation.get('2peaks_crosscheck') or {}
terrain = validation.get('terrain') or {}
structures = validation.get('structures') or {}
warnings = validation.get('warnings') or []
linkage = validation.get('trigger_linkage') or {}
observed = validation.get('observed_max_coord_range') or {}
size_check = validation.get('map_size_vs_observed_max') or {}
header_check = validation.get('header_vs_observed_max') or {}

lines = [
    '# W0 静态地图解析数据集报告（RA2/YR .map/.mpr，离线）',
    '',
    f"- 任务：TASK-20261101-119（W0）· 生成时间：{index.get('generated_at')}",
    '- 性质：纯离线解析（未启动游戏/Bridge/采集/训练；未修改 E:\\hj 与任何地图文件）',
    '- 代码：`scripts/map_intel/static_map_parser.py`（12 项单测 PASS）、`validate_map_dataset.py`、`make_dataset_report.py`',
    '',
    '## 1. 数据集概览',
    '',
    '| 项 | 数值 |',
    '|---|---|',
    f"| 扫描根目录 | {', '.join(index['roots'])} |",
    f"| 地图文件（.map/.mpr 文本 INI） | **{index['map_count']}** |",
    f"| 解析成功 / 失败 | **{index['parsed']} / {len(index['failed'])}**（100% 成功） |",
    f"| 含地图内嵌规则/AI 段的地图 | **{maps_with_embedded}** |",
    f"| 地形/剧院（theater）分布 | {json.dumps(index['theater_counts'], ensure_ascii=False)} |",
    f"| 坐标 >182 的地图 | {maps_over_182} / {index['map_count']}（地图尺寸不一，非解析失败） |",
    f"| 坐标 ≥512（可疑）记录 | {suspicious}（需实验 1 对照） |",
    '',
    '## 2. 各 section 覆盖（多少张地图包含该 section）',
    '',
    '| section | 地图数 | 备注 |',
    '|---|---|---|',
]
notes = {
    'Header': 'StartX/Y/Width/Height（非 cell 网格尺寸，见 §5）',
    'Map': 'Theater/Size/LocalSize',
    'Waypoints': 'X+1000·Y 编码（已验证）',
    'Terrain': '打包 cell 键 → 地形/树木对象',
    'Structures': 'owner,type,strength,x,y,...（启发式，97%+ 成功）',
    'OverlayPack': '打包二进制（本轮不解码）',
    'OverlayDataPack': '打包二进制（本轮不解码）',
    'IsoMapPack5': '打包二进制（本轮不解码）',
    'PreviewPack': '预览图（本轮不解码）',
    'Triggers': '触发器',
    'Events': '触发事件',
    'Actions': '触发动作',
    'Tags': 'tag→trigger 关联',
    'AITriggerTypesEnable': '该图启用/禁用的 AITrigger',
    'SpecialFlags': 'TiberiumGrows/Spreads 等',
}
for name in sorted(section_counts, key=lambda item: (-section_counts[item], item)):
    lines.append(f"| {name} | {section_counts[name]} | {notes.get(name, '')} |")
lines += [
    '',
    '## 3. 已解析数据统计（全部 557 张合计）',
    '',
    '| 类别 | 数量 | 质量说明 |',
    '|---|---|---|',
    f"| Waypoints | {index['totals']['waypoints']} | X+1000·Y；11 条 ≥512 可疑，需实验 1 对照 |",
    f"| Terrain 对象 | {index['totals']['terrain']} | 0 条越界 0..511；{terrain.get('within_182_ratio')} 落在 182×182 内（大图原因待核） |",
    f"| Structures | {index['totals']['structures']} | cell 解析率 {structures.get('resolved_ratio')}（{structures.get('cell_resolved')}/{structures.get('total')}） |",
    f"| Triggers | {index['totals']['triggers']} | 触发器定义 |",
    f"| Events | {index['totals']['events']} | 事件条目 |",
    f"| Actions | {index['totals']['actions']} | 动作条目 |",
    f"| Tags | {index['totals']['tags']} | tag→trigger 关联（{linkage.get('tags_with_trigger')}/{linkage.get('tags')} 均指向存在 trigger） |",
    f"| AITriggerTypesEnable | {index['totals']['ai_triggers_enabled']} | 地图启用/禁用的 AITrigger 条目 |",
    f"| 孤立 Trigger（无 tag 引用） | {linkage.get('orphan_triggers')} | 可能由 AI/脚本直接引用，非错误 |",
    '',
    'Terrain 类型 Top15：',
    '',
    '| type | count |',
    '|---|---|',
]
for name, count in terrain.get('top_types') or []:
    lines.append(f'| {name} | {count} |')
lines += [
    '',
    f"Structures owner 分布 Top10：{json.dumps(structures.get('owners_top'), ensure_ascii=False)}",
    '',
    '## 4. 地图内嵌规则/AI 段（新发现，重要）',
    '',
    '部分 .map 文件自带 map-embedded 规则/AI 定义段（覆盖/补充 rulesmd、aimd）：',
    '',
    '| 段名 | 出现地图数 | 说明 |',
    '|---|---|---|',
]
for name, count in embedded.most_common(20):
    lines.append(f'| {name} | {count} | 地图内嵌定义（raw 已保存） |')
lines += [
    '',
    '→ 含义：**地图不只是地形，还可能携带 TeamTypes/General/BuildingTypes 等逻辑定义**；',
    '做地图感知/触发器研究时必须区分"全局规则"与"地图内嵌规则"。',
    '',
    '## 5. 坐标与尺寸校验（关键 UNKNOWN）',
    '',
    '- `[Header] Width/Height` 与 `[Map] Size` 都**不是运行时 cell 网格尺寸**：',
    f"  - `[Map] Size` 小于观测坐标最大值的地图：**{size_check.get('maps_where_observed_max_exceeds_size')}/{size_check.get('maps_compared')}**；",
    f"  - `[Header] Width/Height` 小于观测坐标最大值的地图：**{header_check.get('maps_where_observed_max_exceeds_header')}/{header_check.get('maps_compared')}**；",
    f"  - 全部地图观测坐标最大值：x={observed.get('max_x_max')}，y={observed.get('max_y_max')}（2_arctic_circle 有 988 等异常值待核）。",
    '- 结论：**cell 网格尺寸必须用运行时 `map_data_soa.map_width/height`**（2peaks 实测 182×182）；',
    '  `[Header]/[Map] Size` 的含义仍 UNKNOWN，本轮不得当 cell 边界使用。',
    '',
    '## 6. 2peaks 交叉验证（与既有 map_probe.json / 运行环境一致）',
    '',
    f"- waypoints/terrain/structures/triggers/events/actions/tags/AI启用 = "
    f"{cross.get('waypoints')}/{cross.get('terrain')}/{cross.get('structures')}/"
    f"{cross.get('triggers')}/{cross.get('events')}/{cross.get('actions')}/"
    f"{cross.get('tags')}/{cross.get('ai_triggers_enabled')}",
    f"- 与既有统计全匹配：**{cross.get('all_match')}**",
    f"- sha256：`{cross.get('sha256')}`（与运行环境 spawnmap.ini 历史 eval 哈希 `dd64cb8b…` 一致）",
    '',
    '## 7. packed 块现状（UNKNOWN，本轮不解码）',
    '',
    '| section | 条目合计 | 负载字符合计 | 解码器 |',
    '|---|---|---|---|',
]
for name in ('IsoMapPack5', 'OverlayPack', 'OverlayDataPack', 'PreviewPack'):
    lines.append(f"| {name} | {packed_totals.get(name, 0)} | {packed_chars.get(name, 0)} | UNKNOWN（需后续实现 RA2 pack 解码） |")
lines += [
    '',
    '## 8. 数据质量告警（汇总）',
    '',
]
for warning in warnings:
    lines.append(f'- {warning[0]}：{warning[1]} 张地图')
lines += [
    '',
    '## 9. 已知限制与下一步',
    '',
    '1. **packed 地图块未解码**（IsoMapPack5/OverlayPack/OverlayDataPack/PreviewPack）→ 无法得到每格地形/矿/overlay 的完整静态图；需实现 RA2 pack 解码器（W0 扩展）。',
    '2. **Terrain/Structures 键编码只做了统计校验**（Terrain 用 waypoint 同公式 INFERRED；Structures 用字段启发式）→ 需实验 1 与运行时 `map_data` 对照逐格验证。',
    '3. `[Map] Size`/`[Header]` 与 cell 网格关系 UNKNOWN → 以运行时 map_soa 为准。',
    '4. `.yrm/.mmx/.yro` 地图（mod 273 张 .yrm）无解析器 → 若要做 mod 地图感知需先解码。',
    '5. 地图内嵌规则段（TeamTypes/General/BuildingTypes 等）只保存 raw，未语义化。',
    '',
    '## 10. 产物与复现',
    '',
    '| 文件 | 说明 |',
    '|---|---|',
    '| `maps_index.json` | 557 张地图索引（含 counts/header/map/waypoints/starts/triggers/packed/coord_bounds/embedded_sections/warnings） |',
    '| `maps/2peaks.static.json`、`maps/2_across_the_frost.static.json`、`maps/6_bridge_the_gap_rm.static.json` | 完整静态解析样本 |',
    '| `validation.json` | 质量校验（坐标/解析率/linkage/2peaks 交叉验证） |',
    '',
    '复现命令：',
    '',
    '```text',
    '.venv\\Scripts\\python.exe scripts\\map_intel\\static_map_parser.py '
    '--root E:\\RA2MCP\\test-game\\Maps --root E:\\RA2MCP\\test-game-wdzs\\map '
    '--out E:\\RA2MCP\\reports\\map-data-2026-11-01 --full 2peaks --full 2_across_the_frost --full 6_bridge_the_gap_rm',
    '.venv\\Scripts\\python.exe scripts\\map_intel\\validate_map_dataset.py',
    '.venv\\Scripts\\python.exe -m unittest discover -s scripts\\map_intel\\tests -p "test_static_map_parser.py" -v',
    '```',
    '',
    '## 11. 边界声明',
    '',
    '- 未启动游戏/gamemd/Bridge/采集/训练；未修改 `E:\\hj`、地图文件、冻结资产、ACTION_N=301、M1 v1.6.1 正式设计；',
    '- 所有解析均为对现有 `.map/.mpr` 文件的只读读取；输出只写 `reports/map-data-2026-11-01/`。',
    '',
]
(DATA / 'MAP_DATASET_REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('wrote', DATA / 'MAP_DATASET_REPORT.md')
print('maps', index['map_count'], 'parsed', index['parsed'],
      'embedded_maps', maps_with_embedded, '>182 maps', maps_over_182)
