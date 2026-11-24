"""Evaluation metrics with explicit zero/missing-data and control-mode semantics."""
from __future__ import annotations
import json
import math
from pathlib import Path
import statistics

METRIC_KEYS=('damage_dealt','own_hp_lost','damage_exchange','attacks_issued','attacks_effective6',
             'attack_effective6_rate','order_repeat_rate','attacks_per_min','hp_exchange',
             'own_losses_per_min','kills_per_1000_unit_damage','first_effective_attack_s','elapsed_s')


def records(path):
    rows=[]
    with Path(path).open(encoding='utf-8-sig') as f:
        for number,line in enumerate(f,1):
            if not line.strip(): continue
            try: row=json.loads(line)
            except ValueError as exc: raise ValueError(f'Corrupt trace at line {number}') from exc
            if not isinstance(row,dict): raise ValueError('Trace row must be an object')
            rows.append(row)
    return rows


def policy_details(meta):
    policy=meta.get('policy','')
    if isinstance(policy,dict):
        name=policy.get('name','');control=policy.get('params') or {}
    else:
        name=policy;control=meta.get('model') or {}  # compatibility with older flat metadata
    if not isinstance(name,str) or not isinstance(control,dict):raise ValueError('Invalid policy metadata')
    return name,control


def trace_context(rows):
    meta=next((r.get('meta') or r for r in rows if r.get('type')=='meta'),{})
    policy,control=policy_details(meta)
    if control.get('fixed_economy') is True or 'combat' in policy: mode='combat'
    elif control.get('fixed_economy') is False or policy=='maskable_ppo_full': mode='full'
    else: mode='unknown'
    return meta,mode


def economy_observed(episode_jsonl,result,rows=None):
    rows=records(episode_jsonl) if rows is None else rows
    _,mode=trace_context(rows)
    observations=[r for r in rows if r.get('type')=='observation']
    money=[float(r['money']) for r in observations if r.get('money') is not None]
    increases=sum(max(0,b-a) for a,b in zip(money,money[1:]))
    elapsed=float(result.get('elapsed_s') or 0)
    metrics=result.get('metrics') or {}
    status={'combat':'INCOMPUTABLE_FIXED_SCRIPTED_ECONOMY','full':'OBSERVED_FULL_CONTROL','unknown':'UNKNOWN_CONTROL_MODE'}[mode]
    reason=('Economy is controlled by RulePolicy, not attributed to the policy.' if mode=='combat'
            else 'Efficiency is not defined by this protocol; cash increases can include refunds.' if mode=='full'
            else 'Trace does not identify full or scripted-economy control.')
    return {'status':status,'control_mode':mode,'na_reason':reason,'economy_efficiency':None,
            'money_final':money[-1] if money else None,'money_income_per_min':round(increases/(elapsed/60),2) if elapsed else None,
            'income_measurement':'positive_cash_delta_proxy_not_verified_harvest_income',
            'money_samples':len(money),'buildings_peak':metrics.get('buildings_peak'),
            'units_peak':metrics.get('units_peak'),'economy_invalid_orders':metrics.get('economy_invalid_orders')}


def episode_metrics(episode_jsonl,result):
    # Import lazily: planning/validation never imports game or ML packages.
    from dsh_learning import trace_parser
    raw=records(episode_jsonl);episode=trace_parser.parse_episode(episode_jsonl,result)
    metrics=result.get('metrics') or {};_,mode=trace_context(raw)
    actions=[r for r in raw if r.get('type')=='action' and r.get('decision_source') in (None,'ppo')]
    raw_attacks=sum(r.get('action')=='ATTACK' for r in actions)
    counter=metrics.get('attacks_issued')
    if raw_attacks:
        status='ATTACKS_OBSERVED'
    elif actions or (mode!='unknown' and counter==0 and sum(r.get('type')=='observation' for r in raw)>=2):
        status='ZERO_ATTACKS_OBSERVED'
    else: status='UNKNOWN_MISSING_ACTION_EVIDENCE'
    if counter is not None and counter>0 and raw_attacks==0: status='CONFLICT_COUNTER_AND_TRACE'
    row={
        'episode_id':result.get('episode_id') or Path(episode_jsonl).stem,
        'kind':result.get('kind'),'is_win':bool(result.get('is_win')),
        'outcome_confirmed':bool(result.get('outcome_confirmed')),
        'damage_dealt':float(metrics.get('damage_dealt') or 0),
        'own_hp_lost':float(metrics.get('own_hp_lost') or 0),
        'elapsed_s':float(result.get('elapsed_s') or 0),
        'attack_evidence_status':status,'raw_attack_orders':raw_attacks,
        'data_quality_flags':list((episode.get('data_quality') or {}).get('quality_flags') or []),
        'economy':economy_observed(episode_jsonl,result,raw),
    }
    row['damage_exchange']=round(row['damage_dealt']/row['own_hp_lost'],4) if row['own_hp_lost'] else None
    for key in METRIC_KEYS:
        if key not in row: row[key]=episode.get(key)
    if status=='ZERO_ATTACKS_OBSERVED':
        row.update(attacks_issued=0,attacks_effective6=0,attack_effective6_rate=None,
                   order_repeat_rate=None,attacks_per_min=0. if row['elapsed_s'] else None,
                   first_effective_attack_s=None)
        row['attack_na_reason']='No ATTACK commands; effectiveness/repeat denominators are zero.'
    elif status in ('UNKNOWN_MISSING_ACTION_EVIDENCE','CONFLICT_COUNTER_AND_TRACE'):
        for key in ('attacks_issued','attacks_effective6','attack_effective6_rate','order_repeat_rate','attacks_per_min','first_effective_attack_s'): row[key]=None
        row['attack_na_reason']='Missing/conflicting action evidence; do not interpret as zero attacks.'
        row['data_quality_flags'].append(status)
    else:
        row['attack_na_reason']=None
    return row


def summarize(rows):
    result={'n_episodes':len(rows),'kinds':{},'attack_evidence_status_counts':{}}
    for row in rows:
        result['kinds'][row['kind']]=result['kinds'].get(row['kind'],0)+1
        status=row.get('attack_evidence_status','UNSPECIFIED')
        result['attack_evidence_status_counts'][status]=result['attack_evidence_status_counts'].get(status,0)+1
    for kind in ('win','timeout','crash'):
        result[kind+'_rate']=round(sum(r.get('kind')==kind for r in rows)/len(rows),4) if rows else None
    for key in METRIC_KEYS:
        values=[r[key] for r in rows if type(r.get(key)) in (int,float) and math.isfinite(r[key])]
        result[key]={'n':len(values),'mean':round(statistics.mean(values),4) if values else None,
                     'median':round(statistics.median(values),4) if values else None}
    result.update(sample_status='MINIMUM_COUNT_MET' if len(rows)>=20 else 'INSUFFICIENT_SAMPLE',
                  mastery_claim=False,mastery_note='Episode count alone never establishes mastery; inspect protocol, pairing, outcomes and uncertainty.')
    return result


def paired_compare(deterministic,stochastic,key):
    field='mean_delta_stochastic_minus_deterministic'
    if not deterministic or not stochastic or any(not r.get('case_id') for r in deterministic+stochastic):
        return {'status':'UNPAIRED_NO_CASE_ID','n_pairs':0,field:None}
    def index(rows):
        result={}
        for row in rows:
            case=row['case_id']
            if case in result: raise ValueError('Duplicate pairing case')
            result[case]=row
        return result
    left,right=index(deterministic),index(stochastic)
    if left.keys()!=right.keys(): raise ValueError('Different pairing cases')
    deltas=[]
    for case,a in left.items():
        b=right[case]
        if any(a.get(k)!=b.get(k) for k in ('engine_seed','slot')): raise ValueError('Pairing seed/slot mismatch')
        if any(a.get(k) is None for k in ('engine_seed','slot')): raise ValueError('Missing pairing identity')
        if type(a.get(key)) in (int,float) and type(b.get(key)) in (int,float): deltas.append(b[key]-a[key])
    return {'status':'PAIRED_CASES','n_pairs':len(deltas),field:round(statistics.mean(deltas),4) if deltas else None}
