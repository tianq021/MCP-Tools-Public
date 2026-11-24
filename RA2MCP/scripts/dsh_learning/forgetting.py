"""技能遗忘检测（V2.2）：统一状态枚举 + 置信区间；INCOMPUTABLE/UNKNOWN/NOT_MEASURED 不参与。"""
import argparse
import pathlib

from dsh_learning import common, skill_metrics

SCHEMA_VERSION = 'v2.2-forgetting-report'
REGRESSION_PASS_RATE_DROP = 0.15
IGNORED_STATUSES = {'INCOMPUTABLE', 'UNKNOWN', 'NOT_MEASURED', 'PARTIAL', 'PROVISIONAL'}


def _run_name(record):
    run = record.get('run')
    if isinstance(run, dict):
        run = run.get('run')
    return str(run)


def detect_forgetting(ordered_records):
    report = {'ordered_runs': [_run_name(r) for r in ordered_records],
              'pass_rate_drop_threshold': REGRESSION_PASS_RATE_DROP,
              'skills': {}, 'alerts': [],
              'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
              'thresholds_hash': skill_metrics.THRESHOLDS_HASH}
    for sid in ('S1', 'S2', 'S3', 'S4', 'S5'):
        series, alerts = [], []
        prev = None
        for record in ordered_records:
            block = (record.get('skills') or {}).get(sid) or {}
            row = {'run': _run_name(record), 'record_id': record.get('record_id'),
                   'status': block.get('status'), 'pass_rate': block.get('pass_rate'),
                   'wilson_low': block.get('wilson_low'),
                   'wilson_high': block.get('wilson_high')}
            series.append(row)
            if prev is not None and prev['status'] not in IGNORED_STATUSES \
                    and row['status'] not in IGNORED_STATUSES:
                if prev['status'] == 'MASTERED' and row['status'] != 'MASTERED':
                    alerts.append({'type': 'STATUS_DOWNGRADE', 'skill': sid,
                                   'from_run': prev['run'], 'to_run': row['run'],
                                   'from_status': prev['status'],
                                   'to_status': row['status'],
                                   'action': '安排复习课程；用户批准后可回滚'})
                elif (prev.get('pass_rate') is not None
                      and row.get('pass_rate') is not None
                      and prev['pass_rate'] - row['pass_rate'] > REGRESSION_PASS_RATE_DROP):
                    alerts.append({'type': 'PASS_RATE_DROP', 'skill': sid,
                                   'from_run': prev['run'], 'to_run': row['run'],
                                   'drop': round(prev['pass_rate'] - row['pass_rate'], 3),
                                   'action': '用固定评测复核；确认后退回最佳模型'})
            prev = row
        report['skills'][sid] = {'name': skill_metrics.SKILLS[sid]['name'],
                                 'series': series, 'regressed': bool(alerts)}
        report['alerts'].extend(alerts)
    return report


def methods_assessment():
    return {
        'curriculum': {'compat': '高', 'cost': '低', 'risk': '课程设计掩盖真实能力',
                       'verdict': '推荐第一优先'},
        'transfer': {'compat': '高', 'cost': '中', 'risk': '契约不一致污染 return',
                     'verdict': '推荐（见 v2.2-transfer-design）',
                     'caveat': '需 S1 掌握 checkpoint；C 组公平性须验证'},
        'replay': {'compat': '中', 'cost': '中高', 'risk': 'PPO off-policy 回放需改训练循环',
                   'verdict': 'V2.2 不实现'},
        'distillation': {'compat': '中', 'cost': '中高', 'risk': 'teacher 自身未掌握',
                         'verdict': 'P6 之后再评估'},
        'continual': {'compat': '低', 'cost': '高', 'risk': '需改损失/架构，违反当前约束',
                      'verdict': '不实现'},
    }


def build_report(ordered_records):
    return {'schema': SCHEMA_VERSION, 'generated_at': common.now_iso(),
            'detection': detect_forgetting(ordered_records),
            'methods': methods_assessment(),
            'note': '只有真实固定评测记录才能判定遗忘；历史轨迹只能作趋势。'}


def markdown(report):
    detection = report['detection']
    lines = ['# 技能遗忘检测（V2.2）', '',
             f"- generated_at: {report['generated_at']}",
             f"- thresholds: {detection['thresholds_version']} `{detection['thresholds_hash'][:16]}`",
             f"- runs: {' → '.join(detection['ordered_runs'])}", '',
             '| 技能 | 状态序列 | 是否回归 |', '|---|---|---|']
    for sid in ('S1', 'S2', 'S3', 'S4', 'S5'):
        entry = detection['skills'][sid]
        sequence = ' → '.join(str(row['status']) for row in entry['series'])
        lines.append(f"| {sid} {entry['name']} | {sequence} | {entry['regressed']} |")
    lines += ['', '## 警报']
    if detection['alerts']:
        for alert in detection['alerts']:
            lines.append(f"- {alert['type']} {alert['skill']}: "
                         f"{alert['from_run']} → {alert['to_run']}")
    else:
        lines.append('- 无（现有评测序列未检测到回归；样本不足时不能证明无遗忘）')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--skill-eval', default='reports/dsh-learning/skill-eval-runs.json')
    ap.add_argument('--out', default='reports/dsh-learning/forgetting-report.json')
    ap.add_argument('--md', default='reports/dsh-learning/forgetting-report.md')
    args = ap.parse_args(argv)
    payload = common.load_json(pathlib.Path(args.skill_eval)) or {}
    report = build_report(payload.get('runs', []))
    common.save_json_atomic(common.ROOT / args.out, report)
    (common.ROOT / args.md).write_text(markdown(report), encoding='utf-8')
    print('[forgetting] alerts=%d' % len(report['detection']['alerts']))


if __name__ == '__main__':
    main()
