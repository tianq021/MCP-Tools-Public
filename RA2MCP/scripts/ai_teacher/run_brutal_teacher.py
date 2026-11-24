"""Script-only full Hard-AI outcome test; each invocation consumes one attempt."""
import argparse,asyncio,collections,configparser,json,os,pathlib,shutil,sys,time,traceback
ROOT=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from ai_teacher.brutal_teacher import BrutalTeacher,VERSION
from dual_channel import run_teacher as common
from dual_channel.collect_formation import execute_plan
from dual_channel.data import sha,write_json,sanitized,resolve_terminal
LEDGER=ROOT/'coordination/approvals/BRUTAL_TEACHER_RUN_AUTHORIZATION.json'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--authorization',required=True)
    p.add_argument('--seconds',type=float,default=1200);p.add_argument('--execute',action='store_true')
    a=p.parse_args();out=pathlib.Path(a.out).resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=True)
    game=ROOT/'test-game-brutal';common.LEDGER=LEDGER
    pre=common.preflight(out,game,14621,a.seconds,a.authorization)
    # This new full-outcome controller has its own1200s cap; old collectors keep300s.
    pre['checks']['bounded_seconds']=0<a.seconds<=1200
    pre['checks']['new_trace']=not(out/'decisions.jsonl').exists()
    cfg=configparser.ConfigParser(interpolation=None,strict=False);cfg.read(game/'spawn.ini',encoding='utf-8')
    pre['checks']['actual_config_Hard0']=cfg.getint('Settings','AIDifficulty')==0 and cfg.getint('HouseHandicaps','Multi2')==0
    pre.update(pass_=all(pre['checks'].values()),requested_seconds=a.seconds,difficulty='Hard0/Brutal')
    pre['pass']=pre.pop('pass_');write_json(out/'PREFLIGHT.json',pre)
    if not pre['pass']:print(json.dumps(pre));return 3
    os.environ.update(DSH_V3_CAPTURE='1',DSH_EVENT_CAPTURE='1',DSH_COMMAND_CAPTURE='1',
        DSH_COMMAND_CAPTURE_DIR=str(out/'command-capture'),DSH_MAP_DIAG_DIR=str(out/'map-diag'),
        DSH_LOCK_DIR=str(ROOT/'build'),DSH_RPC_TIMEOUT_S='15')
    from dsh_rl.full_env import RA2FullEnv
    from dsh_rl.env import RA2MicroEnv
    from dsh_episode import lockfile
    RA2MicroEnv.RESET_RETRY_DELAYS=()
    lock=lockfile.acquire(ROOT/'build/dsh-game.lock');lockfile.release(lock)
    teacher=BrutalTeacher()
    class Env(RA2FullEnv):
        def _control_metadata(self):return VERSION,{'control_mode':'brutal_script_teacher','teacher':teacher.metadata(),'student_model_loaded':False}
    env=Env(out/'episodes',max_seconds=a.seconds,decision_seconds=.5,game_dir=game,port=14621,kill_stale=False,
        protection={'enabled':True,'stall_timeout_s':5,'hard_step_timeout_s':30,'screenshot_interval_s':60})
    if not a.execute:env.loop.close();print('PREFLIGHT PASS; no game');return 0
    common.consume(a.authorization,out)
    result={'authorization':a.authorization,'status':'STARTING','teacher':teacher.metadata(),'training_started':False,
        'student_model_loaded':False,'game_launches':0,'difficulty':'Hard0/Brutal','seed':cfg.getint('Settings','Seed')}
    write_json(out/'RESULT.json',result);failure=None;handle=None
    try:
        env.reset();result.update(status='RUNNING',game_launches=1,game_pid=env.game_pid,episode_id=env.writer.episode_id)
        shutil.copyfile(game/'spawn.ini',out/'spawn.ini');shutil.copyfile(ROOT/'scripts/ai_teacher/brutal_teacher.py',out/'teacher-source.py')
        shutil.copyfile(game/'spawnmap.ini',out/'spawnmap.ini')
        # Public slot choices are configuration, never hidden live house positions.
        occupied=[]
        if cfg.has_section('SpawnLocations'):
            all_starts=env.starts
            for index in range(cfg.getint('Settings','PlayerCount')):
                slot=cfg.getint('SpawnLocations',f'Multi{index+1}',fallback=-1)
                if 0<=slot<len(all_starts):occupied.append(all_starts[slot])
        public_starts=occupied or env.starts
        result['public_start_cells']=public_starts
        result['actual_player_count']=cfg.getint('Settings','PlayerCount')
        result['AIPlayers']=cfg.getint('Settings','AIPlayers')
        result['initial_houses_audit_only']=env.current.get('houses');write_json(out/'RESULT.json',result)
        handle=(out/'decisions.jsonl').open('x',encoding='utf-8');n=0;outcomes=collections.Counter();families=collections.Counter();bad=0;last_report=-100
        while env.kind=='running':
            before=sanitized(env.current);plan=teacher.plan(before,env.full,public_starts,env.map_soa)
            did=f'{env.writer.episode_id}:brutal:{n:05d}'
            execution=env._run(execute_plan(env,plan,did),name='brutal_script_commands');after=sanitized(env.current)
            handle.write(json.dumps({'schema':'ra2.brutal-script-decision/1','decision_id':did,'before':before,'plan':plan,
                'execution':execution,'after':after,'formal_training_eligible':False},ensure_ascii=False)+'\n');handle.flush()
            n+=1
            for cmd in plan['commands']:families[cmd['intent']['action']]+=1
            for o in execution['command_outcomes']:
                outcomes[o['classification']]+=1
                if o['classification']=='ACCEPTED':bad=0
                elif o['classification'] not in ('STALE_REJECTED',):bad+=1
            if any(o['classification']=='TRANSPORT_FAILURE' for o in execution['command_outcomes']):raise RuntimeError('transport failure,no resend')
            if bad>=4:raise RuntimeError('four non-stale rejects without accepted command')
            if float(after['t'])-last_report>=20 or env.kind!='running':
                counts=collections.Counter(o['type_name'] for o in after.get('own_objects') or [] if o.get('on_map') and not o.get('in_limbo') and o.get('health',0)>0)
                progress={'decisions':n,'t':after['t'],'phase':plan['phase'],'kind':env.kind,'money':after.get('money'),
                    'army':plan['army'],'own_counts':dict(counts),'visible_enemy_counts':dict(collections.Counter(e['type_name'] for e in after.get('visible_enemies') or [])),
                    'command_outcomes':dict(outcomes),'families':dict(families)}
                write_json(out/'PROGRESS.json',progress);print(json.dumps(progress,ensure_ascii=True),flush=True);last_report=float(after['t'])
                if shutil.disk_usage(ROOT).free<2*1024**3:raise RuntimeError('disk below2GB; preserve trace')
        result.update(status='FINISHED',decisions=n,native_outcomes=dict(outcomes),families=dict(families),
            engine_outcome_flags=getattr(env,'_last_outcome_flags',{}),terminal=resolve_terminal(sanitized(env.current),env.kind))
    except BaseException as exc:
        failure=f'{type(exc).__name__}: {exc}';result.update(status='FAILED',failure=failure,traceback=traceback.format_exc())
    finally:
        if handle:handle.close()
        result.setdefault('engine_outcome_flags',getattr(env,'_last_outcome_flags',{}))
        result.setdefault('terminal',resolve_terminal(sanitized(getattr(env,'current',{}) or {}),getattr(env,'kind','UNKNOWN')))
        common.finish_attempt(env,out,result,failure=failure)
    print(json.dumps({'status':result['status'],'end_kind':result['end_kind'],'engine_outcome_flags':result['engine_outcome_flags'],'failure':failure}),flush=True)
    return 1 if failure else 0

if __name__=='__main__':
    if sys.version_info[:2]!=(3,10):raise SystemExit('Use existing F:/conda/envs/python_mmx_LLM/python.exe')
    sys.path.append(str(ROOT/'.venv-rl/Lib/site-packages'));sys.exit(main())
