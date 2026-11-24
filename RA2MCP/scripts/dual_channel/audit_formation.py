"""Independent raw intent/native-send association and control-coverage audit."""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from dual_channel.formation_data import verify
from dual_channel.data import write_json,sha


def load_rows(path):
    return [json.loads(l) for l in pathlib.Path(path).read_text(encoding='utf-8').splitlines() if l.strip()]


def audit(run):
    run=pathlib.Path(run)
    checked=verify(run/'formation')
    rows=load_rows(run/'formation/transitions.jsonl')
    native=load_rows(run/'command-capture/action_command_records.jsonl')
    flattened=[c for r in rows for c in r['native_commands']]
    errors=[];filtered=[]
    if native!=flattened:errors.append('trace native records differ from send-capture journal')
    if len({c['command_id'] for c in native})!=len(native):errors.append('duplicate native command id')
    accepted=collections.Counter();outcomes=collections.Counter();max_group=0
    commanded_outside=set();newborn_outside=set();all_commanded=set();assignment_rows=0;assignment_complete=0
    initial_army=set(rows[0]['plan']['all_combat_addresses'])
    per_command_301=collections.Counter();decision_301=0;partial_301_labels=0
    for row in rows:
        commands=row['plan']['commands']
        for p in commands:per_command_301[p['mapping_301']['classification']]+=1
        assigned=[m['addr'] for members in row['plan']['assignments'].values() for m in members]
        if row['plan']['assignments']:
            assignment_rows+=1
            assignment_complete+=len(assigned)==len(set(assigned)) and set(assigned)==set(row['plan']['all_combat_addresses'])
        annotated=row.get('command_outcomes')
        if annotated is None:
            # Run01 predates explicit outcome annotation. Preserve this fact;
            # classify only existing raw ACK error text for the audit.
            acks=row['ack_records']
            annotated=[]
            for i,c in enumerate(commands):
                ack=acks[i] if len(acks)==len(commands) else {}
                err=ack.get('error')
                cls=('STALE_REJECTED' if err and ('object not found' in err or 'no longer exists' in err)
                     else 'OTHER_REJECTED' if err else 'ACCEPTED' if ack.get('type')=='action' else 'UNKNOWN')
                annotated.append({'action_id':f"{row['decision_id']}:command:{i:02d}",'classification':cls})
        outcome_by_id={x['action_id']:x['classification'] for x in annotated}
        outcomes.update(outcome_by_id.values())
        lookup={f"{row['decision_id']}:command:{i:02d}":c for i,c in enumerate(commands)}
        for c in row['native_commands']:
            aid=c['action_id'];planned=lookup.get(aid)
            if planned is None:
                errors.append('native action without matching planned command');continue
            intent=planned['intent'];family=intent['action']
            if c['decision_id']!=row['decision_id'] or c['issued_frame']!=row['observation_frame']:
                errors.append('native decision/frame mismatch')
            if c['action_idx']!=planned['mapping_301']['action_idx']:errors.append('native 301 annotation mismatch')
            expected=set(intent.get('object_addresses') or []);actual=set(c.get('actor_unit_ids') or [])
            if not actual.issubset(expected):errors.append('unrequested actor')
            if actual!=expected:
                filtered.append({'action_id':aid,'requested':sorted(expected),'sent':sorted(actual)})
                if row['decision_301_eligible']:partial_301_labels+=1
            if c['target_id']!=intent.get('target_address'):errors.append('target mismatch')
            if family in ('MOVE','ATTACK') and (c.get('coordinates') or [])[:2]!=intent.get('coordinates'):
                errors.append('coordinate mismatch')
            if family=='PRODUCE' and c['production_type_ptr']!=intent.get('production_type_ptr'):
                errors.append('production mismatch')
            if family=='PLACE' and c['placed_building_ptr']!=intent.get('building_address'):
                errors.append('placement object mismatch')
            if family in ('MOVE','ATTACK','STOP'):
                own={o['addr']:o for o in row['observation']['own_objects']}
                for actor in actual:
                    o=own.get(actor)
                    if o is None or any(n in o['type_name'] for n in ('Miner','Harvester','Construction Vehicle','Engineer','Spy')):
                        errors.append('economic or unknown actor in military order')
            if outcome_by_id.get(aid)=='ACCEPTED' and c.get('transport_result',{}).get('ok'):
                accepted[family]+=1
                max_group=max(max_group,len(actual))
                all_commanded.update(actual)
                outside=actual & set(row['plan']['outside_legacy_slots'])
                commanded_outside.update(outside)
                newborn_outside.update(outside-initial_army)
        if row['decision_301_eligible']:
            decision_301+=1
            if len(commands)!=1 or commands[0]['mapping_301']['classification']!='EXACT':
                errors.append('multi/unsupported command admitted as 301 decision')
        if row['reward'] is not None or row['actor_eligible'] or row['formal_training_eligible']:
            errors.append('raw demonstration incorrectly admitted for PPO/training')
    if partial_301_labels:errors.append('partially filtered command admitted as exact decision')
    summary=json.loads((run/'formation/summary.json').read_text(encoding='utf-8'))
    return {'schema':'ra2.formation-independent-audit/1','association_integrity_pass':checked['pass'] and not errors,
            'source_trace_sha256':sha(run/'formation/transitions.jsonl'),'decisions':len(rows),
            'planned_commands':sum(len(r['plan']['commands']) for r in rows),'native_send_records':len(native),
            'association_errors':errors,'actor_filter_events':filtered,'native_outcomes':dict(outcomes),
            'accepted_native_commands_by_family':dict(accepted),'maximum_accepted_actor_group_size':max_group,
            'distinct_sent_actors':len(all_commanded),'sent_while_outside_8slots':sorted(commanded_outside),
            'newborn_sent_while_outside_8slots':sorted(newborn_outside),
            'assignment_rows':assignment_rows,'complete_assignment_rows':assignment_complete,
            'command_level_301':dict(per_command_301),'single_command_exact_301_candidates':decision_301,
            'max_observed_combat_units':max(r['plan']['combat_units'] for r in rows),
            'terminal':summary['terminal'],'capture_complete':summary['capture_complete'],'failure':summary['failure'],
            'limitations':['RPC acceptance is not APPLIED or damage proof','Raw multi-command decisions are not 301 labels',
                          'Array indexes/addresses are not stable engine unique IDs','N is one run per audit, not a win-rate study']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();out=pathlib.Path(a.out)
    if out.exists():raise FileExistsError('Audit output must be new')
    result=audit(a.run);write_json(out,result)
    print(json.dumps({k:result[k] for k in ('association_integrity_pass','decisions','native_send_records',
        'native_outcomes','maximum_accepted_actor_group_size','association_errors')},ensure_ascii=True))
