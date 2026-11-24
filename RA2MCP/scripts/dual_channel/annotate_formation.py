"""Export raw teacher annotations without silently flattening action batches."""
import argparse
import collections
import json
import pathlib
import sys

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from dual_channel.audit_formation import audit,load_rows
from dual_channel.data import write_json,sha


def export(run,out):
    run,out=pathlib.Path(run),pathlib.Path(out)
    if out.exists():raise FileExistsError('new annotation directory required')
    result=audit(run)
    if not result['association_integrity_pass'] or not result['capture_complete'] or result['failure']:
        raise ValueError('Only intact captures without environmental/collector failure may enter this candidate export')
    source=run/'formation/transitions.jsonl';records=load_rows(source)
    out.mkdir(parents=True)
    counts=collections.Counter();eligible_decisions=set()
    with (out/'command_annotations.jsonl').open('x',encoding='utf-8') as f:
        for row in records:
            outcomes={o['action_id']:o for o in row.get('command_outcomes',[])}
            for i,command in enumerate(row['plan']['commands']):
                aid=f"{row['decision_id']}:command:{i:02d}"
                outcome=outcomes.get(aid,{'classification':'UNKNOWN'})
                single=bool(row['decision_301_eligible'] and len(row['plan']['commands'])==1
                            and command['mapping_301']['classification']=='EXACT' and outcome['classification']=='ACCEPTED')
                annotation={'schema':'ra2.raw-teacher-annotation/1','source':'formation_teacher',
                    'source_trace_sha256':result['source_trace_sha256'],'decision_id':row['decision_id'],'action_id':aid,
                    'observation_frame':row['observation_frame'],'next_frame':row['next_frame'],
                    'role':command['role'],'skill_id':command['skill_id'],'raw_intent':command['intent'],
                    'mapping_301':command['mapping_301'],'native_outcome':outcome['classification'],
                    'single_step_301_label_candidate':single,'formal_training_eligible':False,
                    'ppo_eligible':False,'effect_supervision_eligible':False,
                    'reason':'candidate label only; quality, feature reader, episode split and training approval pending'}
                counts['commands']+=1;counts[outcome['classification']]+=1
                counts[command['mapping_301']['classification']]+=1
                if single:eligible_decisions.add(row['decision_id'])
                f.write(json.dumps(annotation,ensure_ascii=False)+'\n')
    manifest={'schema':'ra2.raw-teacher-annotation-manifest/1','status':'CANDIDATE_REVIEW_REQUIRED',
        'source_run':str(run),'source_episode':records[0]['decision_id'].split(':formation:')[0],
        'source_trace_sha256':sha(source),'counts':dict(counts),
        'single_step_301_label_candidates':len(eligible_decisions),
        'raw_group_commands_not_encoded_as_301':counts['UNSUPPORTED_FOR_301'],
        'formal_training_eligible':False,'ppo_eligible':False,
        'split':'ONE_SOURCE_EPISODE_UNASSIGNED; no row-random train/val/test split',
        'feature_reader':'RAW_OBSERVATION_REFERENCES_ONLY; no fabricated 1801-history or V2-token batch',
        'files':{'command_annotations.jsonl':{'sha256':sha(out/'command_annotations.jsonl'),
                'size':(out/'command_annotations.jsonl').stat().st_size}}}
    write_json(out/'manifest.json',manifest)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();print(json.dumps(export(a.run,a.out),ensure_ascii=True))
