"""Aggregate validated per-case matrices; reject mixed protocols or unpaired/duplicate cases."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
ROOT=Path(os.environ.get('RA2_EVAL_ROOT',str(HERE.parents[1]))).resolve()
from eval_v22_support import SCHEMA,EvaluationError,digest,validate_case,validate_frozen,write_json
from eval_v22_metrics import summarize


def validate_matrix(matrix):
    if matrix.get('schema')!=SCHEMA:raise EvaluationError('Legacy/unrecognized matrix: use --allow-legacy for descriptive output only')
    if matrix.get('protocol_hash')!=digest(matrix.get('protocol')):raise EvaluationError('Protocol hash mismatch')
    plan=matrix.get('case_plan') or []
    if not plan or matrix.get('case_plan_hash')!=digest(plan):raise EvaluationError('Case-plan hash mismatch')
    expected={c['case_id']:c for c in plan}
    if len(expected)!=len(plan):raise EvaluationError('Duplicate planned case')
    if not matrix.get('evaluation_complete') or not matrix.get('frozen_verified'):raise EvaluationError('Incomplete/unfrozen evaluation')
    if matrix.get('training') is not False or matrix.get('used_for_training') is not False:raise EvaluationError('Training data cannot enter fixed evaluation')
    modes=matrix.get('modes') or {}
    if len(modes)!=1:raise EvaluationError('One explicitly identified policy mode is required')
    mode,content=next(iter(modes.items()))
    if matrix['protocol'].get('policy_mode')!=mode:raise EvaluationError('Policy mode differs from protocol')
    if content.get('mode')!=mode or content.get('deterministic')!=(mode=='deterministic'):raise EvaluationError('Policy-mode evidence differs')
    rows=content.get('episodes') or [];seen=set()
    if len(rows)!=len(plan):raise EvaluationError('Wrong episode count')
    for row in rows:
        identity=row.get('case_id')
        if identity not in expected or identity in seen:raise EvaluationError('Duplicate/unexpected result case')
        seen.add(identity);validate_case(row,expected[identity],matrix['protocol']['episode_seconds'])
        attempts=[a for a in matrix.get('attempts',[]) if a.get('case_id')==identity and a.get('attempt')==row.get('attempt')]
        if len(attempts)!=1 or not attempts[0]['result'].get('ok'):raise EvaluationError('Missing/ambiguous successful attempt evidence')
        result=attempts[0]['result']
        if result.get('row')!=row:raise EvaluationError('Successful attempt row differs from matrix row')
        if result.get('recorded_engine_seed')!=row['engine_seed']:raise EvaluationError('Recorded engine seed mismatch')
        validate_frozen(result.get('frozen') or {},matrix['model_sha256'])
    return mode,rows


def aggregate(matrices,allow_legacy=False):
    if not matrices:raise EvaluationError('No matrices found')
    legacy=any(data.get('schema')!=SCHEMA for _,data in matrices)
    if legacy and not allow_legacy:raise EvaluationError('Legacy matrix cannot establish paired/frozen comparison')
    if legacy and any(data.get('schema')==SCHEMA for _,data in matrices):raise EvaluationError('Do not mix legacy and repaired evaluations')
    result={'schema':'v22-aggregate-v2','pairing_status':'UNVERIFIED_LEGACY' if legacy else 'VERIFIED_CASE_PLAN',
            'mastery_claim':False,'games':{},'summary':{},'sources':[]}
    protocol_hash=plan_hash=None
    for path,data in matrices:
        key=data.get('model_key')
        if not key:
            key=next((k for k in ('r1','r4ep004','r4final') if Path(path).parent.name.endswith('-'+k)),None)
        if not key:raise EvaluationError('Missing model identity')
        if key in result['games']:raise EvaluationError('Duplicate model matrices; do not silently merge repeated cases')
        if legacy:
            modes=data.get('modes') or {}
            if len(modes)!=1:raise EvaluationError('Ambiguous legacy policy mode')
            mode,content=next(iter(modes.items()));rows=content.get('episodes') or []
            frozen=False
        else:
            mode,rows=validate_matrix(data)
            if protocol_hash is None:protocol_hash=data['protocol_hash'];plan_hash=data['case_plan_hash']
            if data['protocol_hash']!=protocol_hash or data['case_plan_hash']!=plan_hash:raise EvaluationError('Models have different protocol/case plans')
            frozen=True
        summary=summarize(rows)
        result['games'][key]={'mode':mode,'rows':rows,'frozen_verified':frozen,'summary':summary}
        result['summary'][key]=summary
        result['sources'].append(str(path))
    result.update(protocol_hash=protocol_hash,case_plan_hash=plan_hash)
    if not legacy:result.update(protocol=matrices[0][1]['protocol'],case_plan=matrices[0][1]['case_plan'])
    return result


def render_md(result):
    names=list(result['summary'])
    lines=['# 固定评测汇总','',f"- 配对状态：{result['pairing_status']}。",
           '- 本文件不作掌握或显著优越性声明。',
           f"- 协议哈希：{result.get('protocol_hash')}；逐局计划哈希：{result.get('case_plan_hash')}。"]
    if result['pairing_status']=='UNVERIFIED_LEGACY':
        lines.append('- 历史矩阵仅作描述；旧 frozen_verified 标记未升级为已验证，不能按严格配对比较。')
    else:
        lines.append('- 引擎配置种子：'+', '.join(str(c['engine_seed']) for c in result['case_plan']))
    lines+=['','| 指标 | '+' | '.join(names)+' |','| --- |'+' --- |'*len(names)]
    metrics=[('局数','n_episodes'),('结果','kinds'),('胜率','win_rate'),('攻击证据','attack_evidence_status_counts'),
             ('伤害交换比','damage_exchange'),('攻击有效率','attack_effective6_rate'),('重复指令率','order_repeat_rate'),('攻击/分钟','attacks_per_min')]
    for label,key in metrics:
        cells=[]
        for name in names:
            value=result['summary'][name].get(key)
            if isinstance(value,dict) and 'mean' in value:value=value['mean']
            cells.append('N/A' if value is None else json.dumps(value,ensure_ascii=False))
        lines.append('| '+label+' | '+' | '.join(cells)+' |')
    lines+=['','零 ATTACK 的有效率/重复率为 N/A；缺失或冲突的动作证据另列 UNKNOWN/CONFLICT，不当作零。','']
    return '\n'.join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pattern',required=True,help='Project-relative glob matching model directories')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--allow-legacy',action='store_true')
    args=parser.parse_args(argv)
    if args.out.exists():raise EvaluationError('Output exists; preserve historical evidence')
    matrices=[]
    for directory in sorted(ROOT.glob(args.pattern)):
        path=directory/'evaluation_matrix.json'
        if not path.is_file():raise EvaluationError(f'Missing matrix: {directory}')
        matrices.append((str(path),json.loads(path.read_text(encoding='utf-8-sig'))))
    result=aggregate(matrices,args.allow_legacy)
    args.out.mkdir(parents=True)
    write_json(args.out/'AGGREGATE.json',result)
    (args.out/'SUMMARY.md').write_text(render_md(result),encoding='utf-8')
    print(render_md(result))
    return 0


if __name__=='__main__':raise SystemExit(main())
