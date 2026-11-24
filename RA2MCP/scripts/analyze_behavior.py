"""Behaviour analysis: is the model scouting / attacking or just wandering?

Reads recorded episode jsonl + transitions from a finished training run and
prints movement, exploration and attack statistics.
"""
import json
import math
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts/dsh_train'))
from dsh_rl import full_spec as F  # noqa: E402


def decode_action(action):
    """Return a human-readable category for a full-control action id."""
    if action == 0:
        return 'NOOP'
    if action == 1:
        return 'DEPLOY'
    if F.PRODUCE_START <= action < F.PLACE_START:
        return 'PRODUCE'
    if F.PLACE_START <= action < F.UNIT_START:
        return 'PLACE'
    if not F.UNIT_START <= action < F.ACTION_N:
        return 'UNKNOWN'
    selection, command = divmod(action - F.UNIT_START, F.UNIT_ACTIONS)
    if command == 0:
        return 'STOP'
    if 1 <= command <= 8:
        return 'MOVE_LOCAL'
    if 9 <= command < 9 + F.TARGET_SLOTS:
        return 'ATTACK'
    if command == 9 + F.TARGET_SLOTS:
        return 'SCOUT'
    return 'RETREAT'


def load_transitions(run):
    buckets = defaultdict(Counter)
    with (run / 'transitions.jsonl').open(encoding='utf-8-sig', errors='replace') as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            episode = (record.get('episode_id') or '?')[-6:]
            buckets[episode][decode_action(int(record.get('action', 0)))] += 1
    return buckets


def analyse_episode(path):
    observations = []
    with path.open(encoding='utf-8-sig', errors='replace') as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('type') == 'observation' and row.get('stage') == 'STAGE_INGAME':
                observations.append(row)
    if len(observations) < 5:
        return None
    base = None
    tracks = defaultdict(list)
    mapped_over_time = []
    enemies_over_time = []
    for row in observations:
        own = row.get('own_objects') or []
        for obj in own:
            kind = str(obj.get('abstract') or '')
            if 'BUILDING' in kind and 'Construction Yard' in obj.get('type_name', ''):
                if base is None:
                    base = tuple(obj.get('cell') or (0, 0))
            if 'BUILDING' not in kind:
                tracks[int(obj.get('addr', 0))].append((tuple(obj.get('cell') or (0, 0)), row.get('frame')))
        mapped_over_time.append(int((row.get('map') or {}).get('mapped') or 0))
        enemies_over_time.append(len(row.get('visible_enemies') or []))
    if base is None:
        base = (0.0, 0.0)

    unit_stats = []
    for addr, track in tracks.items():
        if len(track) < 5:
            continue
        path = 0.0
        max_dist = 0.0
        cells = set()
        for (previous, _), (current, _) in zip(track, track[1:]):
            path += math.dist(previous, current)
        for cell, _ in track:
            cells.add(cell)
            max_dist = max(max_dist, math.dist(cell, base))
        net = math.dist(track[0][0], track[-1][0])
        unit_stats.append({'addr': addr, 'samples': len(track), 'path': round(path, 1),
                           'unique_cells': len(cells), 'max_from_base': round(max_dist, 1),
                           'straightness': round(net / path, 2) if path > 0 else 0.0})
    unit_stats.sort(key=lambda item: item['samples'], reverse=True)
    return {
        'samples': len(observations),
        'duration_s': round(observations[-1].get('t', 0) - observations[0].get('t', 0), 1),
        'base': base,
        'mapped': mapped_over_time,
        'enemies': enemies_over_time,
        'units': unit_stats[:4],
    }


def main():
    run = None
    if len(sys.argv) > 1:
        run = pathlib.Path(sys.argv[1])
    else:
        active = json.loads((ROOT / 'reports/rl/active-run.json').read_text(encoding='utf-8-sig'))
        run = pathlib.Path(active['run_dir'])
    episodes = sorted(run.glob('episodes/*.jsonl'), key=lambda p: p.stat().st_mtime)
    print('run', run.name)
    if not episodes:
        print('no finished episode jsonl in this run; pass an older run path')
        return 1
    actions = load_transitions(run) if (run / 'transitions.jsonl').exists() else {}
    for episode in episodes[-3:]:
        short = episode.name.split('-')[-1][:6]
        stats = analyse_episode(episode)
        print('=' * 60)
        print('episode', short, 'action buckets:', dict(actions.get(short, {})))
        if not stats:
            print('  not enough observations')
            continue
        mapped = stats['mapped']
        enemies = stats['enemies']
        print(f"duration={stats['duration_s']}s samples={stats['samples']} base={stats['base']}")
        print(f"map mapped: start={mapped[0]} end={mapped[-1]} "
              f"max={max(mapped)} growth_last_half={mapped[-1] - mapped[len(mapped) // 2]}")
        first_enemy = next((index for index, value in enumerate(enemies) if value > 0), None)
        print(f"visible enemies: max={max(enemies)} first_seen_sample={first_enemy} "
              f"fraction_visible={sum(1 for value in enemies if value > 0) / len(enemies):.2f}")
        for unit in stats['units']:
            print(f"  unit {unit['addr']}: samples={unit['samples']} path={unit['path']} "
                  f"unique_cells={unit['unique_cells']} max_from_base={unit['max_from_base']} "
                  f"straightness={unit['straightness']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
