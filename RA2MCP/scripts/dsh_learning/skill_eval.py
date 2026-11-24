"""技能评测 CLI/模块（V2.2 record schema）：离线只读评测既有 run 轨迹。

输出每条记录带：model_sha256、eval_protocol_hash、thresholds_version+hash、
data_source（每文件 SHA256）、source_manifest_sha256、generated_at、episodes 计数。
"""
import argparse
import hashlib
import json
import pathlib
import statistics
import sys
import time

from dsh_learning import common, skill_metrics, trace_parser

SCHEMA_VERSION = 'v2.2-skill-record'
EVAL_CONFIG = {
    'map': '2peaks', 'difficulty': 'easy', 'credits': 20000, 'units': 8,
    'superweapons': False, 'control_mode': 'full', 'episode_seconds': 300,
    'decision_seconds': 0.5,
}
METRIC_KEYS = [
    'first_attack_command_s', 'first_effective_attack_s', 'first_damage_dealt_s',
    'attacks_per_min', 'attack_effective6_rate', 'order_repeat_rate',
    'pre_contact_invalid_orders', 'multi_unit_order_share',
    'multi_unit_effective_share', 'close_multi_unit_effective_share',
    'attribution_unconfirmed_share', 'attack_concentration', 'top1_damage_share',
    'kills_per_1000_unit_damage', 'kills_per_effective_attack', 'hp_exchange',
    'own_losses_per_min', 'retreat_opportunities', 'retreat_executed',
    'retreat_execution_rate', 'preserved_unit_ratio', 'first_new_enemy_building_s',
    'map_coverage_final', 'gaps_gt5s', 'gap_max_s',
]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.fmean(values), 4) if values else None


def _median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 4) if values else None


def _run_metadata(run_dir):
    run_json = run_dir / 'run.json'
    meta = {}
    if run_json.exists():
        try:
            meta = json.loads(run_json.read_text(encoding='utf-8-sig'))
        except Exception:  # noqa: BLE001
            meta = {}
    if not meta and (run_dir / 'evaluation.json').exists():
        # P3 固定评测产物：eval_fixed.py 写 evaluation.json（无 run.json）
        try:
            evaluation = json.loads((run_dir / 'evaluation.json').read_text(encoding='utf-8-sig'))
        except Exception:  # noqa: BLE001
            evaluation = {}
        meta = {
            'status': 'complete',
            'resume_model': evaluation.get('model'),
            'reward_version': (evaluation.get('eval_protocol') or {}).get('reward_version'),
            'action_space_version': 'full_ground_v2p1',
            'control_mode': 'full',
            'parameters': {'workers': evaluation.get('workers'),
                           'episode_seconds': evaluation.get('episode_seconds'),
                           'decision_seconds': 0.5},
            'evaluation': evaluation,
        }
    model_path = meta.get('resume_model') or None
    model_sha = None
    if model_path:
        candidate = pathlib.Path(model_path)
        if not candidate.is_absolute():
            candidate = common.ROOT / candidate
        if candidate.exists():
            model_sha = _sha256(candidate)
    training_context = {
        'run_dir': common.rel(run_dir),
        'workers': (meta.get('parameters') or {}).get('workers'),
        'episode_seconds': (meta.get('parameters') or {}).get('episode_seconds'),
        'reward_version': meta.get('reward_version'),
        'action_space_version': meta.get('action_space_version'),
        'control_mode': meta.get('control_mode'),
        'approval_token_id': meta.get('approval_token_id'),
    }
    return meta, model_path, model_sha, training_context


def evaluate_run(run_dir, max_episodes=20):
    run_dir = pathlib.Path(run_dir)
    if not run_dir.is_absolute():
        run_dir = common.ROOT / run_dir
    meta, model_path, model_sha, training_context = _run_metadata(run_dir)
    decision_seconds = float((meta.get('parameters') or {}).get('decision_seconds') or 0.5)
    episodes = trace_parser.load_run_episodes(
        run_dir, max_episodes=max_episodes, include_timeouts=True)
    parsed, excluded = [], []
    data_sources = []
    for jsonl, result in episodes:
        try:
            parsed_episode = trace_parser.parse_episode(
                jsonl, result, decision_seconds=decision_seconds)
        except Exception as exc:  # noqa: BLE001
            excluded.append({'episode_id': jsonl.stem, 'reason': 'parse_error: %r' % exc})
            continue
        parsed.append(parsed_episode)
        for path in (jsonl, jsonl.with_name(jsonl.name.replace('.jsonl', '.result.json'))):
            if path.exists():
                data_sources.append({'role': 'episode_trace', 'path': common.rel(path),
                                     'sha256': _sha256(path), 'size': path.stat().st_size})
    per_episode = []
    for episode in parsed:
        skills = skill_metrics.evaluate_episode(episode)
        per_episode.append({'episode_id': episode['episode_id'],
                            'episode': episode, 'skills': skills})
    skills_result = {}
    for sid in ('S1', 'S2', 'S3', 'S4'):
        skills_result[sid] = _skill_block(sid, per_episode)
    for sub in ('S5.1', 'S5.2', 'S5.3'):
        skills_result[sub] = _skill_block(sub, per_episode)
    skills_result['S5'] = skill_metrics.aggregate_skill('S5', per_episode)
    episode_metrics = {key: {'mean': _mean([e.get(key) for e in parsed]),
                             'median': _median([e.get(key) for e in parsed])}
                       for key in METRIC_KEYS}
    quality_flags = []
    for episode in parsed:
        quality_flags.extend((episode.get('data_quality') or {}).get('quality_flags') or [])
    eval_protocol = dict(EVAL_CONFIG, episode_seconds=training_context.get('episode_seconds')
                         or EVAL_CONFIG['episode_seconds'],
                         reward_version=training_context.get('reward_version'),
                         action_space_version=training_context.get('action_space_version'),
                         thresholds_version=skill_metrics.THRESHOLDS_VERSION)
    evaluation_meta = (meta.get('evaluation') or {}) if isinstance(meta, dict) else {}
    source_kind = 'fixed_eval' if evaluation_meta else 'training_traces'
    if evaluation_meta:
        eval_protocol = evaluation_meta.get('eval_protocol') or eval_protocol
    frozen_verified = evaluation_meta.get('frozen_verified')
    protocol_compliant = evaluation_meta.get('protocol_compliant')
    evaluation_protocol_hash = evaluation_meta.get('protocol_hash')
    eval_protocol_hash = evaluation_protocol_hash or hashlib.sha256(
        json.dumps(eval_protocol, sort_keys=True, separators=(',', ':'),
                   ensure_ascii=False).encode('utf-8')).hexdigest()
    source_manifest_hash = hashlib.sha256(
        json.dumps(data_sources, sort_keys=True, separators=(',', ':'),
                   ensure_ascii=False).encode('utf-8')).hexdigest()
    return {
        'schema_version': SCHEMA_VERSION,
        'record_id': 'R-' + time.strftime('%Y%m%d-%H%M%S') + '-' +
                     hashlib.sha256(str(run_dir).encode()).hexdigest()[:8],
        'generated_at': common.now_iso(),
        'run': common.rel(run_dir),
        'source_kind': source_kind,
        'frozen_verified': frozen_verified,
        'protocol_compliant': protocol_compliant,
        'model_path': model_path,
        'model_sha256': model_sha,
        'eval_protocol': eval_protocol,
        'eval_protocol_hash': eval_protocol_hash,
        'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
        'thresholds_hash': skill_metrics.THRESHOLDS_HASH,
        'mastery_rule_id': skill_metrics.MASTERY_RULE_ID,
        's4_sample_rule_id': skill_metrics.S4_SAMPLE_RULE_ID,
        's5_aggregation_id': skill_metrics.S5_AGG_ID,
        'episodes': {'total': len(parsed) + len(excluded), 'used': len(parsed),
                     'excluded': excluded},
        'data_quality': {'quality_flags': sorted(set(quality_flags)),
                         'obs_coverage_mean': _mean([
                             (e.get('data_quality') or {}).get('obs_coverage_composite')
                             for e in parsed]),
                         'damage_coverage_mean': _mean([
                             (e.get('data_quality') or {}).get('damage_coverage')
                             for e in parsed])},
        'data_sources': data_sources,
        'source_manifest_sha256': source_manifest_hash,
        'training_context': training_context,
        'skills': skills_result,
        'episode_metrics': episode_metrics,
    }


def _skill_block(skill_id, per_episode):
    block = skill_metrics.aggregate_skill(skill_id, per_episode)
    indicator_values = {}
    for entry in per_episode:
        item = entry['skills'].get(skill_id) or {}
        for name, indicator in (item.get('indicators') or {}).items():
            indicator_values.setdefault(name, []).append(indicator.get('value'))
    block['indicator_means'] = {name: _mean(values)
                                for name, values in indicator_values.items()}
    return block


def markdown(result):
    lines = ['# 技能评测结果（V2.2）', '',
             f"- generated_at: {result['generated_at']}",
             f"- schema: {result['schema_version']}",
             f"- thresholds: {result['thresholds_version']} `{result['thresholds_hash'][:16]}`",
             f"- mastery_rule: `{result['mastery_rule_id']}`",
             f"- eval_protocol_hash: `{result['eval_protocol_hash'][:16]}`",
             f"- episodes: total={result['episodes']['total']} used={result['episodes']['used']}",
             f"- model_sha256: `{result['model_sha256']}`", '',
             '## 技能状态', '', '| 技能 | 状态 | pass_rate | Wilson 95% | n |', '|---|---|---|---|---|']
    for sid in ('S1', 'S2', 'S3', 'S4', 'S5', 'S5.1', 'S5.2', 'S5.3'):
        block = result['skills'].get(sid) or {}
        ci = ('[%s, %s]' % (block.get('wilson_low'), block.get('wilson_high'))
              if block.get('wilson_low') is not None else '-')
        lines.append('| %s | %s | %s | %s | %s |'
                     % (sid, block.get('status', '-'), block.get('pass_rate', '-'),
                        ci, block.get('n_episodes', '-')))
    lines += ['', '## 关键指标均值', '',
              '| 指标 | mean | median |', '|---|---|---|']
    for name, values in result['episode_metrics'].items():
        lines.append('| %s | %s | %s |' % (name, values.get('mean'), values.get('median')))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', action='append', required=True, help='run 目录，可重复')
    ap.add_argument('--out', default='reports/dsh-learning/skill-eval-runs.json')
    ap.add_argument('--md', default='reports/dsh-learning/skill-eval-runs.md')
    ap.add_argument('--max-episodes', type=int, default=20)
    args = ap.parse_args(argv)
    runs = []
    for run in args.run:
        print('[skill-eval] parsing', run, flush=True)
        record = evaluate_run(run, max_episodes=args.max_episodes)
        runs.append(record)
        print('  episodes=%d skills=%s' % (record['episodes']['used'],
              {sid: record['skills'][sid]['status']
               for sid in ('S1', 'S2', 'S3', 'S4', 'S5')}), flush=True)
    payload = {'schema': 'v2.2-skill-eval-runs', 'generated_at': common.now_iso(),
               'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
               'thresholds_hash': skill_metrics.THRESHOLDS_HASH,
               'runs': runs}
    common.save_json_atomic(common.ROOT / args.out, payload)
    (common.ROOT / args.md).write_text(
        '\n\n'.join([markdown(r) for r in runs]), encoding='utf-8')
    print('wrote', args.out, 'and', args.md)


if __name__ == '__main__':
    main()
