"""Stage A round metrics from a real training run (offline; no game, no training).

Reads ``reports/rl/<run>`` episode JSONL/result files and reports, per episode
and aggregated:

* attack commands: total, 3s effective, 6s effective (+ rates vs the historical
  22.57% baseline), target switches and ineffective switches,
* kills (truth tracker), first-kill sampling, damage / own losses / exchange,
* target candidate coverage (all decisions and only decisions with visible
  enemies AND at least one combat unit), mean candidates, nearest enemy dist,
* first enemy sight / first damage times,
* focus-fire distribution (per-target damage, top1 share),
* per-episode reward parts, checkpoints + sha256.

Usage:
    python scripts/dsh_rl/stage_a_metrics.py --run reports/rl/stage-A-round1-<stamp> \
        --out reports/stage-A-round1-metrics.json
"""
import argparse
import hashlib
import json
import math
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from dsh_rl import full_spec as S  # noqa: E402

COMPLETED_KINDS = ('defeat', 'win', 'timeout')
# Historical baseline from the 75-episode audit (V3/legacy action space).
BASELINE = {
    'source': 'reports/rl/ppo-shared-20261030-102633 (75 defeat episodes, legacy action space)',
    'attacks_issued': 2689,
    'attacks_effective_6s': 607,
    'attack_effective_rate_6s': 0.2257,
    'kills_total': 110,
    'kills_per_episode': 110 / 75,
    'episodes_with_kill_rate': 37 / 75,
    'damage_per_episode': 502.1,
    'own_hp_lost_per_episode': 3610.7,
    'hp_exchange': 502.1 / 3610.7,
    'own_unit_losses_per_episode': 1201 / 75,
    'first_damage_median_s': 182.31,
    'candidate_coverage_range30': 0.3394,
    'mean_candidates_range30': 2.109,
    'top1_damage_share_mean': 0.34,
    'attack_target_switch_rate': 0.8484,
    'unit_slot_change_rate_legacy': 0.027,
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_index(run):
    index_path = run / 'episode-index.json'
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001 - may be mid-write
            pass
    rows = []
    for p in sorted((run / 'workers').glob('*/episodes/*.result.json')):
        r = json.loads(p.read_text(encoding='utf-8'))
        rows.append(dict(worker=p.parents[1].name, episode_id=r['episode_id'],
                         kind=r['kind'], elapsed_s=r['elapsed_s'], metrics=r['metrics'],
                         outcome_confirmed=r.get('outcome_confirmed', False),
                         learning_excluded=r.get('learning_excluded', False),
                         failure=r.get('failure'), reward_parts=r.get('reward_parts'),
                         result=str(p.relative_to(run))))
    return rows


def parse_episode(path):
    observations, actions = [], []
    cur_t = 0.0
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            kind = rec.get('type')
            if kind == 'observation':
                observations.append(rec)
                cur_t = float(rec.get('t') or 0)
            elif kind == 'action':
                rec['_t'] = cur_t
                actions.append(rec)
    return observations, actions


def episode_analysis(run, entry):
    result_path = run / entry['result']
    jl = pathlib.Path(str(result_path).replace('.result.json', '.jsonl'))
    metrics = entry.get('metrics') or {}
    reward_parts = entry.get('reward_parts') or {}
    observations, actions = parse_episode(jl) if jl.exists() else ([], [])
    attacks = [a for a in actions if a.get('decision_source') == 'ppo' and a.get('action') == 'ATTACK']
    # Damage events from consecutive observations (same identity only).
    damage_events = []  # (t, addr, delta)
    for i in range(1, len(observations)):
        prev, cur = observations[i - 1], observations[i]
        t = float(cur.get('t') or 0)
        pe = {o['addr']: o for o in prev.get('visible_enemies') or []}
        for o in cur.get('visible_enemies') or []:
            po = pe.get(o['addr'])
            if (po and po.get('type_name') == o.get('type_name')
                    and po.get('owner') == o.get('owner')):
                delta = int(po.get('health', 0)) - int(o.get('health', 0))
                if delta > 0:
                    damage_events.append((t, o['addr'], delta))
    times_by_addr = {}
    for t, addr, delta in damage_events:
        times_by_addr.setdefault(addr, []).append(t)

    def effective(attack, window):
        t0 = float(attack.get('_t') or 0)
        return any(t0 <= t <= t0 + window for t in times_by_addr.get(attack.get('target_address'), []))

    eff3 = sum(1 for a in attacks if effective(a, 3.0))
    eff6 = sum(1 for a in attacks if effective(a, 6.0))
    # Target switches + ineffective switches (previous target got no damage in 6s).
    switches = 0
    ineffective_switches = 0
    previous = None
    for attack in attacks:
        if previous is not None:
            switches += 1
            if not previous['effective6']:
                ineffective_switches += 1
        attack['effective6'] = effective(attack, 6.0)
        previous = attack
    # Candidate coverage: replay each PPO decision state with the P2.0 mapping.
    decision_rows = []
    obs_by_order = observations
    obs_index = 0
    for action in actions:
        if action.get('decision_source') != 'ppo':
            continue
        while (obs_index < len(obs_by_order)
               and float(obs_by_order[obs_index].get('t') or 0) <= float(action.get('_t') or 0)):
            obs_index += 1
        row = obs_by_order[obs_index - 1] if obs_index > 0 else None
        if row is None:
            continue
        candidates = S.p20_targets(row, 30.0)
        anchors = len(S._unit_proximity(row))
        visible = len(S._valid_enemy_targets(row))
        decision_rows.append({'candidates': len(candidates), 'visible': visible,
                              'anchors': anchors})
    coverage = (sum(1 for r in decision_rows if r['candidates'] > 0) / len(decision_rows)) if decision_rows else 0.0
    armed = [r for r in decision_rows if r['visible'] > 0 and r['anchors'] > 0]
    coverage_armed = (sum(1 for r in armed if r['candidates'] > 0) / len(armed)) if armed else 0.0
    mean_candidates = (sum(r['candidates'] for r in decision_rows) / len(decision_rows)) if decision_rows else 0.0
    # First enemy sight / first damage.
    first_sight = next((float(o.get('t') or 0) for o in observations
                        if S._valid_enemy_targets(o)), None)
    first_damage = damage_events[0][0] if damage_events else None
    # Own unit losses + per-type counts.
    own_losses = {}
    own_lost_total = 0
    for i in range(1, len(observations)):
        prev, cur = observations[i - 1], observations[i]
        pe = {o['addr']: o for o in prev.get('own_objects') or [] if S.placed(o)}
        cur_addrs = {o['addr'] for o in cur.get('own_objects') or [] if S.placed(o)}
        for addr, o in pe.items():
            if addr in cur_addrs or S.is_building(o) or S.is_mcv(o):
                continue
            name = o.get('type_name', '?')
            own_losses[name] = own_losses.get(name, 0) + 1
            own_lost_total += 1
    # Focus fire: damage per damaged target.
    per_target = {}
    for t, addr, delta in damage_events:
        per_target[addr] = per_target.get(addr, 0) + delta
    damaged = [v for v in per_target.values() if v > 0]
    top1_share = (max(damaged) / sum(damaged)) if damaged else 0.0
    # Unit slot swaps (max cumulative diagnostics from transitions of this episode).
    swaps = 0
    tf = run / 'workers' / str(entry.get('worker')) / 'transitions.jsonl'
    if tf.exists():
        with tf.open(encoding='utf-8') as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get('episode_id') == entry['episode_id']:
                    swaps = max(swaps, int(rec.get('unit_slot_swaps') or 0))
    damage = float(metrics.get('damage_dealt') or 0)
    own_hp = float(metrics.get('own_hp_lost') or 0)
    own_removed = float(metrics.get('truth_own_removed_hp') or 0)
    kills = int(metrics.get('truth_kills_infantry') or 0) + int(metrics.get('truth_kills_vehicle') or 0)
    return {
        'episode_id': entry['episode_id'],
        'worker': entry.get('worker'),
        'kind': entry['kind'],
        'elapsed_s': round(float(entry.get('elapsed_s') or 0), 1),
        'reward_total': round(sum(float(v) for v in reward_parts.values()), 2),
        'reward_parts': {k: round(float(v), 3) for k, v in reward_parts.items() if abs(float(v)) > 1e-9},
        'attacks_issued': len(attacks),
        'attacks_effective_3s': eff3,
        'attacks_effective_6s': eff6,
        'target_switches': switches,
        'ineffective_switches': ineffective_switches,
        'damage_dealt': round(damage, 1),
        'own_hp_lost_damage': round(own_hp, 1),
        'own_removed_hp': round(own_removed, 1),
        'hp_exchange': round(damage / (own_hp + own_removed), 3) if (own_hp + own_removed) else None,
        'kills': kills,
        'kills_infantry': int(metrics.get('truth_kills_infantry') or 0),
        'kills_vehicle': int(metrics.get('truth_kills_vehicle') or 0),
        'building_destroyed': int(metrics.get('truth_building_destroyed') or 0),
        'first_kill_sampled': kills > 0 or float(reward_parts.get('first_kill') or 0) > 0,
        'own_unit_losses': own_lost_total,
        'own_unit_losses_by_type': own_losses,
        'candidate_decisions': len(decision_rows),
        'candidate_coverage': round(coverage, 4),
        'candidate_coverage_armed': round(coverage_armed, 4),
        'mean_candidates': round(mean_candidates, 3),
        'visible_targets_mean': round(sum(r['visible'] for r in decision_rows) / max(1, len(decision_rows)), 3),
        'first_enemy_sight_s': None if first_sight is None else round(first_sight, 1),
        'first_damage_s': None if first_damage is None else round(first_damage, 1),
        'damaged_targets': len(damaged),
        'damage_per_damaged_target': round(sum(damaged) / len(damaged), 1) if damaged else 0,
        'max_single_target_damage': round(max(damaged), 1) if damaged else 0,
        'top1_damage_share': round(top1_share, 3),
        'unit_slot_swaps': swaps,
        'outcome_confirmed': bool(entry.get('outcome_confirmed')),
    }


def aggregate(episodes):
    completed = [e for e in episodes if e['kind'] in COMPLETED_KINDS]
    n = len(completed)
    if n == 0:
        return {'episodes': 0}

    def mean(key, data=None):
        values = [e[key] for e in (data or completed) if e[key] is not None]
        return round(sum(values) / len(values), 3) if values else None

    def total(key):
        return sum(e[key] or 0 for e in completed)

    issued = total('attacks_issued')
    eff6 = total('attacks_effective_6s')
    eff3 = total('attacks_effective_3s')
    kills = total('kills')
    return {
        'episodes': n,
        'kinds': {k: sum(1 for e in completed if e['kind'] == k) for k in COMPLETED_KINDS},
        'elapsed_s_mean': mean('elapsed_s'),
        'attacks_issued_total': issued,
        'attacks_issued_mean': round(issued / n, 2),
        'attacks_effective_3s_total': eff3,
        'attacks_effective_3s_rate': round(eff3 / issued, 4) if issued else None,
        'attacks_effective_6s_total': eff6,
        'attacks_effective_6s_rate': round(eff6 / issued, 4) if issued else None,
        'kills_total': kills,
        'kills_mean': round(kills / n, 2),
        'first_kill_sampled_rate': round(sum(1 for e in completed if e['first_kill_sampled']) / n, 4),
        'building_destroyed_total': total('building_destroyed'),
        'damage_dealt_mean': mean('damage_dealt'),
        'own_hp_lost_damage_mean': mean('own_hp_lost_damage'),
        'own_removed_hp_mean': mean('own_removed_hp'),
        'hp_exchange_mean': mean('hp_exchange'),
        'own_unit_losses_mean': mean('own_unit_losses'),
        'unit_kill_exchange': round(kills / max(1, sum(e['own_unit_losses'] for e in completed)), 3),
        'candidate_coverage_mean': mean('candidate_coverage'),
        'candidate_coverage_armed_mean': mean('candidate_coverage_armed'),
        'mean_candidates_mean': mean('mean_candidates'),
        'visible_targets_mean': mean('visible_targets_mean'),
        'first_enemy_sight_s_median': round(statistics.median(
            [e['first_enemy_sight_s'] for e in completed if e['first_enemy_sight_s'] is not None]), 1)
            if any(e['first_enemy_sight_s'] is not None for e in completed) else None,
        'first_damage_s_median': round(statistics.median(
            [e['first_damage_s'] for e in completed if e['first_damage_s'] is not None]), 1)
            if any(e['first_damage_s'] is not None for e in completed) else None,
        'damaged_targets_mean': mean('damaged_targets'),
        'damage_per_damaged_target_mean': mean('damage_per_damaged_target'),
        'max_single_target_damage_best': max((e['max_single_target_damage'] or 0) for e in completed),
        'top1_damage_share_mean': mean('top1_damage_share'),
        'target_switches_mean': mean('target_switches'),
        'ineffective_switches_mean': mean('ineffective_switches'),
        'unit_slot_swaps_max': max((e['unit_slot_swaps'] or 0) for e in completed),
        'reward_parts_mean': _reward_means(completed),
    }


def _reward_means(episodes):
    keys = set()
    for e in episodes:
        keys.update((e['reward_parts'] or {}).keys())
    out = {}
    for key in sorted(keys):
        values = [float((e['reward_parts'] or {}).get(key) or 0) for e in episodes]
        out[key] = round(sum(values) / len(values), 3)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', required=True)
    ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)
    run = ROOT / a.run if not pathlib.Path(a.run).is_absolute() else pathlib.Path(a.run)
    entries = load_index(run)
    episodes = [episode_analysis(run, entry) for entry in entries]
    completed = [e for e in episodes if e['kind'] in COMPLETED_KINDS]
    checkpoints = []
    for path in sorted(run.glob('*.zip')):
        checkpoints.append({'name': path.name, 'bytes': path.stat().st_size,
                            'sha256': sha256_file(path)})
    payload = {
        'run': str(run.relative_to(ROOT)),
        'generated_at': __import__('time').strftime('%Y-%m-%dT%H:%M:%S'),
        'episodes_recorded': len(entries),
        'episodes_completed': len(completed),
        'partial_episodes': len(entries) - len(completed),
        'aggregate': aggregate(episodes),
        'baseline': BASELINE,
        'episodes': episodes,
        'checkpoints': checkpoints,
    }
    out_path = ROOT / (a.out or 'reports/stage-A-round1-metrics.json')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'run': payload['run'],
                      'episodes_completed': len(completed),
                      'partial': payload['partial_episodes'],
                      'aggregate': payload['aggregate']}, ensure_ascii=False, indent=2))
    print('wrote', out_path)


if __name__ == '__main__':
    main()
