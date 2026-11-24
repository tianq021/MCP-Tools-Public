"""分析「上帝的玩具」采集数据：有效段、建造顺序、兵种配比、战斗波次、可学习性估算。

只读；不启动游戏。输出 analysis/summary.json + analysis/report.md。
用法：python scripts/ai_teacher/analyze_godstoy_recording.py <采集目录>
"""
import json
import pathlib
import statistics
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(r'E:\RA2MCP')
BUILDING_HINTS = ('Barracks', 'Factory', 'Refinery', 'Reactor', 'Power Plant', 'Tower',
                  'Bunker', 'Pill Box', 'Pillbox', 'Depot', 'Radar', 'Air Force',
                  'Lab', 'Shipyard', 'Construction Yard', 'Ore Processor', 'Silo',
                  'Battle Lab', 'Tank Bunker', 'Sentry Gun', 'Flak Cannon', 'Patriot',
                  'Tesla Coil', 'Prism Tower', 'Gatling', 'Grinder', 'Cloning', 'Cloning Vats')
COMBAT_MISSIONS = {1: 'attack', 2: 'move', 3: 'qmove', 25: 'patrol'}
GUARD_MISSIONS = {5: 'guard', 11: 'area_guard'}
MISSION_NAMES = {
    -1: 'limbo', 0: 'sleep', 1: 'attack', 2: 'move', 3: 'qmove', 4: 'retreat',
    5: 'guard', 6: 'sticky', 7: 'enter', 8: 'capture', 9: 'eaten', 10: 'harvest',
    11: 'area_guard', 12: 'return', 13: 'stop', 14: 'ambush', 15: 'hunt',
    16: 'unload', 17: 'sabotage', 18: 'construction', 19: 'deconstruct',
    20: 'repair', 21: 'rescue', 22: 'missile', 23: 'harmless', 24: 'open',
    25: 'patrol', 27: 'deploy', 31: '?31',
}


def is_building(name):
    return any(h.lower() in name.lower() for h in BUILDING_HINTS)


def main():
    arg = pathlib.Path(sys.argv[1])
    out_dir = (arg if arg.is_absolute() else ROOT / arg).resolve()
    f = out_dir / 'states_full.jsonl'
    samples = []
    dup = 0
    last_frame = None
    for line in f.open(encoding='utf-8', errors='replace'):
        try:
            s = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if last_frame is not None and s['frame'] <= last_frame:
            dup += 1
            continue
        last_frame = s['frame']
        samples.append(s)
    houses = {h[0]: h[1] for h in samples[-1]['houses']}
    fps = (samples[-1]['frame'] - samples[0]['frame']) / max(1e-9, samples[-1]['wall'] - samples[0]['wall'])
    t0 = samples[0]['wall']
    t1 = samples[-1]['wall']
    print(f'有效样本={len(samples)} 去重丢弃={dup} 帧 {samples[0]["frame"]}->{samples[-1]["frame"]} '
          f'时长={t1 - t0:.1f}s fps≈{fps:.1f}')

    # 时间轴：每样本按 key 聚合
    timeline = []
    for s in samples:
        t = s['wall'] - t0
        owners = Counter()
        types_by_owner = defaultdict(Counter)
        missions = Counter()
        for o in s['objects']:
            owner, kind, hp, x, y, on_map, in_limbo, mission = o
            owners[owner] += 1
            types_by_owner[owner][kind] += 1
            missions[mission] += 1
        timeline.append({'t': round(t, 2), 'frame': s['frame'], 'objects': len(s['objects']),
                         'owners': dict(owners),
                         'missions': {MISSION_NAMES.get(k, str(k)): v for k, v in missions.items()},
                         'types': {str(k): dict(v) for k, v in types_by_owner.items()},
                         'money': {h[0]: h[2] for h in s['houses']},
                         'defeated': {h[0]: h[5] for h in s['houses']}})

    # 建造顺序：建筑 (owner, kind, cell) 首次出现
    seen = set()
    build_orders = defaultdict(list)
    for idx, s in enumerate(samples):
        t = s['wall'] - t0
        for o in s['objects']:
            owner, kind, hp, x, y, on_map, in_limbo, mission = o
            if not is_building(kind) or not on_map:
                continue
            key = (owner, kind, x, y)
            if key in seen:
                continue
            seen.add(key)
            build_orders[owner].append([kind, round(t, 1), x, y])
    for owner in sorted(build_orders):
        build_orders[owner].sort(key=lambda r: r[1])

    # 兵种配比随时间（每 60s 快照）
    snapshots = []
    bounds = [0, 60, 120, 180, 240, 300, 360, 420, 480]
    for lo, hi in zip(bounds, bounds[1:] + [10 ** 9]):
        seg = [s for s in samples if lo <= s['wall'] - t0 < hi]
        if not seg:
            continue
        s = seg[-1]
        comp = defaultdict(Counter)
        for o in s['objects']:
            owner, kind = o[0], o[1]
            if not is_building(kind) and kind not in ('Bridge repair hut',) and 'Miami Hotel' not in kind:
                comp[owner][kind] += 1
        snapshots.append({'t': round(s['wall'] - t0, 0),
                          'combat_units': {str(k): dict(v.most_common(8)) for k, v in comp.items()}})

    # 战斗/推进：每 10s 每 owner 的 mission 分布 + 单位平均位置
    waves = []
    win = 10
    max_t = samples[-1]['wall'] - t0
    t = 0.0
    while t < max_t:
        seg = [s for s in samples if t <= s['wall'] - t0 < t + win]
        if seg:
            s = seg[-1]
            per = defaultdict(lambda: {'attack': 0, 'move': 0, 'guard': 0, 'total': 0,
                                       'cx': 0.0, 'cy': 0.0, 'n': 0})
            for o in s['objects']:
                owner, kind, hp, x, y, on_map, in_limbo, mission = o
                if is_building(kind) or not on_map or in_limbo:
                    continue
                p = per[owner]
                p['total'] += 1
                if mission in COMBAT_MISSIONS:
                    p['attack' if mission == 1 else 'move'] += 1
                elif mission in GUARD_MISSIONS:
                    p['guard'] += 1
                p['cx'] += x
                p['cy'] += y
                p['n'] += 1
            merged = {}
            for owner, p in per.items():
                merged[str(owner)] = {
                    'total': p['total'], 'attack': p['attack'], 'move': p['move'],
                    'guard': p['guard'],
                    'center': [round(p['cx'] / p['n'], 1), round(p['cy'] / p['n'], 1)] if p['n'] else None}
            waves.append({'t': round(t, 0), 'owners': merged})
        t += win

    result = {
        'source': str(out_dir.relative_to(ROOT)),
        'valid_samples': len(samples), 'duplicate_samples_dropped': dup,
        'frames': [samples[0]['frame'], samples[-1]['frame']],
        'wall_seconds': round(t1 - t0, 1), 'fps_estimate': round(fps, 1),
        'houses': houses,
        'build_orders': {str(k): v for k, v in build_orders.items()},
        'snapshots': snapshots,
        'waves': waves,
        'final_houses': samples[-1]['houses'],
        'timeline_tail': timeline[-3:],
    }
    ana = out_dir / 'analysis'
    ana.mkdir(exist_ok=True)
    (ana / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                      encoding='utf-8')

    # 报告
    lines = ['# 「上帝的玩具」采集数据分析（自动生成）', '',
             f'- 有效样本：{len(samples)}（去重丢弃 {dup} 个暂停帧）',
             f'- 帧：{samples[0]["frame"]} → {samples[-1]["frame"]}，时长 {result["wall_seconds"]}s，约 {fps:.1f} fps',
             f'- 阵营：' + '；'.join(f'{i}={n}' for i, n in houses.items()), '',
             '## 建造顺序（首次出现，秒）']
    for owner in sorted(build_orders):
        rows = build_orders[owner]
        lines.append(f'### owner {owner} ({houses.get(owner)}) — {len(rows)} 个建筑')
        for kind, t, x, y in rows[:20]:
            lines.append(f'- {t:7.1f}s  {kind}  @({x},{y})')
        if len(rows) > 20:
            lines.append(f'- …（共 {len(rows)}）')
        lines.append('')
    lines.append('## 兵种配比快照')
    for snap in snapshots:
        lines.append(f'### t≈{snap["t"]:.0f}s')
        for owner, comp in snap['combat_units'].items():
            lines.append(f'- owner {owner}: ' + ', '.join(f'{k}×{v}' for k, v in comp.items()))
        lines.append('')
    lines.append('## 进攻/移动波次（每 10s）')
    lines.append('| t(s) | ' + ' | '.join(f'owner {i}' for i in sorted(houses) if i in (0, 1, 2, 3)) + ' |')
    lines.append('|---|' + '---|' * 4)
    for w in waves:
        cells = []
        for i in (0, 1, 2, 3):
            p = w['owners'].get(str(i))
            if not p:
                cells.append('-')
            else:
                cells.append(f"{p['total']}u A{p['attack']} M{p['move']} G{p['guard']} @{p['center']}")
        lines.append(f'| {w["t"]:.0f} | ' + ' | '.join(cells) + ' |')
    (ana / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print('wrote', ana / 'summary.json', 'and', ana / 'report.md')
    print('\n建造顺序概览:')
    for owner in sorted(build_orders):
        print(f'  owner {owner} ({houses.get(owner)}): ' +
              ', '.join(f'{k}@{t:.0f}s' for k, t, _, _ in build_orders[owner][:10]))


if __name__ == '__main__':
    main()
