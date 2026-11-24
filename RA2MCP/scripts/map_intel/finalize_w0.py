#!/usr/bin/env python3
"""Finalize W0: register map dataset artifacts, complete TASK-119, one version bump, events."""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(r'E:\RA2MCP')
sys.path.insert(0, str(ROOT / 'scripts' / 'coordination'))
import context_store as store  # noqa: E402

DATA = ROOT / 'reports' / 'map-data-2026-11-01'
state = store.load_state()
base = int(state['context_version'])
store.check_base_version(base)
tasks_doc = store.load_tasks()
artifacts_doc = store.load_artifacts()

ARTIFACTS = [
    ('reports/map-data-2026-11-01/MAP_DATASET_REPORT.md', 'report', 'w0_map_dataset_report'),
    ('reports/map-data-2026-11-01/maps_index.json', 'data', 'w0_maps_index'),
    ('reports/map-data-2026-11-01/validation.json', 'data', 'w0_dataset_validation'),
    ('reports/map-data-2026-11-01/maps/2peaks.static.json', 'data', 'w0_full_dump_2peaks'),
    ('reports/map-data-2026-11-01/maps/2_across_the_frost.static.json', 'data', 'w0_full_dump_across_frost'),
    ('reports/map-data-2026-11-01/maps/6_bridge_the_gap_rm.static.json', 'data', 'w0_full_dump_bridge_gap'),
    ('reports/map-data-2026-11-01/SHA256.txt', 'manifest', 'w0_sha256'),
    ('scripts/map_intel/static_map_parser.py', 'code', 'w0_static_map_parser'),
    ('scripts/map_intel/tests/test_static_map_parser.py', 'code', 'w0_parser_tests'),
    ('scripts/map_intel/validate_map_dataset.py', 'code', 'w0_validator'),
    ('scripts/map_intel/make_dataset_report.py', 'code', 'w0_report_generator'),
]
# manifest first
manifest_rows = []
for rel, _, _ in ARTIFACTS:
    if rel.endswith('SHA256.txt'):
        continue
    path = ROOT / rel
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_rows.append(f'{digest}  {path.stat().st_size}  {rel}')
(DATA / 'SHA256.txt').write_text('\n'.join(manifest_rows) + '\n', encoding='utf-8')

for rel, artifact_type, role in ARTIFACTS:
    store.register_artifact(artifacts_doc, rel, artifact_type=artifact_type, role=role,
                            task_id='TASK-20261101-119', status='VERIFIED',
                            context_version=base)
outputs = [rel for rel, _, _ in ARTIFACTS]
summary = (
    'W0 静态地图解析器与数据集完成（离线只读，未启动游戏/Bridge/采集/训练）。'
    '扫描 557 个 .map/.mpr（test-game/Maps 554 + test-game-wdzs/map 3），解析成功 557/557、失败 0。'
    '产出：maps_index.json（3.67MB 索引）+ validation.json + 3 张完整静态样本 + MAP_DATASET_REPORT.md；'
    '代码 scripts/map_intel/static_map_parser.py（12 项单测 PASS）+ validator + report generator。'
    '统计：Waypoints 14,543（X+1000·Y 编码 VERIFIED，11 条≥512 待核）、Terrain 271,447 对象（0 条越界 0..511；'
    '79.95% 落 182×182 内，大图/编码待实验1核）、Structures 34,456（cell 解析率 99.74%）、Triggers 9,901 / '
    'Events 9,903 / Actions 9,903 / Tags 9,314（tag→trigger 100% 指向存在触发器；孤立 trigger 590）、'
    'AITriggerTypesEnable 73,831。新发现：530/557 地图含 map-embedded 规则/AI 段（TeamTypes 250 图、General 207、'
    'BuildingTypes/VehicleTypes/AircraftTypes 等），需与全局 rulesmd/aimd 区分。2peaks 交叉验证全匹配'
    '（81/104/10/7/7/7/7/163，sha256 dd64cb8b… 与运行环境一致）。UNKNOWN：packed 块（IsoMapPack5/OverlayPack/'
    'OverlayDataPack/PreviewPack）未解码；[Map] Size/[Header] 与运行时 cell 网格关系未知（195/557 图坐标 >182，'
    '须以运行时 map_soa 为准）；.yrm/.mmx/.yro 无解析器。未改 E:\\hj/资产/301/M1。')
store.update_task(tasks_doc, 'TASK-20261101-119', status='COMPLETED',
                  owner='DSH', result_summary=summary, outputs=outputs)
store.save_tasks(tasks_doc)
store.save_artifacts(artifacts_doc)
with store.Lock():
    new_state = store.bump_context(
        state, reason='W0 static map parser + map dataset completed (557 maps parsed, 0 failed)',
        actor='DSH')
new_version = new_state['context_version']
store.regenerate_current_context(new_state)
for rel, artifact_type, role in ARTIFACTS:
    store.register_artifact(artifacts_doc, rel, artifact_type=artifact_type, role=role,
                            task_id='TASK-20261101-119', status='VERIFIED',
                            context_version=new_version)
store.save_artifacts(artifacts_doc)
event = store.append_event(
    'w0_map_dataset_done',
    'W0 完成：557/557 地图解析成功（0 失败）；Waypoints 14,543 / Terrain 271,447 / Structures 34,456（cell 解析率 99.74%）/ '
    'Triggers 9,901 / Events 9,903 / Actions 9,903 / Tags 9,314 / AITriggerTypesEnable 73,831；'
    '530 图含 map-embedded 规则/AI 段；2peaks 交叉验证全匹配（sha256 dd64cb8b…）。'
    'packed 块未解码、[Map] Size 与 cell 网格关系 UNKNOWN（坐标>182 的图 195/557），.yrm 无解析器。'
    '未启动游戏/Bridge/采集/训练，未改资产。',
    actor='DSH',
    payload={'task_id': 'TASK-20261101-119', 'map_count': 557, 'parsed': 557, 'failed': 0,
             'totals': {'waypoints': 14543, 'terrain': 271447, 'structures': 34456,
                        'triggers': 9901, 'events': 9903, 'actions': 9903, 'tags': 9314,
                        'ai_triggers_enabled': 73831},
             'embedded_maps': 530, 'maps_coords_gt_182': 195,
             '2peaks_crosscheck_all_match': True,
             '2peaks_sha256': 'dd64cb8b10d44e489bd21cf62914af4706386f9ee161bf53875fef186c571e6d',
             'unknown': ['packed map sections not decoded', 'Map Size vs runtime cell grid',
                         '.yrm/.mmx/.yro parser missing']},
    context_version=new_version)
print(json.dumps({'base_version': base, 'new_version': new_version,
                  'task': 'TASK-20261101-119', 'artifacts': len(ARTIFACTS),
                  'event': event['event_id']}, ensure_ascii=False, indent=1))
