"""学习效率记录（V2.2）：环境步数为主指标，墙钟时间绑定 worker 配置。"""
import argparse
import pathlib
import sys

from dsh_learning import common, skill_metrics

SCHEMA_VERSION = 'v2.2-learning-efficiency'


def _episode_times(run_dir):
    times = []
    for result in run_dir.glob('workers/*/episodes/*.result.json'):
        try:
            times.append(result.stat().st_mtime)
        except OSError:
            continue
    return (min(times), max(times)) if times else (None, None)


def scan_run(run_dir):
    run_dir = pathlib.Path(run_dir)
    if not run_dir.is_absolute():
        run_dir = common.ROOT / run_dir
    meta = {}
    if (run_dir / 'run.json').exists():
        try:
            meta = common.load_json(run_dir / 'run.json') or {}
        except Exception:  # noqa: BLE001
            meta = {}
    progress = {}
    if (run_dir / 'progress.json').exists():
        progress = common.load_json(run_dir / 'progress.json') or {}
    params = meta.get('parameters') or {}
    timesteps_after = meta.get('timesteps_after') or progress.get('timesteps')
    timesteps_before = meta.get('timesteps_before') or 0
    steps = (int(timesteps_after) - int(timesteps_before)) if timesteps_after else None
    completed = int(meta.get('completed_episodes') or progress.get('episodes') or 0) or None
    t0, t1 = _episode_times(run_dir)
    wall = (t1 - t0) if (t0 and t1 and t1 > t0) else None
    return {
        'run': common.rel(run_dir),
        'workers': params.get('workers'),
        'episode_seconds': params.get('episode_seconds'),
        'reward_version': meta.get('reward_version'),
        'action_space_version': meta.get('action_space_version'),
        'status': meta.get('status'),
        'training_steps': steps,
        'completed_episodes': completed,
        'steps_per_episode': (round(steps / completed, 1) if steps and completed else None),
        'wall_seconds_observed': round(wall, 1) if wall else None,
        'steps_per_hour': (round(steps * 3600.0 / wall, 1) if steps and wall else None),
        'episodes_per_hour': (round(completed * 3600.0 / wall, 2)
                              if completed and wall else None),
        'primary_metric': 'training_steps',
        'time_note': 'wall time 依赖 worker 配置；跨配置只比较环境步数',
    }


def skill_mastery_events(scans, skill_records):
    """按评测时间顺序累计训练量，记录每技能首次达到 MASTERED 时的累计步数。"""
    by_run = {scan['run']: scan for scan in scans}
    ordered = sorted(skill_records, key=lambda r: r.get('generated_at', ''))
    events = {}
    for sid in ('S1', 'S2', 'S3', 'S4', 'S5'):
        events[sid] = {'status': 'NOT_ACHIEVED', 'steps_to_mastery': None,
                       'episodes_to_mastery': None, 'hours_to_mastery': None,
                       'record_id': None, 'thresholds_version': None,
                       'thresholds_hash': None}
    cumulative = {'steps': 0, 'episodes': 0, 'hours': 0.0}
    for record in ordered:
        run_path = record.get('run')
        scan = by_run.get(run_path) or by_run.get(str(run_path))
        if scan is None:
            for key, value in by_run.items():
                if key.endswith(str(run_path).replace('\\', '/')):
                    scan = value
                    break
        if scan:
            cumulative['steps'] += scan.get('training_steps') or 0
            cumulative['episodes'] += scan.get('completed_episodes') or 0
            cumulative['hours'] += (scan.get('wall_seconds_observed') or 0) / 3600.0
        for sid in ('S1', 'S2', 'S3', 'S4', 'S5'):
            block = (record.get('skills') or {}).get(sid) or {}
            if block.get('status') == 'MASTERED' and events[sid]['status'] != 'MASTERED':
                events[sid] = {
                    'status': 'MASTERED',
                    'steps_to_mastery': cumulative['steps'],
                    'episodes_to_mastery': cumulative['episodes'],
                    'hours_to_mastery': round(cumulative['hours'], 3),
                    'record_id': record.get('record_id'),
                    'thresholds_version': record.get('thresholds_version'),
                    'thresholds_hash': record.get('thresholds_hash'),
                }
    return events


def build_report(run_dirs, skill_eval_path):
    scans = [scan_run(run) for run in run_dirs]
    skill_records = []
    if skill_eval_path:
        payload = common.load_json(pathlib.Path(skill_eval_path)) or {}
        skill_records = payload.get('runs', [])
    return {
        'schema': SCHEMA_VERSION,
        'generated_at': common.now_iso(),
        'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
        'thresholds_hash': skill_metrics.THRESHOLDS_HASH,
        'runs': scans,
        'mastery_events': skill_mastery_events(scans, skill_records),
        'notes': ['主指标=环境步数；wall time 仅供同配置比较',
                  '未达到掌握 = NOT_ACHIEVED，不是 0 步'],
    }


def markdown(report):
    lines = ['# 学习效率（V2.2，真实记录）', '',
             f"- generated_at: {report['generated_at']}",
             f"- thresholds_version: {report['thresholds_version']}", '',
             '## 训练量/吞吐', '',
             '| run | workers | episode_s | steps | episodes | steps/ep | steps/hour | wall_s | status |',
             '|---|---|---|---|---|---|---|---|---|']
    for scan in report['runs']:
        lines.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s |'
                     % (scan['run'], scan['workers'], scan['episode_seconds'],
                        scan['training_steps'], scan['completed_episodes'],
                        scan['steps_per_episode'], scan['steps_per_hour'],
                        scan['wall_seconds_observed'], scan['status']))
    lines += ['', '## 掌握所需训练量（真实评测序列）', '',
              '- 只有当技能在评测记录中达到 MASTERED 才会出现 steps_to_mastery；'
              '否则标记 NOT_ACHIEVED（不是 0）。', '']
    for sid, event in report['mastery_events'].items():
        lines.append(f"- {sid}: {event['status']}"
                     + (f" steps={event['steps_to_mastery']}" if event['status'] == 'MASTERED' else ''))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', action='append', required=True)
    ap.add_argument('--skill-eval', default='reports/dsh-learning/skill-eval-runs.json')
    ap.add_argument('--out', default='reports/dsh-learning/learning-efficiency.json')
    ap.add_argument('--md', default='reports/dsh-learning/learning-efficiency.md')
    args = ap.parse_args(argv)
    report = build_report(args.run, args.skill_eval)
    common.save_json_atomic(common.ROOT / args.out, report)
    (common.ROOT / args.md).write_text(markdown(report), encoding='utf-8')
    print('[efficiency] wrote', args.out, 'and', args.md)


if __name__ == '__main__':
    main()
