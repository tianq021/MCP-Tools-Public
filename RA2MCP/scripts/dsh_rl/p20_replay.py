"""Offline P2.0 replay over historical episode logs (no game, no training).

Replays the observation/action rows of an old run and reports, for the same
states:

* attack-candidate coverage (legacy base-centre ordering vs P2.0 army ordering,
  and candidate ranges 20/30/40 cells),
* how many historical PPO ATTACK commands would have been allowed/removed,
* target-slot ordering change rate and target switch frequency,
* unit-slot identity stability (legacy re-sort vs P2.0 sticky slots).

Usage:
    python scripts/dsh_rl/p20_replay.py \
        --run reports/rl/ppo-shared-20261030-102633 \
        --out reports/p20-replay.json
"""
import argparse
import json
import math
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from dsh_rl import full_spec as S  # noqa: E402


def load_catalog(worker_dir):
    path = pathlib.Path(worker_dir) / 'public-catalog.json'
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    groups = {k: list(v) for k, v in (data.get('groups') or {}).items()}
    return data['types'], groups, int(data.get('country_index', 0)), int(data.get('tech_level', 10))


def make_specs(loaded):
    types, groups, country, tech = loaded
    legacy = S.FullControlSpec(types, groups, country, tech,
                               action_space_version=S.LEGACY_ACTION_SPACE_VERSION)
    modern = S.FullControlSpec(types, groups, country, tech,
                               action_space_version=S.ACTION_SPACE_VERSION,
                               target_candidate_range=30.0)
    return legacy, modern


def nearest_distance(row, target):
    anchors = S._unit_proximity(row)
    if not anchors:
        return None
    return min(math.dist(anchor, target['cell']) for anchor in anchors)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', default='reports/rl/ppo-shared-20261030-102633')
    ap.add_argument('--out', default='reports/p20-replay.json')
    a = ap.parse_args(argv)
    run = ROOT / a.run
    index = json.loads((run / 'episode-index.json').read_text(encoding='utf-8'))
    episodes = [e for e in index if e['kind'] == 'defeat']

    specs = {}
    for worker_dir in (run / 'workers').iterdir():
        loaded = load_catalog(worker_dir)
        if loaded:
            specs[worker_dir.name] = make_specs(loaded)

    agg = {
        'episodes': 0, 'obs_frames': 0, 'ppo_decisions': 0,
        'with_candidate': {'legacy': 0, 'range20': 0, 'range30': 0, 'range40': 0},
        'candidate_count': {'legacy': 0, 'range20': 0, 'range30': 0, 'range40': 0},
        'legacy_slot_candidates': 0, 'legacy_slots_beyond_30': 0,
        'historical_attacks': 0,
        'attack_target_in_legacy_slots': 0, 'attack_target_in_modern_slots': 0,
        'attack_target_within_20': 0, 'attack_target_within_30': 0, 'attack_target_within_40': 0,
        'effective_attacks_baseline': 0,
        'kept_attacks': 0, 'kept_effective': 0,
        'removed_attacks': 0, 'removed_effective': 0,
        'ordering_diff_decisions': 0, 'target_list_changes': 0,
        'attack_target_switches': 0, 'attack_pairs': 0,
        'unit_frames': 0, 'legacy_unit_survivors': 0,
        'legacy_slot_changes': 0, 'modern_slot_changes': 0,
        'episodes_with_combat_units': 0,
    }
    per_episode = []
    for e in episodes:
        worker = e['worker']
        if worker not in specs:
            continue
        legacy, modern = specs[worker]
        # Sticky slots are per-episode state; rebuild both specs per episode.
        legacy, modern = make_specs(load_catalog(run / 'workers' / worker))
        jl = pathlib.Path(str(run / e['result']).replace('.result.json', '.jsonl'))
        if not jl.exists():
            continue
        agg['episodes'] += 1
        row = None
        prev_attack_target = None
        prev_modern_order = None
        prev_legacy_identities = None
        prev_modern_slots = None
        ep = {'episode_id': e['episode_id'], 'decisions': 0, 'historical_attacks': 0,
              'attack_target_in_modern': 0, 'attack_target_within_30': 0,
              'legacy_candidates': 0, 'modern_candidates': 0, 'combat_anchors': 0,
              'effective_attacks': 0, 'kept_attacks': 0, 'kept_effective': 0,
              'removed_attacks': 0, 'removed_effective': 0}
        observations = []
        attacks = []
        with jl.open(encoding='utf-8') as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                kind = rec.get('type')
                if kind == 'observation':
                    row = rec
                    observations.append(rec)
                    agg['obs_frames'] += 1
                    legacy_identities = [S.unit_identity(o) for o in legacy.units(row)]
                    modern_identities = [S.unit_identity(o) for o in modern.units(row)]
                    if prev_legacy_identities is not None:
                        previous = {identity: i for i, identity in enumerate(prev_legacy_identities)}
                        for index_, identity in enumerate(legacy_identities):
                            if identity in previous and previous[identity] != index_:
                                agg['legacy_slot_changes'] += 1
                        survivors = set(prev_legacy_identities) & set(legacy_identities)
                        agg['legacy_unit_survivors'] += len(survivors)
                        if prev_modern_slots is not None:
                            previous_slots = {identity: i for i, identity in
                                              enumerate(prev_modern_slots) if identity is not None}
                            current_slots = {identity: i for i, identity in
                                             enumerate(modern.unit_slot_identities)
                                             if identity is not None}
                            for identity in survivors:
                                if identity in previous_slots and identity in current_slots:
                                    if previous_slots[identity] != current_slots[identity]:
                                        agg['modern_slot_changes'] += 1
                        agg['unit_frames'] += 1
                    prev_legacy_identities = legacy_identities
                    prev_modern_slots = list(modern.unit_slot_identities)
                    continue
                if kind != 'action' or rec.get('decision_source') != 'ppo':
                    continue
                if row is None:
                    continue
                anchors = S._unit_proximity(row)
                if anchors:
                    ep['combat_anchors'] += 1
                legacy_targets = legacy.targets(row)
                modern_targets = modern.targets(row)
                counts = {'legacy': len(legacy_targets), 'range20': 0, 'range30': len(modern_targets),
                          'range40': 0}
                for radius, label in ((20.0, 'range20'), (40.0, 'range40')):
                    counts[label] = len(S.p20_targets(row, radius))
                for label, count in counts.items():
                    if count:
                        agg['with_candidate'][label] += 1
                    agg['candidate_count'][label] += count
                agg['ppo_decisions'] += 1
                ep['decisions'] += 1
                ep['legacy_candidates'] += len(legacy_targets)
                ep['modern_candidates'] += len(modern_targets)
                agg['legacy_slot_candidates'] += len(legacy_targets)
                for target in legacy_targets:
                    nearest = min(math.dist(anchor, target['cell']) for anchor in anchors) if anchors else None
                    if nearest is not None and nearest > 30.0:
                        agg['legacy_slots_beyond_30'] += 1
                legacy_order = [t['addr'] for t in legacy_targets]
                modern_order = [t['addr'] for t in modern_targets]
                if legacy_order != modern_order:
                    agg['ordering_diff_decisions'] += 1
                if prev_modern_order is not None and modern_order != prev_modern_order:
                    agg['target_list_changes'] += 1
                prev_modern_order = modern_order
                if rec.get('action') == 'ATTACK':
                    target_addr = rec.get('target_address')
                    agg['historical_attacks'] += 1
                    ep['historical_attacks'] += 1
                    in_modern = target_addr in modern_order
                    attacks.append({'t': row.get('t'), 'target': target_addr,
                                    'kept': in_modern})
                    if target_addr in legacy_order:
                        agg['attack_target_in_legacy_slots'] += 1
                    if in_modern:
                        agg['attack_target_in_modern_slots'] += 1
                        ep['attack_target_in_modern'] += 1
                    visible = S._valid_enemy_targets(row)
                    target = next((t for t in visible if t['addr'] == target_addr), None)
                    nearest = nearest_distance(row, target) if target is not None else None
                    if nearest is not None:
                        for radius in (20.0, 30.0, 40.0):
                            if nearest <= radius:
                                agg[f'attack_target_within_{int(radius)}'] += 1
                                if radius == 30.0:
                                    ep['attack_target_within_30'] += 1
                    if prev_attack_target is not None:
                        agg['attack_pairs'] += 1
                        if target_addr != prev_attack_target:
                            agg['attack_target_switches'] += 1
                    prev_attack_target = target_addr
        # Effectiveness is derived from consecutive observations: an ATTACK is
        # effective when its target takes damage within 6 real seconds.
        damage_times = defaultdict(list)
        for i in range(1, len(observations)):
            previous, current = observations[i - 1], observations[i]
            t = float(current.get('t') or 0)
            pe = {o['addr']: o for o in (previous.get('visible_enemies') or [])}
            ce = {o['addr']: o for o in (current.get('visible_enemies') or [])}
            for addr, co in ce.items():
                po = pe.get(addr)
                if (po and po.get('type_name') == co.get('type_name')
                        and po.get('owner') == co.get('owner')
                        and int(po.get('health', 0)) - int(co.get('health', 0)) > 0):
                    damage_times[addr].append(t)
        for attack in attacks:
            t0 = float(attack.get('t') or 0)
            effective = any(t0 <= t <= t0 + 6.0 for t in damage_times.get(attack['target'], []))
            if effective:
                agg['effective_attacks_baseline'] += 1
                ep['effective_attacks'] += 1
            if attack['kept']:
                agg['kept_attacks'] += 1
                ep['kept_attacks'] += 1
                if effective:
                    agg['kept_effective'] += 1
                    ep['kept_effective'] += 1
            else:
                agg['removed_attacks'] += 1
                ep['removed_attacks'] += 1
                if effective:
                    agg['removed_effective'] += 1
                    ep['removed_effective'] += 1
        if ep['combat_anchors']:
            agg['episodes_with_combat_units'] += 1
        per_episode.append(ep)

    decisions = max(1, agg['ppo_decisions'])
    attacks = max(1, agg['historical_attacks'])
    survivors = max(1, agg['legacy_unit_survivors'])
    out = {
        'source_run': str(run.relative_to(ROOT)),
        'episodes': agg['episodes'],
        'obs_frames': agg['obs_frames'],
        'ppo_decisions': agg['ppo_decisions'],
        'episodes_with_combat_units': agg['episodes_with_combat_units'],
        'coverage': {label: round(agg['with_candidate'][label] / decisions, 4)
                     for label in ('legacy', 'range20', 'range30', 'range40')},
        'mean_candidates': {label: round(agg['candidate_count'][label] / decisions, 3)
                            for label in ('legacy', 'range20', 'range30', 'range40')},
        'legacy_slot_candidates': agg['legacy_slot_candidates'],
        'legacy_slots_beyond_30': agg['legacy_slots_beyond_30'],
        'legacy_slots_beyond_30_rate': round(
            agg['legacy_slots_beyond_30'] / max(1, agg['legacy_slot_candidates']), 4),
        'historical_attacks': agg['historical_attacks'],
        'attack_target_in_legacy_slots_rate': round(agg['attack_target_in_legacy_slots'] / attacks, 4),
        'attack_target_in_modern_slots_rate': round(agg['attack_target_in_modern_slots'] / attacks, 4),
        'attack_target_within_radius': {
            '20': round(agg['attack_target_within_20'] / attacks, 4),
            '30': round(agg['attack_target_within_30'] / attacks, 4),
            '40': round(agg['attack_target_within_40'] / attacks, 4)},
        'attack_effectiveness': {
            'baseline_total': agg['historical_attacks'],
            'baseline_effective': agg['effective_attacks_baseline'],
            'baseline_rate': round(agg['effective_attacks_baseline'] / attacks, 4),
            'kept_total': agg['kept_attacks'],
            'kept_effective': agg['kept_effective'],
            'kept_rate': round(agg['kept_effective'] / max(1, agg['kept_attacks']), 4),
            'removed_total': agg['removed_attacks'],
            'removed_effective': agg['removed_effective'],
            'removed_rate': round(agg['removed_effective'] / max(1, agg['removed_attacks']), 4),
            'note': '静态回放：历史动作保持不变，只按新候选列表过滤；真实策略会随掩码改变，最终有效率需重训/冒烟验证',
        },
        'ordering_diff_rate': round(agg['ordering_diff_decisions'] / decisions, 4),
        'target_list_change_rate': round(agg['target_list_changes'] / decisions, 4),
        'attack_target_switch_rate': round(agg['attack_target_switches'] / max(1, agg['attack_pairs']), 4),
        'unit_frames': agg['unit_frames'],
        'legacy_unit_survivors': agg['legacy_unit_survivors'],
        'legacy_unit_slot_change_rate': round(agg['legacy_slot_changes'] / survivors, 4),
        'modern_unit_slot_change_rate': round(agg['modern_slot_changes'] / survivors, 4),
        'notes': [
            'historical ATTACK commands were generated under the legacy action space',
            '20/30/40 is an attack candidate filter radius in cells; it is not a weapon range and not a path reachability test',
            'modern unit slots are sticky; their change rate is expected to be 0 for surviving units',
        ],
        'per_episode': per_episode,
    }
    out_path = ROOT / a.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    brief = {k: v for k, v in out.items() if k != 'per_episode'}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    print('wrote', out_path)


if __name__ == '__main__':
    main()
