"""Fixed per-case evaluation. Start with --plan-only; live runs need their own user authorization.

One native episode per worker process; unique engine seed per case; failed cases
retain their ID, seed and slot. No global process-name cleanup or auto-reset.
"""
from __future__ import annotations
from contextlib import ExitStack
import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
import traceback

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
ROOT=Path(os.environ.get('RA2_EVAL_ROOT',str(HERE.parents[1]))).resolve()
sys.path.insert(0,str(ROOT/'scripts'))
from eval_v22_support import (SCHEMA,PROTOCOL,CleanupError,EvaluationError,FrozenViolation,SeedGuard,
    assert_copies_idle,digest,frozen_file,game_dir,make_plan,owned_games,port_free,retry_cases,seed_value,
    sha256,snapshot_processes,stop_owned,validate_frozen,wait_ports,write_json)
from eval_v22_metrics import episode_metrics,policy_details,summarize

BASE_PORT=int(os.environ.get('RA2YR_BASE_PORT','14621'))
MODELS={
    'r1':ROOT/'reports/rl/stage-A-round1-20261030-153225/model.zip',
    'r4ep004':ROOT/'reports/rl/stage-A-round4-fresh-20261103-122402/checkpoint-ep004.zip',
    'r4final':ROOT/'reports/rl/stage-A-round4-fresh-20261103-122402/model.zip',
}


def policy_sha(model):
    h=hashlib.sha256()
    for name,tensor in sorted(model.policy.state_dict().items()):
        value=tensor.detach().cpu().contiguous()
        h.update(name.encode());h.update(str(value.dtype).encode());h.update(str(tuple(value.shape)).encode())
        h.update(value.numpy().tobytes())
    return h.hexdigest()


def action_family(action):
    if action==0:return 'NOOP'
    if action==1:return 'DEPLOY'
    if action<98:return 'PRODUCE'
    if action<130:return 'PLACE'
    command=(action-130)%19
    return 'STOP' if command==0 else 'ATTACK' if 9<=command<=16 else 'MOVE'


def choose_action(model,obs,mask,mode):
    import numpy as np
    import torch
    with torch.inference_mode():
        action,_=model.predict(obs,deterministic=mode=='deterministic',action_masks=mask)
        tensor,_=model.policy.obs_to_tensor(obs)
        distribution=model.policy.get_distribution(tensor,action_masks=mask)
        probabilities=distribution.distribution.probs[0].cpu().numpy()
    action=int(action)
    if not bool(mask[action]):raise EvaluationError('Policy selected an illegal action')
    argmax=int(probabilities.argmax())
    if mode=='deterministic' and action!=argmax:raise EvaluationError('Predict/distribution disagreement')
    categories={}
    for index,p in enumerate(probabilities):
        name=action_family(index);categories[name]=categories.get(name,0.)+float(p)
    top=np.argsort(probabilities)[-5:][::-1]
    return action,{'action':action,'family':action_family(action),'argmax':argmax,
                   'legal_action_count':int(mask.sum()),
                   'legal_attack_count':sum(bool(mask[a]) for a in range(len(mask)) if action_family(a)=='ATTACK'),
                   'probability_by_family':categories,'top5':[{'action':int(a),'p':float(probabilities[a])} for a in top]}


def run_one_episode(job):
    """Child process: frozen inference only. Preserve cleanup evidence even on reset/step errors."""
    folder=Path(job['attempt_dir']);model_path=Path(job['model']);env=None;model=None
    status={'case_id':job['case_id'],'attempt':job['attempt'],'engine_seed':job['engine_seed'],
            'slot':job['slot'],'ok':False};errors=[];frozen={};inputs=[];masks_saved=[];actions=[]
    try:
        from sb3_contrib import MaskablePPO
        import numpy as np
        import torch
        torch.set_num_threads(1)
        sys.path.insert(0,str(ROOT/'scripts/dsh_train'))
        from dsh_rl.full_env import RA2FullEnv
        os.environ['DSH_ACTION_SPACE_VERSION']=PROTOCOL['action_space_version']
        os.environ['DSH_TARGET_CANDIDATE_RANGE']=str(PROTOCOL['target_candidate_range'])
        os.environ['DSH_TARGET_NEAREST_K']=str(PROTOCOL['target_nearest_k'])
        frozen_file(model_path,job['model_sha256'])
        frozen['model_sha256_before']=sha256(model_path)
        model=MaskablePPO.load(str(model_path),device='cpu')
        if getattr(model.action_space,'n',None)!=301 or model.observation_space.shape!=(1801,):
            raise EvaluationError('Model action/observation contract differs from fixed protocol')
        model.set_random_seed(job['engine_seed'])
        model.policy.set_training_mode(False)
        frozen['optimizer_updates_before']=model._n_updates
        frozen['policy_sha256_before']=policy_sha(model)
        spawn=Path(job['game_dir'])/'spawn.ini'
        if seed_value(spawn.read_bytes())!=job['engine_seed'] or sha256(spawn)!=job['seeded_spawn_sha256']:
            raise EvaluationError('Engine config seed/hash differs before reset')
        env=RA2FullEnv(folder/'workers'/str(job['slot'])/'episodes',job['episode_seconds'],.5,
                       game_dir=Path(job['game_dir']),port=job['port'],instance=job['slot'],kill_stale=False,render_off=job.get('render_off',True))
        if job.get('single_launch'):env.RESET_RETRY_DELAYS=()
        obs,info=env.reset(seed=job['engine_seed'])
        status['native_episode_id']=info['episode_id']
        terminated=truncated=False
        with (folder/'POLICY_TELEMETRY.jsonl').open('x',encoding='utf-8') as telemetry:
            for step in range(int(job['episode_seconds']/.5)+600):
                mask=env.action_masks()
                action,record=choose_action(model,obs,mask,job['mode'])
                inputs.append(np.array(obs,dtype=np.float32,copy=True));masks_saved.append(np.array(mask,dtype=bool,copy=True));actions.append(action)
                record.update(step=step,case_id=job['case_id'],observation_frame=env.current.get('frame'))
                try:
                    obs,_,terminated,truncated,feedback=env.step(action)
                    record['action_executed']=feedback.get('action_executed')
                    record['step_status']='returned'
                except BaseException:
                    record['step_status']='error_delivery_unknown'
                    telemetry.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n');telemetry.flush()
                    raise
                telemetry.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n');telemetry.flush()
                if terminated or truncated:break
        if not (terminated or truncated):raise EvaluationError('Step budget exhausted without a terminal outcome')
        status['loop_terminal']=True
    except BaseException as exc:
        errors.append({'phase':'inference','type':type(exc).__name__,'error':traceback.format_exc()[-4000:]})
    finally:
        try:
            claims=owned_games(snapshot_processes(),[os.getpid()],[job['game_dir']])
            write_json(folder/'OWNED_PROCESSES.json',claims)
        except BaseException as exc:
            errors.append({'phase':'ownership','type':'CleanupError','error':str(exc)})
        if env is not None:
            try:env.close()
            except BaseException as exc:errors.append({'phase':'close','type':'CleanupError','error':str(exc)})
        try:
            if inputs:
                import numpy as np
                np.savez_compressed(folder/'POLICY_INPUTS.npz',observations=np.asarray(inputs),masks=np.asarray(masks_saved),actions=np.asarray(actions))
            frozen['model_sha256_after']=sha256(model_path)
            if model is not None:
                frozen['optimizer_updates_after']=model._n_updates
                frozen['policy_sha256_after']=policy_sha(model)
            status['frozen_verified']=validate_frozen(frozen,job['model_sha256'])
        except BaseException as exc:errors.append({'phase':'frozen','type':'FrozenViolation','error':str(exc)})
    status['frozen']=frozen
    if not errors:
        try:
            result_path=folder/'workers'/str(job['slot'])/'episodes'/f"{status['native_episode_id']}.result.json"
            result=json.loads(result_path.read_text(encoding='utf-8-sig'))
            trace=result_path.with_name(result_path.name.replace('.result.json','.jsonl'))
            with trace.open(encoding='utf-8-sig') as f:meta=json.loads(next(f))['meta']
            actual=meta['environment']
            if 'render_off' in job and (actual.get('render_off') or {}).get('enabled') is not job['render_off']:
                raise EvaluationError('Recorded rendering mode differs')
            _,control=policy_details(meta)
            if control.get('fixed_economy') is not False:raise EvaluationError('Recorded control mode is not full')
            for key in ('action_space_version','target_candidate_range','target_nearest_k'):
                if control.get(key)!=PROTOCOL[key]:raise EvaluationError(f'Recorded action contract differs: {key}')
            if seed_value(actual['spawn_ini'].encode('utf-8'))!=job['engine_seed']:
                raise EvaluationError('Recorded launch metadata has wrong engine config seed')
            if actual['spawn_ini_sha256']!=job['seeded_spawn_sha256']:
                raise EvaluationError('Recorded launch config hash differs')
            for key in ('map_sha256','game_exe_sha256','bridge_dll_sha256'):
                if actual.get(key)!=job['expected_environment'][key]:raise EvaluationError(f'Recorded launch {key} differs')
            if actual.get('bridge_port')!=job['port']:raise EvaluationError('Recorded bridge port differs')
            if result.get('failure') or result.get('learning_excluded'):raise EvaluationError('Failure/excluded episode cannot be accepted')
            row=episode_metrics(trace,result)
            row.update(case_id=job['case_id'],engine_seed=job['engine_seed'],slot=job['slot'],attempt=job['attempt'],
                       result_path=str(result_path),trace_path=str(trace),result_sha256=sha256(result_path),
                       trace_sha256=sha256(trace),seeded_spawn_sha256=job['seeded_spawn_sha256'])
            row['launch_environment']={key:actual[key] for key in ('map_sha256','game_exe_sha256','bridge_dll_sha256','bridge_port','spawn_ini_sha256')}
            status.update(ok=True,row=row,recorded_engine_seed=job['engine_seed'])
        except BaseException as exc:errors.append({'phase':'result','type':type(exc).__name__,'error':str(exc)})
    status['errors']=errors
    status['fatal']=any(e['type'] in ('FrozenViolation','CleanupError') for e in errors)
    write_json(folder/'ATTEMPT_RESULT.json',status)


def run_wave(group,attempt,model,model_sha,out,args,deadline):
    ctx=mp.get_context('spawn');processes=[];folders=[];claims=[]
    allowed=[str(game_dir(ROOT,c['slot']).resolve()) for c in group]
    ports=[BASE_PORT+c['slot'] for c in group]
    assert_copies_idle(snapshot_processes(),allowed)
    wait_ports(ports,timeout=0)
    with ExitStack() as stack:
        try:
            for case in group:
                folder=out/'cases'/case['case_id']/f'attempt-{attempt:02d}'
                folder.mkdir(parents=True,exist_ok=False);folders.append(folder)
                spawn=game_dir(ROOT,case['slot'])/'spawn.ini'
                stack.enter_context(SeedGuard(spawn,case['engine_seed'],folder/'seed-config'))
                job={**case,'attempt':attempt,'attempt_dir':str(folder),'model':str(model),'model_sha256':model_sha,
                     'game_dir':str(game_dir(ROOT,case['slot']).resolve()),'port':BASE_PORT+case['slot'],
                     'episode_seconds':args.episode_seconds,'mode':args.mode,'seeded_spawn_sha256':sha256(spawn),
                     'render_off':not getattr(args,'visible',False),'single_launch':getattr(args,'max_retries',2)==0,
                     'expected_environment':args.copy_evidence[case['slot']]}
                write_json(folder/'JOB.json',job)
                process=ctx.Process(target=run_one_episode,args=(job,));process.start();processes.append(process)
            wave_deadline=min(deadline,time.monotonic()+args.wave_timeout_seconds)
            while any(p.is_alive() for p in processes) and time.monotonic()<wave_deadline:
                for p in processes:p.join(timeout=.05)
                time.sleep(.05)
        finally:
            # Capture live descendants before terminating workers, so orphaned games retain exact identities.
            cleanup_errors=[]
            try:
                if any(p.is_alive() for p in processes):
                    claims.extend(owned_games(snapshot_processes(),[p.pid for p in processes],allowed))
            except BaseException as exc:cleanup_errors.append(str(exc))
            for p in processes:
                try:
                    if p.is_alive():p.terminate();p.join(timeout=3)
                    if p.is_alive():p.kill();p.join(timeout=3)
                    if p.is_alive():raise CleanupError('Worker would not stop')
                except BaseException as exc:cleanup_errors.append(str(exc))
            for folder in folders:
                owned=folder/'OWNED_PROCESSES.json'
                if owned.exists():claims.extend(json.loads(owned.read_text(encoding='utf-8')))
            unique={(r['pid'],r['created'],r['path']):r for r in claims}
            try:stop_owned(list(unique.values()),allowed)
            except BaseException as exc:cleanup_errors.append(str(exc))
            try:wait_ports(ports,timeout=args.cleanup_timeout_seconds)
            except BaseException as exc:cleanup_errors.append(str(exc))
            if cleanup_errors:raise CleanupError('; '.join(cleanup_errors))
        results=[]
        for case,folder in zip(group,folders):
            result=folder/'ATTEMPT_RESULT.json'
            value=json.loads(result.read_text(encoding='utf-8')) if result.exists() else {'case_id':case['case_id'],'ok':False,'error':'worker_exit_or_deadline_without_result'}
            if value.get('fatal'):
                if any(e['type']=='FrozenViolation' for e in value.get('errors',[])):raise FrozenViolation('Worker frozen assertion failed')
                raise CleanupError('Worker could not verify cleanup ownership')
            if value.get('ok'):validate_frozen(value['frozen'],model_sha)
            results.append(value)
        return results


def build_matrix(key,model,expected,out,plan,accepted,attempts,pending,mode):
    rows=[accepted[c['case_id']]['row'] for c in plan['case_plan'] if c['case_id'] in accepted]
    frozen_file(model,expected)
    frozen_ok=bool(rows) and all(validate_frozen(v['frozen'],expected) for v in accepted.values())
    complete=not pending and len(rows)==len(plan['case_plan'])
    matrix={'schema':SCHEMA,'model_key':key,'model':str(model),'model_sha256':expected,
            'protocol':plan['protocol'],'protocol_hash':plan['protocol_hash'],
            'case_plan':plan['case_plan'],'case_plan_hash':plan['case_plan_hash'],
            'frozen_verified':frozen_ok,'evaluation_complete':complete,'training':False,'used_for_training':False,
            'environment':{'game_copies':plan['copies']},'attempts':attempts,'missing_cases':pending,
            'modes':{mode:{'mode':mode,'deterministic':mode=='deterministic','episodes':rows,'summary':summarize(rows)}}}
    write_json(out/'evaluation_matrix.json',matrix)
    return matrix


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models',default='r1,r4ep004,r4final')
    parser.add_argument('--episodes',type=int,default=10)
    parser.add_argument('--parallel',type=int,default=2)
    parser.add_argument('--seed-base',type=int,default=20000)
    parser.add_argument('--episode-seconds',type=float,default=300.)
    parser.add_argument('--mode',choices=('deterministic','stochastic'),default='deterministic')
    parser.add_argument('--stamp',default=time.strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--out',type=Path)
    parser.add_argument('--max-retries',type=int,default=2)
    parser.add_argument('--wave-timeout-seconds',type=float,default=1200.)
    parser.add_argument('--cleanup-timeout-seconds',type=float,default=30.)
    parser.add_argument('--max-runtime-minutes',type=float,default=180.)
    parser.add_argument('--plan-only',action='store_true',help='Read-only preflight and write plan; no game/config/process changes')
    parser.add_argument('--visible',action='store_true',help='Normal game rendering for a visible demonstration (recorded separately in protocol)')
    args=parser.parse_args(argv)
    keys=args.models.split(',')
    if len(set(keys))!=len(keys) or any(k not in MODELS for k in keys):parser.error('Unknown/duplicate model')
    if args.max_retries<0 or args.max_runtime_minutes<=0 or args.wave_timeout_seconds<=0 or args.cleanup_timeout_seconds<0:parser.error('Invalid retry/time bound')
    if not 1<=BASE_PORT<=65535-args.parallel+1:parser.error('Invalid bridge port range')
    if not args.stamp or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in args.stamp):parser.error('Invalid stamp')
    models={k:MODELS[k] for k in keys}
    plan=make_plan(ROOT,models,args.episodes,args.parallel,args.seed_base,args.episode_seconds,args.mode,render_off=not args.visible)
    args.copy_evidence={copy['slot']:copy for copy in plan['copies']}
    out=args.out or ROOT/'reports/rl'/f'eval-v22-{args.stamp}'
    out=out.resolve()
    if out.exists():raise EvaluationError('Output already exists; refusing to mix runs')
    out.mkdir(parents=True)
    write_json(out/'PLAN.json',plan)
    if args.plan_only:
        write_json(out/'MANIFEST.json',{'status':'PLAN_ONLY','game_started':False,'plan_hash':digest(plan)})
        print(f'PLAN_ONLY: {out}; {len(plan["case_plan"])} paired cases per model; no games/configs changed')
        return 0
    manifest={'schema':SCHEMA,'status':'RUNNING','ok':False,'models':{},'plan_hash':digest(plan)}
    write_json(out/'MANIFEST.json',manifest)
    deadline=time.monotonic()+args.max_runtime_minutes*60
    try:
        ports=[BASE_PORT+i for i in range(args.parallel)]
        for key,model in models.items():
            expected=plan['models'][key]['sha256'];target=out/key;target.mkdir()
            def guard():
                if time.monotonic()>=deadline:raise EvaluationError('Total evaluation wall-time bound reached')
                frozen_file(model,expected)
                from eval_v22_support import preflight
                if preflight(ROOT,args.parallel)!=plan['copies']:raise EvaluationError('Game-copy environment changed since planning')
            def cleanup():
                assert_copies_idle(snapshot_processes(),[copy['game_dir'] for copy in plan['copies']])
                wait_ports(ports,timeout=0)
            def execute(group,attempt):return run_wave(group,attempt,model,expected,target,args,deadline)
            accepted,attempts,pending=retry_cases(plan['case_plan'],execute,cleanup,guard,args.episode_seconds,args.max_retries)
            matrix=build_matrix(key,model,expected,target,plan,accepted,attempts,pending,args.mode)
            manifest['models'][key]={'matrix':str(target/'evaluation_matrix.json'),'complete':matrix['evaluation_complete'],'n':len(accepted)}
            write_json(out/'MANIFEST.json',manifest)
            if pending:raise EvaluationError(f'{key}: incomplete cases after bounded retries')
        manifest.update(ok=True,status='COMPLETED')
    except BaseException as exc:
        manifest.update(ok=False,status='FAILED',error_type=type(exc).__name__,error=str(exc))
        write_json(out/'MANIFEST.json',manifest)
        raise
    write_json(out/'MANIFEST.json',manifest)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
