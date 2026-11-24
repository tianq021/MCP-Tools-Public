"""A visible, bounded model-only match. No teacher or rule fallback is invoked."""
from pathlib import Path
import argparse,asyncio,collections,json,os,sys,time,traceback
import numpy as np
import torch
ROOT=Path('E:/RA2MCP');HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(ROOT/'scripts'),str(ROOT/'scripts/dsh_train'),str(ROOT/'vendor/pyra2yr')]
sys.path.append(str(ROOT/'.venv-rl/Lib/site-packages'))
from .model import load
from .profiles import make_features
from .contract import VERSION,Vocabulary,SnapshotBuilder,FAMILIES,intent_of
from .cell_map import MASK_VERSION,read_native_cells
from .lifecycle import finish,exit_code,close_checked
from .execution import execute_once
from .runtime import resolve_runtime
from dual_channel.collect_formation import rejection_class
from dual_channel.data import sanitized,write_json,sha

async def execute(env,snapshot,action,intent,decision):
    if intent!=intent_of(snapshot,action):
        raise ValueError('intent differs from validated model action')
    from dsh_m1 import command_capture
    return await execute_once(env,snapshot,intent,decision,command_capture)

def main(out):
    plan=json.loads((out/'MATCH_PLAN.json').read_text(encoding='utf-8'));parent=out.parent
    if plan.get('legality_mask_version')!=MASK_VERSION:raise ValueError('Fresh match plan must name the new mask version')
    if sha(parent/'ops.pt')!=plan['model_sha256']:raise ValueError('checkpoint hash mismatch')
    game,port,instance=resolve_runtime(plan,ROOT)
    torch.set_num_threads(2);torch.manual_seed(20261103);model,vocab,checkpoint=load(parent/'ops.pt',allow_legacy_mask=plan.get('allow_legacy_mask',False))
    if 'input_profile' in plan and plan['input_profile']!=getattr(model,'input_profile',None):
        raise ValueError('Match plan and checkpoint input profiles differ')
    os.environ.update(DSH_V3_CAPTURE='1',DSH_EVENT_CAPTURE='1',DSH_COMMAND_CAPTURE='1',
       DSH_COMMAND_CAPTURE_DIR=str(out/'command-capture'),DSH_MAP_DIAG_DIR=str(out/'map-diag'),
       DSH_LOCK_DIR=str(ROOT/'build'),DSH_RPC_TIMEOUT_S='15')
    from dsh_rl.full_env import RA2FullEnv
    from dsh_rl.env import RA2MicroEnv
    RA2MicroEnv.RESET_RETRY_DELAYS=()
    class OpsEnv(RA2FullEnv):
        def _control_metadata(self):
            return VERSION,{'control_mode':'v2_ops_model','model_loaded':True,'checkpoint':str(parent/'ops.pt'),'model_sha256':sha(parent/'ops.pt'),
                'fixed_economy':False,'warmup':False,'teacher_decisions':False,'rule_policy_fallback':False,'online_training':False}
        async def _commands(self,intents,source):
            if source!='v2_ops_model':raise RuntimeError('Non-model command source refused: '+source)
            return await super()._commands(intents,source)
        async def _economy(self):raise RuntimeError('Scripted economy disabled in model-only test')
    limit=float(plan.get('max_seconds',300))
    if not 50<limit<=600:raise ValueError('bounded match duration must be >50 and <=600 seconds')
    env=OpsEnv(out/'episodes',max_seconds=limit,decision_seconds=.5,game_dir=game,port=port,instance=instance,kill_stale=False,render_off=False,
        protection={'enabled':True,'stall_timeout_s':8,'hard_step_timeout_s':30,'screenshot_interval_s':30})
    result={'status':'STARTING','bridge_port':port,'instance':instance,'game_dir':str(game),'training_started':False,'teacher_decisions':False,'rule_policy_fallback':False,'render_off':False,
            'checkpoint':str(parent/'ops.pt'),'model_sha256':sha(parent/'ops.pt'),'game_launches':0,'native_commands':0,'decisions':0}
    write_json(out/'RESULT.json',result);error=None;rows=0;outcomes=collections.Counter();families=collections.Counter();native=collections.Counter();placed=set();h=None;previous=None;started=time.monotonic()
    try:
        with (out/'ONE_NATIVE_LAUNCH_CLAIM').open('x',encoding='utf-8') as claim:claim.write(str(time.time()))
        env.reset();env.action_source='v2_ops_model';env.action_version=VERSION
        actual=Vocabulary.from_catalog(env.full.all_types.values());assert actual.manifest()==vocab.manifest(),'Live vocabulary mismatch'
        result.update(status='RUNNING',game_pid=env.game_pid,game_launches=1,episode_id=env.writer.episode_id)
        cells=env._run(read_native_cells(env.runner.client,env.writer.episode_id,env.current['frame']),name='native_cell_map')
        write_json(out/'CELL_MAP.json',cells.payload())
        result['legality_mask_version']=MASK_VERSION
        builder=SnapshotBuilder(vocab,env.writer.episode_id,cells);features=make_features(model,env.full)
        # Full raw state is used only to record the opponent configuration, never to choose actions.
        result['initial_houses']=[{'name':house.name,'country':house.faction,'country_index':house.type_array_index,'current_player':house.current_player,'money':house.money} for house in env.state.houses]
        write_json(out/'RESULT.json',result)
        with (out/'DECISIONS.jsonl').open('x',encoding='utf-8') as log, (out/'ATTEMPTS.jsonl').open('x',encoding='utf-8') as journal,torch.inference_mode():
            serious=0
            while env.kind=='running':
                if time.monotonic()-started>750:result['stop_reason']='WALL_LIMIT';break
                if (out/'STOP').exists():result['stop_reason']='USER_STOP';break
                before=sanitized(env.current);snapshot=builder.build(before,env.full,previous);x,current=features(snapshot,before)
                chosen=model([snapshot],[x],[current],h,deterministic=True);h=chosen['h_next'];action=chosen['actions'][0];intent=intent_of(snapshot,action)
                decision=f'{env.writer.episode_id}:v2ops:{rows:05d}'
                journal.write(json.dumps({'event':'ATTEMPT','decision_id':decision,'snapshot':snapshot,'action':action,'intent':intent},ensure_ascii=False)+'\n');journal.flush();os.fsync(journal.fileno())
                execution=env._run(execute(env,snapshot,action,intent,decision),name='v2_ops_decision')
                after=sanitized(env.current) if execution['next_frame'] is not None else {};family=FAMILIES[action['family']];families[family]+=1;outcomes[execution['classification']]+=1
                if execution['native_commands']:native[family]+=len(execution['native_commands'])
                placed.update(obj['type_name'] for obj in after.get('own_objects',[]) if obj.get('abstract')=='ABSTRACT_TYPE_BUILDING' and obj.get('on_map') and not obj.get('in_limbo'))
                row={'decision_id':decision,'source':'v2_ops_model','snapshot':snapshot,'forecast_features':x.tolist(),'current_log_counts':current.tolist(),
                     'action':action,'intent':intent,'forecast_counts_30s':chosen['forecast'][0][0].tolist(),'elimination_score_60s':float(chosen['forecast'][2][0]),
                     'execution':execution,'next_frame':execution['next_frame'],'teacher_decisions':False,'rule_policy_fallback':False}
                log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush();os.fsync(log.fileno());rows+=1
                journal.write(json.dumps({'event':'RESULT','decision_id':decision,'classification':execution['classification'],'fatal':execution['fatal']})+'\n');journal.flush();os.fsync(journal.fileno())
                if execution['fatal']:raise RuntimeError(execution.get('error','execution invariant failed'))
                previous={'action':action,'intent':intent,'outcome':execution['classification']}
                result.update(decisions=rows,native_commands=sum(native.values()),families=dict(families),native_families=dict(native),outcomes=dict(outcomes),frame=after['frame'],
                    building_types_observed=sorted(placed),money=after.get('money'),visible_enemies=len(after.get('visible_enemies',[])),heartbeat=time.time(),elapsed_seconds=time.monotonic()-started)
                if rows%10==0:write_json(out/'RESULT.json',result);print(json.dumps({key:result[key] for key in ('decisions','native_commands','families','building_types_observed','frame','elapsed_seconds')}),flush=True)
                c=execution['classification'];serious=0 if c=='ACCEPTED' else serious+int(c not in ('NOOP','STALE_REJECTED'))
                if c=='TRANSPORT_FAILURE' or serious>=3:raise RuntimeError('Native command rejection threshold; no rule fallback')
        finish(result,env.kind)
    except BaseException as exc:
        error=f'{type(exc).__name__}: {exc}';result.update(status='FAILED',error=error,traceback=traceback.format_exc())
    finally:
        result.update(decisions=rows,native_commands=sum(native.values()),families=dict(families),native_families=dict(native),outcomes=dict(outcomes),building_types_observed=sorted(placed),elapsed_seconds=time.monotonic()-started)
        if getattr(env,'current',None):result['final_observation']=sanitized(env.current)
        try:close_checked(env)
        except Exception as exc:result['cleanup_error']=str(exc);result['status']='FAILED'
        write_json(out/'RESULT.json',result)
    print(json.dumps({key:result.get(key) for key in ('status','end_kind','error','decisions','families','native_families','building_types_observed')},ensure_ascii=False),flush=True)
    return exit_code(result)

if __name__=='__main__':sys.exit(main(Path(sys.argv[1])))
