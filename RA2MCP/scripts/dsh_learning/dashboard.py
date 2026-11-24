"""成长档案生成器（P7/D6）：只从真实记录文件生成 dashboard JSON + 自包含 HTML。

原则：
- 数据全部来自用户/评测产出的 JSON（skill-eval / efficiency / forgetting / proposal / best-model）；
- 未知/未测/不可计算 → 显式状态与 N/A 原因，不填 0、不模拟曲线；
- 训练步数只作为 context 展示，禁止用于推断技能进度；
- 协议/阈值版本不一致 → 面板标 INCOMPARABLE 并警告，不混合比较；
- 无外部依赖、无网络请求、断网可用。
"""
import argparse
import html
import json
import pathlib
import sys

from dsh_learning import common, skill_metrics

SCHEMA_VERSION = 'v2.2-dashboard'
STATE_ENUM = ('MASTERED', 'NOT_MASTERED', 'UNDETERMINED', 'INCOMPUTABLE',
              'UNKNOWN', 'PARTIAL', 'NOT_MEASURED')
SKILL_IDS = ('S1', 'S2', 'S3', 'S4', 'S5', 'S5.1', 'S5.2', 'S5.3')
SKILL_NAMES = {'S1': '主动进攻', 'S2': '有组织进攻', 'S3': '集中火力',
               'S4': '生存与撤退', 'S5': '侦察与反制',
               'S5.1': 'S5.1 发现', 'S5.2': 'S5.2 探索', 'S5.3': 'S5.3 反制'}
STATUS_COLORS = {'MASTERED': '#2e7d32', 'NOT_MASTERED': '#c62828',
                 'UNDETERMINED': '#f9a825', 'INCOMPUTABLE': '#6d4c41',
                 'UNKNOWN': '#546e7a', 'PARTIAL': '#8d6e63',
                 'NOT_MEASURED': '#9e9e9e', 'INCOMPARABLE': '#b71c1c'}


def _load(path):
    if not path:
        return None
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        candidate = common.ROOT / candidate
    if not candidate.exists():
        return {'_missing': str(path)}
    try:
        return common.load_json(candidate)
    except Exception as exc:  # noqa: BLE001
        return {'_error': repr(exc), '_path': str(path)}


def collect_skill_records(paths):
    """返回 (records, warnings)：只接受 v2.2-skill-eval-runs；旧 schema 拒绝混合。"""
    records, warnings = [], []
    for path in paths:
        payload = _load(path)
        if payload is None:
            continue
        if '_missing' in payload or '_error' in payload:
            warnings.append('source_unreadable: %s' % path)
            continue
        runs = payload.get('runs') or []
        for record in runs:
            version = record.get('schema_version')
            if version != 'v2.2-skill-record':
                warnings.append('legacy_or_unknown_schema, excluded: %s (%s)'
                                % (path, version))
                continue
            records.append(record)
    records.sort(key=lambda r: r.get('generated_at') or '')
    return records, warnings


def _consistency(records):
    if not records:
        return {'ok': False, 'reason': 'no_records'}
    versions = {r.get('thresholds_version') for r in records}
    hashes = {r.get('thresholds_hash') for r in records}
    protocols = {r.get('eval_protocol_hash') for r in records}
    if len(versions) > 1 or len(hashes) > 1:
        return {'ok': False, 'reason': 'thresholds_version_or_hash_mixed',
                'versions': sorted(str(v) for v in versions),
                'hashes': sorted(str(h) for h in hashes)}
    if len(protocols) > 1:
        return {'ok': False, 'reason': 'eval_protocol_hash_mixed',
                'protocols': sorted(str(p) for p in protocols)}
    return {'ok': True, 'thresholds_version': list(versions)[0],
            'thresholds_hash': list(hashes)[0],
            'eval_protocol_hash': list(protocols)[0],
            'record_ids': [r.get('record_id') for r in records]}


def _skill_panel(records, consistency):
    latest = records[-1] if records else {}
    skills = latest.get('skills') or {}
    panel = {}
    for sid in SKILL_IDS:
        block = skills.get(sid) or {}
        status = block.get('status')
        if consistency.get('reason') == 'no_records':
            status = 'UNKNOWN'
        elif not consistency['ok']:
            status = 'INCOMPARABLE'
        elif status is None:
            status = 'UNKNOWN'
        if status == 'PARTIAL':
            status = 'PARTIAL'  # 仅历史兼容，保留并注明
        detail_status = status
        if status == 'PROVISIONAL':
            status = 'NOT_MEASURED'  # V2.2 状态枚举无 PROVISIONAL，映射并保留细节        assert status in STATE_ENUM or status == 'INCOMPARABLE'
        panel[sid] = {
            'name': SKILL_NAMES[sid],
            'status': status,
            'detail_status': detail_status,
            'pass_rate': block.get('pass_rate'),
            'wilson_low': block.get('wilson_low'),
            'wilson_high': block.get('wilson_high'),
            'n_episodes': block.get('n_episodes'),
            'record_id': latest.get('record_id'),
            'source': latest.get('run'),
            'evidence_path': latest.get('data_sources', [{}])[0].get('path')
            if latest.get('data_sources') else None,
            'na_reason': None if status not in ('UNKNOWN', 'NOT_MEASURED',
                                                'INCOMPUTABLE', 'INCOMPARABLE')
            else (block.get('reasons') or ['no_evidence']),
        }
    return panel


def _learning_panel(efficiency, consistency):
    if (not efficiency or '_missing' in efficiency or '_error' in efficiency):
        return {'source': None, 'skills': {}, 'runs': [],
                'note': '无效率记录文件或不可读'}
    events = efficiency.get('mastery_events') or {}
    panel = {}
    for sid in ('S1', 'S2', 'S3', 'S4', 'S5'):
        event = events.get(sid) or {}
        mastered = event.get('status') == 'MASTERED'
        panel[sid] = {
            'steps_to_mastery': event.get('steps_to_mastery') if mastered else None,
            'episodes_to_mastery': event.get('episodes_to_mastery') if mastered else None,
            'hours_to_mastery': event.get('hours_to_mastery') if mastered else None,
            'status': 'OK' if mastered else 'NOT_MEASURED',
            'na_reason': None if mastered else 'not_mastered_or_no_mastery_evidence',
        }
    runs = []
    for scan in efficiency.get('runs') or []:
        runs.append({'run': scan.get('run'), 'workers': scan.get('workers'),
                     'episode_seconds': scan.get('episode_seconds'),
                     'training_steps': scan.get('training_steps'),
                     'steps_per_hour': scan.get('steps_per_hour'),
                     'wall_seconds_observed': scan.get('wall_seconds_observed'),
                     'status': scan.get('status')})
    return {'source': 'learning-efficiency', 'skills': panel, 'runs': runs,
            'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
            'note': '训练步数仅作上下文，禁止据此推断技能进度；未达标 = NOT_MEASURED 而非 0'}


def _transfer_panel(skill_records, consistency):
    if not consistency['ok'] and skill_records:
        return {'ratio': None, 'na_reason': 'incomparable_records',
                'status': 'INCOMPARABLE'}
    return {'ratio': None, 'na_reason': 'not_measured',
            'status': 'NOT_MEASURED',
            'note': '未见地图迁移评测属 P3+/迁移实验，当前无数据'}


def _forgetting_panel(forgetting):
    if (not forgetting or '_missing' in forgetting or '_error' in forgetting):
        return {'alerts': [], 'source': None, 'status': 'NOT_MEASURED',
                'na_reason': 'no_forgetting_records'}
    detection = forgetting.get('detection') or {}
    alerts = detection.get('alerts') or []
    return {'alerts': alerts, 'source': 'forgetting-report',
            'status': 'OK' if alerts else 'OK_NO_ALERTS',
            'disclaimer': '0 警报不等于无遗忘；需逐 checkpoint 固定评测（P3）'}


def _best_model_panel(pointer):
    if not pointer:
        return {'id': None, 'status': 'NOT_MEASURED', 'na_reason': 'no_pointer'}
    return {'id': pointer.get('id'), 'path': pointer.get('path'),
            'sha256': pointer.get('sha256'), 'score': pointer.get('score'),
            'record_id': pointer.get('record_id'),
            'updated_at': pointer.get('generated_at'),
            'status': 'OK' if pointer.get('id') else 'NOT_MEASURED'}


def _target_panel(proposal):
    if (not proposal or '_missing' in proposal or '_error' in proposal):
        return {'skill': None, 'status': 'NOT_MEASURED', 'na_reason': 'no_proposal',
                'requires_user_approval': True, 'launch_performed': False}
    course = proposal.get('course') or {}
    return {'skill': course.get('target_skill'),
            'course_type': course.get('type'),
            'status': proposal.get('status'),
            'rationale': proposal.get('rationale'),
            'evidence_record_id': proposal.get('evidence_record_id'),
            'requires_user_approval': True,
            'launch_performed': bool(proposal.get('launch_performed')),
            'manager_started_training': bool(proposal.get('manager_started_training'))}


def build_dashboard(skill_eval_paths, efficiency_path=None, forgetting_path=None,
                    proposal_path=None, best_model_path=None):
    records, warnings = collect_skill_records(skill_eval_paths)
    consistency = _consistency(records)
    efficiency = _load(efficiency_path)
    forgetting = _load(forgetting_path)
    proposal = _load(proposal_path)
    best = _load(best_model_path)
    if isinstance(best, dict) and ('_missing' in best or '_error' in best):
        best = None
    latest = records[-1] if records else {}
    if not consistency['ok'] and consistency.get('reason') != 'no_records':
        warnings.append('INCOMPARABLE: ' + str(consistency.get('reason')))
    dashboard = {
        'schema': SCHEMA_VERSION,
        'state_enum_id': 'v2.2-state-enum',
        'allowed_states': list(STATE_ENUM),
        'mastery_rule_id': skill_metrics.MASTERY_RULE_ID,
        's4_sample_rule_id': skill_metrics.S4_SAMPLE_RULE_ID,
        's5_aggregation_id': skill_metrics.S5_AGG_ID,
        'thresholds': {'version': consistency.get('thresholds_version'),
                       'hash': consistency.get('thresholds_hash')},
        'eval_protocol_hash': consistency.get('eval_protocol_hash'),
        'generated_at': common.now_iso(),
        'source_status': {'consistency': consistency, 'warnings': warnings,
                          'records_used': len(records),
                          'record_ids': [r.get('record_id') for r in records]},
        'panels': {
            'skills': _skill_panel(records, consistency),
            'learning': _learning_panel(efficiency, consistency),
            'transfer': _transfer_panel(records, consistency),
            'forgetting': _forgetting_panel(forgetting),
            'best_model': _best_model_panel(best),
            'current_target': _target_panel(proposal),
            'training_context': {
                'note': '训练步数仅作上下文；技能状态只来自 skill-eval 记录',
                'records_excluded': [w for w in warnings if 'excluded' in w],
            },
        },
        'honesty_rules': [
            '无数据 = N/A/NOT_MEASURED/UNKNOWN，不是 0',
            '0 警报不等于无遗忘',
            '训练奖励/步数不能证明技能掌握',
            '协议/阈值不一致 → INCOMPARABLE，不混合比较',
            '不生成模拟数据或插值曲线',
        ],
    }
    return dashboard


def _fmt(value, suffix=''):
    if value is None:
        return 'N/A'
    if isinstance(value, float):
        return ('%.4f' % value).rstrip('0').rstrip('.') + suffix
    return str(value) + suffix


def render_html(dashboard):
    panels = dashboard['panels']
    rows = []
    for sid, block in panels['skills'].items():
        color = STATUS_COLORS.get(block['status'], '#455a64')
        ci = ('[%s, %s]' % (_fmt(block['wilson_low']), _fmt(block['wilson_high']))
              if block.get('wilson_low') is not None else 'N/A')
        reason = ''
        if block.get('na_reason'):
            reason = '<div class="reason">N/A: %s</div>' % html.escape(
                str(block['na_reason'])[:160])
        rows.append(
            '<tr><td>%s</td><td><span class="badge" style="background:%s">%s</span>%s</td>'
            '<td>%s</td><td>%s</td><td>%s</td><td class="src">%s</td></tr>'
            % (html.escape(sid + ' ' + block['name']), color,
               html.escape(block['status']), reason, _fmt(block['pass_rate']),
               ci, _fmt(block['n_episodes']), html.escape(str(block.get('source')))))
    learning_rows = []
    for sid, block in (panels['learning'].get('skills') or {}).items():
        learning_rows.append(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (sid, _fmt(block.get('steps_to_mastery')),
               _fmt(block.get('hours_to_mastery')), html.escape(block.get('status'))))
    context_rows = []
    for scan in panels['learning'].get('runs') or []:
        context_rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                            % (html.escape(str(scan.get('run'))), _fmt(scan.get('workers')),
                               _fmt(scan.get('training_steps')),
                               _fmt(scan.get('steps_per_hour'))))
    alerts = panels['forgetting'].get('alerts') or []
    alert_items = ''.join('<li>%s</li>' % html.escape(json.dumps(alert, ensure_ascii=False))
                          for alert in alerts) or '<li>无警报（不代表无遗忘）</li>'
    transfer = panels['transfer']
    target = panels['current_target']
    best = panels['best_model']
    warnings = ''.join('<li>%s</li>' % html.escape(w)
                       for w in dashboard['source_status']['warnings']) or '<li>无</li>'
    threshold = dashboard['thresholds']
    consistency = dashboard['source_status']['consistency']
    consistency_text = '一致' if consistency.get('ok') else 'INCOMPARABLE: %s' % consistency.get('reason')
    return '''<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>RA2MCP 成长档案（V2.2）</title>
<style>
body{font-family:Segoe UI,Microsoft YaHei,sans-serif;margin:24px;background:#fafafa;color:#222}
h1{font-size:22px} h2{font-size:16px;margin-top:26px;border-left:4px solid #1565c0;padding-left:8px}
table{border-collapse:collapse;background:#fff;min-width:760px}
th,td{border:1px solid #ddd;padding:6px 10px;font-size:13px;text-align:left}
th{background:#eceff1} .badge{color:#fff;border-radius:4px;padding:2px 6px;font-size:12px}
.reason{color:#b71c1c;font-size:12px;margin-top:3px} .src{color:#607d8b;font-size:11px}
.note{background:#fff8e1;border:1px solid #ffe082;padding:8px;font-size:12px;margin:10px 0}
ul{font-size:12px} .muted{color:#607d8b}
</style></head><body>
<h1>RA2MCP 成长档案（V2.2 / 真实记录）</h1>
<div class="muted">generated_at: %s | thresholds: %s `%s` | protocol: %s</div>
<div class="note">数据一致性：%s。训练步数仅作上下文；技能状态只来自 skill-eval 记录；
无数据 = N/A/NOT_MEASURED/UNKNOWN，不填 0；不生成模拟数据。</div>
<h2>P1 技能掌握总览</h2>
<table><tr><th>技能</th><th>状态</th><th>pass_rate</th><th>Wilson95pct</th><th>n</th><th>来源</th></tr>
%s</table>
<h2>P2/P3 学习时间与掌握步数</h2>
<table><tr><th>技能</th><th>steps_to_mastery</th><th>hours_to_mastery</th><th>状态</th></tr>%s</table>
<div class="muted">未达标显示 N/A（原因：未达到掌握标准），不是 0。</div>
<h2>P4 训练上下文（不计入技能成长）</h2>
<table><tr><th>run</th><th>workers</th><th>training_steps</th><th>steps/hour</th></tr>%s</table>
<h2>P5 迁移</h2><p>ratio=%s，status=%s，原因=%s</p>
<h2>P6 遗忘</h2><ul>%s</ul>
<h2>P7 历史最佳模型</h2><p id=%s，sha256=%s，status=%s</p>
<h2>P8 当前训练目标（管理器建议，需用户批准）</h2>
<p>course=%s，target=%s，requires_approval=%s，launch_performed=%s</p>
<div class="reason">%s</div>
<h2>数据警告</h2><ul>%s</ul>
<h2>诚实规则</h2><ul>%s</ul>
</body></html>''' % (
        html.escape(dashboard['generated_at']),
        html.escape(str(threshold.get('version'))),
        html.escape(str(threshold.get('hash'))[:16]),
        html.escape(str(dashboard.get('eval_protocol_hash'))[:16]),
        html.escape(consistency_text),
        ''.join(rows) or '<tr><td colspan="6">无记录</td></tr>',
        ''.join(learning_rows) or '<tr><td colspan="4">无记录</td></tr>',
        ''.join(context_rows) or '<tr><td colspan="4">无记录</td></tr>',
        _fmt(transfer.get('ratio')), html.escape(str(transfer.get('status'))),
        html.escape(str(transfer.get('na_reason'))),
        alert_items,
        html.escape(str(best.get('id'))), html.escape(str(best.get('sha256'))),
        html.escape(str(best.get('status'))),
        html.escape(str(target.get('course_type'))),
        html.escape(str(target.get('skill'))),
        html.escape(str(target.get('requires_user_approval'))),
        html.escape(str(target.get('launch_performed'))),
        html.escape(str(target.get('rationale'))),
        warnings,
        ''.join('<li>%s</li>' % html.escape(rule) for rule in dashboard['honesty_rules']))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--skill-eval', action='append', default=None,
                    help='v2.2-skill-record 输出（可重复；旧 schema 会被拒绝）')
    ap.add_argument('--efficiency', default='reports/dsh-learning/learning-efficiency.json')
    ap.add_argument('--forgetting', default='reports/dsh-learning/forgetting-report.json')
    ap.add_argument('--proposal',
                    default='reports/dsh-learning/manager/curriculum-proposal.json')
    ap.add_argument('--best-model', default='reports/dsh-learning/best-model.json')
    ap.add_argument('--out-json', default='reports/dsh-learning/dashboard.json')
    ap.add_argument('--out-html', default='reports/dsh-learning/dashboard.html')
    args = ap.parse_args(argv)
    dashboard = build_dashboard(args.skill_eval or [], args.efficiency,
                                args.forgetting, args.proposal, args.best_model)
    common.save_json_atomic(common.ROOT / args.out_json, dashboard)
    (common.ROOT / args.out_html).write_text(render_html(dashboard), encoding='utf-8')
    print('[dashboard] records=%d consistency=%s -> %s / %s'
          % (dashboard['source_status']['records_used'],
             dashboard['source_status']['consistency'].get('ok'),
             args.out_json, args.out_html))


if __name__ == '__main__':
    main()
