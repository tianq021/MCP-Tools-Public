"""One authorized game: observe -> one structured teacher command -> next observe."""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import traceback

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from structured_control.contract import VERSION,Vocabulary,SnapshotBuilder,intent_of
from structured_control.serial_teacher import SerialTeacher
from structured_control.data import SCHEMA,SerialWriter,verify
from dual_channel import run_teacher as common
from dual_channel.collect_formation import execute_plan
from dual_channel.data import sanitized,write_json,sha

LEDGER=ROOT/'coordination/approvals/STRUCTURED_SERIAL_RUN_AUTHORIZATION.json'


async def execute_one(env,snapshot,choice,did):
    command={'intent':choice['intent'],'role':choice['role'],'mapping_301':{'action_idx':None}}
    plan={'observation_frame':snapshot['frame'],'commands':[command] if choice['intent'] else []}
    result=await execute_plan(env,plan,did)
    if len(result['native_commands'])>1:raise RuntimeError('single command emitted multiple native sends')
    if not choice['intent']:classification='NOOP'
    elif len(result['command_outcomes'])!=1:classification='UNKNOWN'
    else:classification=result['command_outcomes'][0]['classification']
    if classification=='ACCEPTED' and len(result['native_commands'])!=1:classification='UNKNOWN'
    return {**result,'classification':classification,'applied_status':'UNKNOWN',
            'placement_resolution':'NATIVE_PLACEQUERY_ANCHOR_RESOLUTION' if choice['action']['family']==3 else 'NONE'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True);p.add_argument('--authorization',required=True)
    p.add_argument('--game-dir',default=str(ROOT/'test-game-v3-teacher'));p.add_argument('--seconds',type=float,default=120)
    p.add_argument('--port',type=int,default=14621);p.add_argument('--execute',action='store_true')
    a=p.parse_args();out=pathlib.Path(a.out).resolve();out.relative_to(ROOT);game=pathlib.Path(a.game_dir).resolve()
    out.mkdir(exist_ok=True,parents=True);common.LEDGER=LEDGER
    pre=common.preflight(out,game,a.port,a.seconds,a.authorization)
    pre['checks']['bounded_serial_seconds']=0<a.seconds<=120
    pre['checks']['serial_directory_new']=not(out/'structured').exists()
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
    class SerialEnv(RA2FullEnv):
        def _control_metadata(self):
            return VERSION,{'control_mode':'structured_serial_teacher','student_model_loaded':False,'training_started':False}
    env=SerialEnv(out/'episodes',max_seconds=a.seconds,decision_seconds=.5,game_dir=game,port=a.port,kill_stale=False,
        protection={'enabled':True,'stall_timeout_s':5,'hard_step_timeout_s':30,'screenshot_interval_s':30})
    if not a.execute:env.loop.close();print('PREFLIGHT PASS; no game launched');return 0
    common.consume(a.authorization,out)
    result={'authorization':a.authorization,'status':'STARTING','game_launches':0,'training_started':False,
        'student_model_loaded':False,'action_contract':VERSION,'source_sha256':sha(pathlib.Path(__file__))}
    write_json(out/'RESULT.json',result);writer=None;failure=None
    try:
        env.reset();episode=env.writer.episode_id
        result.update(status='RUNNING',game_launches=1,game_pid=env.game_pid,episode_id=episode)
        write_json(out/'RESULT.json',result)
        vocab=Vocabulary.from_catalog(env.full.all_types.values())
        write_json(out/'vocabulary.json',vocab.manifest())
        builder=SnapshotBuilder(vocab,episode);teacher=SerialTeacher()
        writer=SerialWriter(out/'structured',{'schema':SCHEMA,'action_contract':VERSION,'vocabulary':vocab.manifest(),
            'teacher':'serial-formation/1','collector_sha256':result['source_sha256'],
            'group_builder':'fixed-role/1','formal_training_eligible':False,
            'catalog_sha256':sha(out/'public-catalog.json'),'legacy_contract_sha256':sha(out/'action-contract.json'),
            'source_hashes':{name:sha(ROOT/'scripts/structured_control'/name) for name in ('contract.py','serial_teacher.py','data.py','collect_serial.py')},
            'game':pre['game_fingerprints']})
        step=0;previous=None;serious=0
        while env.kind=='running':
            before=sanitized(env.current);snapshot=builder.build(before,env.full,previous)
            choice=teacher.choose(before,env.full,env.starts,env.map_soa,snapshot)
            assert choice['intent']==intent_of(snapshot,choice['action'])
            did=f'{episode}:structured:{step:05d}'
            execution=env._run(execute_one(env,snapshot,choice,did),name='structured_teacher')
            after=sanitized(env.current)
            row={'schema':SCHEMA,'decision_id':did,'observation':before,'next_observation':after,
                'snapshot':snapshot,'action':choice['action'],'intent':choice['intent'],'teacher_proposal':choice,
                'execution':execution,'reward':None,'actor_eligible':False,'formal_training_eligible':False,
                'effect_eligible':False,'ppo_eligible':False,'recurrent':{'reset':step==0,'behavior':'SCRIPTED_NO_NETWORK'},
                'reward_source':'NOT_COMPUTED; protected V4 unchanged'}
            writer.append(row);step+=1
            previous={'action':choice['action'],'intent':choice['intent'],'outcome':execution['classification']}
            c=execution['classification']
            if c=='TRANSPORT_FAILURE':raise RuntimeError('transport failure; no resend')
            if c=='ACCEPTED':serious=0
            elif c not in ('NOOP','STALE_REJECTED'):serious+=1
            if serious>=3:raise RuntimeError('three serious command rejections without accepted command')
            if step%50==0:print(json.dumps({'step':step,'t':after['t'],'action':choice['action'],'native':len(execution['native_commands'])}),flush=True)
        result['status']='FINISHED'
    except BaseException as exc:
        failure=f'{type(exc).__name__}: {exc}';result.update(status='FAILED',failure=failure,traceback=traceback.format_exc())
    finally:
        try:
            if writer:
                writer.finish(getattr(env,'kind','launch_failed'),failure)
                result['verification']=verify(out/'structured')
                if not result['verification']['pass']:failure=failure or 'serial verification failed';result['status']='FAILED'
        except BaseException as exc:
            failure=failure or str(exc);result.update(status='FAILED',finalization_error=str(exc))
        failure=common.finish_attempt(env,out,result,failure=failure)
    print(json.dumps({'status':result['status'],'verification':result.get('verification'),'failure':failure}),flush=True)
    return 1 if failure else 0


if __name__=='__main__':
    if sys.version_info[:2]!=(3,10):raise SystemExit('Use existing Python3.10 F:/conda/envs/python_mmx_LLM/python.exe')
    sys.path.append(str(ROOT/'.venv-rl/Lib/site-packages'))
    sys.exit(main())
