"""Rebuild every serial model input from raw observations and actual past actions."""
import argparse
import json
import pathlib
import sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from structured_control.contract import Vocabulary,SnapshotBuilder,intent_of,FAMILIES
from structured_control.data import verify
from ai_teacher.collector_offline_replay import load_spec
from dual_channel.collect_formation import rejection_class
from dual_channel.data import write_json,sha


def advance_production_guard(spec,row):
    """Replay existing RA2FullEnv._commands local guard from real past ACKs.

    This is a recorder-side reconstruction, not a new production rule. See
    protected scripts/dsh_rl/full_env.py:306-323. No mask is read from the row.
    """
    intent=row.get('intent') or {}
    if intent.get('action')!='PRODUCE':return
    acks=[a for a in row['execution']['ack_records'] if a.get('type')=='action']
    if not acks:return
    slot=spec.slot_by_ptr[intent['production_type_ptr']];error=acks[0].get('error')
    if error and 'unbuildable' in error:spec.failed[slot]=spec._signature(row['observation'])
    elif not error:spec.sent_until[slot]=row['observation']['t']+3.


def audit(run):
    run=pathlib.Path(run);checked=verify(run/'structured')
    spec,_,_=load_spec(run);vocab=Vocabulary.from_catalog(spec.all_types.values())
    raw_vocab=json.loads((run/'vocabulary.json').read_text(encoding='utf-8'))
    rows=[json.loads(l) for l in (run/'structured/transitions.jsonl').read_text(encoding='utf-8').splitlines()]
    errors=[];previous=None;rebuilt=0;placement=[];maximum=0
    if raw_vocab!=vocab.manifest():errors.append('frozen vocabulary differs from catalog')
    builder=SnapshotBuilder(vocab,rows[0]['snapshot']['episode'])
    for r in rows:
        snap=builder.build(r['observation'],spec,previous)
        if snap!=r['snapshot']:errors.append(f"reconstructed snapshot differs: {r['decision_id']}")
        else:rebuilt+=1
        if intent_of(snap,r['action'])!=r['intent']:errors.append('decoded intent differs')
        execution=r['execution'];native=execution['native_commands'];acks=execution['ack_records']
        errors_text=[a.get('error') for a in acks if a.get('error')]
        skipped=any(a.get('event')=='order_skipped' for a in acks)
        c=('NOOP' if r['action']['family']==0 else rejection_class(errors_text[0] if errors_text else None,skipped))
        if c=='ACCEPTED' and (len(native)!=1 or not native[0].get('transport_result',{}).get('ok')):c='UNKNOWN'
        if c!=execution['classification']:errors.append('outcome does not match raw ACK/send')
        if c=='ACCEPTED':maximum=max(maximum,len(native[0].get('actor_unit_ids') or []))
        if r['action']['family']==3 and native:
            placement.append({'decision_id':r['decision_id'],'proposed_anchor_cell':r['intent']['anchor'],
                'native_resolved_world_coordinates':native[0]['coordinates'],
                'actor_eligible':False,'applied_status':'UNKNOWN'})
        previous={'action':r['action'],'intent':r['intent'],'outcome':execution['classification']}
        advance_production_guard(spec,r)
    return {'schema':'structured-independent-audit/1','pass':checked['pass'] and not errors,
        'source_sha256':sha(run/'structured/transitions.jsonl'),'reconstructed_snapshots':rebuilt,
        'errors':errors,'data_verification':checked,'maximum_accepted_group':maximum,
        'placement_proposed_resolved':placement,'vocabulary_sha256':vocab.sha256,
        'limits':['RPC acceptance is not APPLIED/effect','Scripted teacher,not sampled policy behavior',
                  'Input history and existing3s production guards reconstructed from actual past actions/ACKs only',
                  'No formal BC/PPO admission']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();out=pathlib.Path(a.out)
    if out.exists():raise FileExistsError('new audit output required')
    result=audit(a.run);write_json(out,result)
    print(json.dumps({k:result[k] for k in ('pass','reconstructed_snapshots','errors','maximum_accepted_group')},ensure_ascii=True))
