"""汇总 V2 训练 run 的结果（报告数据来源，只读）。

用法：
  python summarize_build_v2.py <run_dir> [<run_dir2> ...]
输出：<run_dir>/v2_summary.json（每个 run 一份）+ 控制台摘要。
"""
import csv
import json
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return default


def summarize(run):
    run = pathlib.Path(run)
    summary = {'run_dir': str(run), 'exists': run.exists()}
    if not run.exists():
        return summary
    r = load_json(run / 'run.json', {})
    summary['run'] = {k: r.get(k) for k in (
        'status', 'stop_reason', 'error', 'budget_initial', 'budget_final',
        'extended_rounds', 'timesteps_before', 'timesteps_after',
        'real_transitions_this_run', 'optimizer_updates_this_run',
        'parameter_l2_change_from_init', 'parameters_updated',
        'save_reload_weights_identical', 'reward_version', 'model_sha256',
        'episode_count', 'cumulative_rewards', 'verdict', 'compatibility')}
    summary['model'] = r.get('model')
    summary['diagnosis'] = r.get('diagnosis')
    summary['last_eval'] = r.get('last_eval')
    # episodes
    eps = []
    ep_dir = run / 'episodes'
    if ep_dir.exists():
        for p in sorted(ep_dir.glob('*.result.json')):
            d = load_json(p, {})
            m = d.get('metrics') or {}
            eps.append({
                'episode_id': d.get('episode_id'), 'kind': d.get('kind'),
                'reward_total': d.get('reward_total'),
                'rows': d.get('rows'),
                'setup_success': m.get('setup_success'),
                'setup_duration_s': m.get('setup_duration_s'),
                'buildings_awarded': m.get('buildings_awarded'),
                'units_awarded': m.get('units_awarded'),
                'building_types_awarded': m.get('building_types_awarded'),
                'unit_types_awarded': m.get('unit_types_awarded'),
                'building_reward_total': m.get('building_reward_total'),
                'unit_reward_total': m.get('unit_reward_total'),
                'elapsed_s': d.get('elapsed_s'),
                'is_crash': d.get('is_crash'),
            })
    summary['episodes'] = eps
    btypes, utypes = Counter(), Counter()
    totals = {'buildings': 0, 'units': 0, 'building_reward': 0.0, 'unit_reward': 0.0}
    for e in eps:
        btypes.update(e.get('building_types_awarded') or {})
        utypes.update(e.get('unit_types_awarded') or {})
        totals['buildings'] += int(e.get('buildings_awarded') or 0)
        totals['units'] += int(e.get('units_awarded') or 0)
        totals['building_reward'] += float(e.get('building_reward_total') or 0)
        totals['unit_reward'] += float(e.get('unit_reward_total') or 0)
    summary['episode_totals'] = totals
    summary['building_types_total'] = dict(btypes)
    summary['unit_types_total'] = dict(utypes)
    summary['building_types_distinct'] = len([k for k, v in btypes.items() if v > 0])
    summary['unit_types_distinct'] = len([k for k, v in utypes.items() if v > 0])
    # metrics csv
    metrics = []
    mpath = run / 'build_training_v2_metrics.csv'
    if mpath.exists():
        with mpath.open(encoding='utf-8') as fh:
            metrics = list(csv.DictReader(fh))
    summary['metrics_rows'] = len(metrics)
    keep = ('timesteps', 'steps_this_run', 'episodes_completed', 'current_buildings',
            'current_units', 'cum_buildings_awarded', 'cum_units_awarded',
            'cum_building_reward', 'cum_unit_reward', 'action_noop', 'action_produce',
            'action_place', 'action_unit_control', 'window_buildings', 'window_units',
            'entropy_loss', 'explained_variance', 'param_l2_from_init',
            'param_l2_since_prev', 'mask_produce_allowed', 'mask_place_allowed',
            'eval_decision')
    summary['eval_table'] = [{k: m.get(k) for k in keep} for m in metrics]
    # events
    events = []
    epath = run / 'build_training_v2_events.jsonl'
    if epath.exists():
        for line in epath.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    summary['events_count'] = len(events)
    summary['event_kinds'] = dict(Counter(e.get('event') for e in events))
    summary['build_events'] = [e for e in events
                               if e.get('event') == 'entity_created']
    summary['setup_events'] = [e for e in events if str(e.get('source')) == 'setup'
                               or str(e.get('event', '')).startswith('setup_')]
    # resource
    resource = {}
    mon = run / 'monitor' / 'monitor_summary.json'
    if mon.exists():
        resource['summary'] = load_json(mon, {})
    csvp = run / 'monitor' / 'build_monitor.csv'
    if csvp.exists():
        with csvp.open(encoding='utf-8') as fh:
            rows = list(csv.DictReader(fh))
        resource['samples'] = len(rows)

        def series(key):
            out = []
            for row in rows:
                try:
                    out.append(float(row[key]))
                except Exception:  # noqa: BLE001
                    continue
            return out

        def stat(key):
            vals = series(key)
            if not vals:
                return None
            return {'min': round(min(vals), 1), 'max': round(max(vals), 1),
                    'first': round(vals[0], 1), 'last': round(vals[-1], 1),
                    'mean': round(sum(vals) / len(vals), 1)}
        resource['series_stats'] = {k: stat(k) for k in (
            'phys_avail_mb', 'commit_used_mb', 'commit_pct', 'trainer_private_mb',
            'game_private_mb', 'gpu_used_mb', 'adapter_dedicated_mb',
            'adapter_shared_mb', 'commit_growth_120s_mb')}
    summary['resource'] = resource
    # crash logs
    crash_dir = run / 'crash-logs'
    summary['crash_logs'] = [p.name for p in crash_dir.glob('*')] if crash_dir.exists() else []
    # checkpoints
    ck = run / 'build_training_v2_model' / 'checkpoints'
    summary['checkpoints'] = sorted(p.name for p in ck.glob('*.zip')) if ck.exists() else []
    summary['model_files'] = sorted(
        str(p.relative_to(run)) for p in (run / 'build_training_v2_model').rglob('*.zip')
    ) if (run / 'build_training_v2_model').exists() else []
    out = run / 'v2_summary.json'
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    summary['saved_to'] = str(out)
    return summary


def main():
    runs = sys.argv[1:]
    if not runs:
        runs = [str(p) for p in sorted(
            (ROOT / 'experiments' / 'build_training_v2').glob('*build_v2*'))]
    for run in runs:
        s = summarize(run)
        print('=' * 100)
        print('RUN:', s.get('run_dir'))
        print('status:', (s.get('run') or {}).get('status'),
              '| transitions:', (s.get('run') or {}).get('real_transitions_this_run'),
              '| eps:', len(s.get('episodes') or []))
        print('episode_totals:', s.get('episode_totals'))
        print('building types distinct:', s.get('building_types_distinct'),
              s.get('building_types_total'))
        print('unit types distinct:', s.get('unit_types_distinct'),
              s.get('unit_types_total'))
        print('building behavior learned:', (s.get('run') or {}).get('verdict'))
        if s.get('resource', {}).get('series_stats'):
            print('resource stats:', json.dumps(
                s['resource']['series_stats'], ensure_ascii=False))
        print('saved:', s.get('saved_to'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
