"""Bounded student-only evaluation; shadow baseline never sends commands."""
import argparse
import collections
import json
import os
import pathlib
import sys
import time
import traceback

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.append(str(ROOT/'.venv-rl/Lib/site-packages'))
import torch
from structured_control.contract import VERSION,Vocabulary,SnapshotBuilder,intent_of,FAMILIES
from structured_control.policy import load_checkpoint
from structured_control.collect_serial import execute_one
from dual_channel import run_teacher as common
from dual_channel.data import sanitized,write_json,sha

# Teacher-episode construction-yard cell. All 7 reviewed PLACE labels are this cell
# plus a 5-cell ring (teacher intent = base_cell + 5*dir), stored absolute by the
# frozen contract. The optional diagnostic adapter re-expresses a model anchor in
# the current episode frame; it is not a contract or training change.
TRAIN_BASE_CELL=(89,126)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True);p.add_argument('--ledger',required=True)
    p.add_argument('--authorization',default='STRUCTURED-STUDENT-01')
    p.add_argument('--checkpoint',default=str(ROOT/'reports/structured-bc-smoke-2026-11-02/trained.pt'))
    p.add_argument('--execute',action='store_true');p.add_argument('--place-frame-adapter',action='store_true');a=p.parse_args()
    out=pathlib.Path(a.out).resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=True)
    common.LEDGER=pathlib.Path(a.ledger).resolve()
    game=ROOT/'test-game-v3-teacher';seconds=60;authorization=a.authorization
    ckpt=pathlib.Path(a.checkpoint).resolve();ckpt.relative_to(ROOT)
    initial=ROOT/'reports/structured-control-v1-2026-11-02/INITIAL_UNTRAINED.pt'
    vocab=Vocabulary(json.loads((ROOT/'_runs_g0l1/v3-structured-01/vocabulary.json').read_text(encoding='utf-8'))['names'])
    torch.set_num_threads(2);torch.manual_seed(20261102)
    model=load_checkpoint(ckpt,vocab).eval();baseline=load_checkpoint(initial,vocab).eval()
    pre=common.preflight(out,game,14621,seconds,authorization)
    pre.update(model_sha256=sha(ckpt),baseline_sha256=sha(initial))
    pre['checks']['new_student_trace']=not(out/'student.jsonl').exists()
    # Replay real snapshots to check decoding and finite recurrent inference before launching.
    h=hb=None;count=0;train_cy=None
    with torch.no_grad():
        for line in (ROOT/'_runs_g0l1/v3-structured-01/structured/transitions.jsonl').read_text(encoding='utf-8').splitlines():
            s=json.loads(line)['snapshot'];r=model([s],h,deterministic=True);h=r['h_next']
            b=baseline([s],hb,deterministic=True);hb=b['h_next']
            intent_of(s,r['actions'][0]);intent_of(s,b['actions'][0])
            assert torch.isfinite(r['log_prob']).all() and torch.isfinite(h).all()
            train_cy=next((list(e['object']['cell']) for e in s['entities'] if e['friendly']
                and 'Construction Yard' in e['object'].get('type_name','') and e['object'].get('on_map')),train_cy)
            count+=1
    pre['checks']['offline_decoding_218']=count==218
    if a.place_frame_adapter:pre['checks']['frame_adapter_train_base']=train_cy==list(TRAIN_BASE_CELL)
    pre['frame_adapter']='PLACE_ABSOLUTE_TO_BASE_RELATIVE_DIAGNOSTIC' if a.place_frame_adapter else 'NONE'
    pre['pass']=all(pre['checks'].values());write_json(out/'PREFLIGHT.json',pre)
    if not pre['pass']:print(json.dumps(pre));return 3
    os.environ.update(DSH_V3_CAPTURE='1',DSH_EVENT_CAPTURE='1',DSH_COMMAND_CAPTURE='1',
        DSH_COMMAND_CAPTURE_DIR=str(out/'command-capture'),DSH_MAP_DIAG_DIR=str(out/'map-diag'),
        DSH_LOCK_DIR=str(ROOT/'build'),DSH_RPC_TIMEOUT_S='15')
    from dsh_rl.full_env import RA2FullEnv
    from dsh_rl.env import RA2MicroEnv
    from dsh_episode import lockfile
    RA2MicroEnv.RESET_RETRY_DELAYS=()
    held=lockfile.acquire(ROOT/'build/dsh-game.lock');lockfile.release(held)
    class StudentEnv(RA2FullEnv):
        def _control_metadata(self):
            return VERSION,{'control_mode':'structured_student_deterministic','student_model_loaded':True,
                'training_started':False,'model_sha256':sha(ckpt),'teacher_decisions':False,
                'frame_adapter':('PLACE_ABSOLUTE_TO_BASE_RELATIVE_DIAGNOSTIC' if a.place_frame_adapter else 'NONE')}
    env=StudentEnv(out/'episodes',max_seconds=seconds,decision_seconds=.5,game_dir=game,port=14621,
        kill_stale=False,protection={'enabled':True,'stall_timeout_s':5,'hard_step_timeout_s':30,'screenshot_interval_s':30})
    if not a.execute:env.loop.close();print('PREFLIGHT PASS; 218 decoded; no game launched');return 0
    common.consume(authorization,out)
    result={'status':'STARTING','game_launches':0,'student_model_loaded':True,'training_started':False,
        'model_sha256':sha(ckpt),'source_sha256':sha(__file__),'baseline_is_shadow_only':True,
        'no_teacher_decisions':True,'wall_cap_seconds':120,'simulation_cap_seconds':seconds,
        'frame_adapter':'PLACE_ABSOLUTE_TO_BASE_RELATIVE_DIAGNOSTIC' if a.place_frame_adapter else 'NONE'}
    write_json(out/'RESULT.json',result);failure=None;rows=[];counts=collections.Counter();h=hb=None
    start=time.monotonic()
    try:
        env.reset();episode=env.writer.episode_id
        result.update(status='RUNNING',game_launches=1,game_pid=env.game_pid,episode_id=episode)
        write_json(out/'RESULT.json',result)
        assert Vocabulary.from_catalog(env.full.all_types.values()).manifest()==vocab.manifest(),'live vocabulary mismatch'
        builder=SnapshotBuilder(vocab,episode);previous=None;serious=0
        with (out/'student.jsonl').open('x',encoding='utf-8') as f,torch.no_grad():
            while env.kind=='running':
                if time.monotonic()-start>120:
                    result['stop_reason']='WALL_BUDGET';break
                before=sanitized(env.current);s=builder.build(before,env.full,previous)
                r=model([s],h,deterministic=True);h=r['h_next']
                b=baseline([s],hb,deterministic=True);hb=b['h_next']
                model_action=r['actions'][0];action=model_action;adapter=None
                if a.place_frame_adapter and action['family']==3:
                    cy=next((list(e['object']['cell']) for e in s['entities'] if e['friendly']
                        and 'Construction Yard' in e['object'].get('type_name','') and e['object'].get('on_map')),None)
                    if cy is not None:
                        delta=(int(cy[0])-TRAIN_BASE_CELL[0],int(cy[1])-TRAIN_BASE_CELL[1])
                        action={**action,'x':min(max(int(action['x'])+delta[0],0),s['map_width']-1),
                            'y':min(max(int(action['y'])+delta[1],0),s['map_height']-1)}
                        adapter={'train_base':list(TRAIN_BASE_CELL),'current_base':[int(cy[0]),int(cy[1])],
                            'delta':list(delta),'model_anchor':[int(model_action['x']),int(model_action['y'])],
                            'adapted_anchor':[int(action['x']),int(action['y'])]}
                intent=intent_of(s,action)
                did=f'{episode}:student:{len(rows):05d}'
                execution=env._run(execute_one(env,s,{'action':action,'intent':intent,'role':'student_network'},did),name='student')
                row={'decision_id':did,'source':'structured_student','snapshot':s,'action':action,'model_action':model_action,
                    'frame_adapter':adapter,'intent':intent,
                    'shadow_initial_action':b['actions'][0],'log_prob':float(r['log_prob'][0]),
                    'recurrent_reset':not rows,'next_observation':sanitized(env.current),'execution':execution,
                    'formal_training_eligible':False}
                f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();rows.append(row)
                c=execution['classification'];counts[c]+=1
                previous={'action':action,'intent':intent,'outcome':c}
                serious=0 if c=='ACCEPTED' else serious+int(c not in ('NOOP','STALE_REJECTED'))
                if c=='TRANSPORT_FAILURE' or serious>=3:raise RuntimeError('command failure threshold; no retry')
        result['status']='FINISHED'
    except BaseException as exc:
        failure=f'{type(exc).__name__}: {exc}'
        result.update(status='FAILED',failure=failure,traceback=traceback.format_exc())
    finally:
        result.update(decisions=len(rows),command_outcomes=dict(counts),
            action_families=dict(collections.Counter(FAMILIES[r['action']['family']] for r in rows)),
            shadow_action_disagreements=sum(r['model_action']!=r['shadow_initial_action'] for r in rows),
            frame_adapters_applied=sum(r['frame_adapter'] is not None for r in rows),
            accepted_is_not_effect=True,elapsed_wall_seconds=time.monotonic()-start)
        failure=common.finish_attempt(env,out,result,failure=failure)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 1 if failure else 0

if __name__=='__main__':sys.exit(main())
