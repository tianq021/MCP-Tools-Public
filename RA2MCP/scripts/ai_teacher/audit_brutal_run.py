"""Independent outcome and actual-command audit for raw Brutal teacher runs."""
import argparse,collections,configparser,json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
from dual_channel.data import sha,write_json


def audit(run):
    run=pathlib.Path(run);result=json.loads((run/'RESULT.json').read_text(encoding='utf-8'))
    cfg=configparser.ConfigParser(interpolation=None,strict=False);cfg.read(run/'spawn.ini',encoding='utf-8')
    journal=[json.loads(l) for l in (run/'command-capture/action_command_records.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    flattened=[];errors=[];n=0;prior=None;outcomes=collections.Counter();maximum=0;max_army=0
    with(run/'decisions.jsonl').open(encoding='utf-8') as f:
        for line in f:
            r=json.loads(line);n+=1;plan=r['plan'];before=r['before'];after=r['after']
            if prior is not None and prior!=before:errors.append('noncontinuous raw observations')
            prior=after
            if before['frame']!=plan['observation_frame'] or after['frame']<=before['frame']:errors.append('frame mismatch')
            if r['formal_training_eligible']:errors.append('script data admitted to training')
            actual=r['execution']['native_commands'];flattened+=actual
            planned={f"{r['decision_id']}:command:{i:02d}":c['intent'] for i,c in enumerate(plan['commands'])}
            known={e['addr'] for e in before.get('visible_enemies') or [] if not e.get('cell_shrouded')}
            for c in actual:
                intent=planned.get(c['action_id'])
                if intent is None:errors.append('unplanned native command');continue
                if c['decision_id']!=r['decision_id'] or c['issued_frame']!=before['frame']:errors.append('native provenance mismatch')
                if c['action_idx'] is not None:errors.append('raw command fabricated as301')
                if set(c.get('actor_unit_ids') or [])!=set(intent.get('object_addresses') or []):errors.append('actor mismatch')
                if c.get('target_id')!=intent.get('target_address'):errors.append('target mismatch')
                if intent.get('target_address') and intent['target_address'] not in known:errors.append('hidden target')
                maximum=max(maximum,len(c.get('actor_unit_ids') or []))
            outcomes.update(o['classification'] for o in r['execution']['command_outcomes'])
            max_army=max(max_army,plan['army'])
    if journal!=flattened:errors.append('native journal differs from recorded executions')
    if sha(run/'teacher-source.py')!=result['teacher']['source_sha256']:errors.append('teacher source hash mismatch')
    flags=result.get('engine_outcome_flags') or {};terminal=result.get('terminal') or {};kind=result.get('end_kind')
    win=(result.get('status')=='FINISHED' and kind=='win' and not flags.get('human_defeated') and not flags.get('human_is_loser')
         and bool(flags.get('human_is_winner') or flags.get('enemies_all_defeated')))
    hard=cfg.getint('Settings','AIDifficulty')==0 and cfg.getint('HouseHandicaps','Multi2')==0
    return {'schema':'ra2.brutal-run-audit/1','association_pass':not errors,'errors':errors,'decisions':n,
        'native_commands':len(journal),'native_outcomes':dict(outcomes),'maximum_actor_group':maximum,'maximum_army':max_army,
        'difficulty_config_verified':hard,'difficulty_config':{'AIDifficulty':cfg.getint('Settings','AIDifficulty'),
            'HouseHandicaps.Multi2':cfg.getint('HouseHandicaps','Multi2')},'seed':cfg.getint('Settings','Seed'),
        'spawn_sha256':sha(run/'spawn.ini'),'map_sha256':sha(run/'spawnmap.ini'),
        'teacher_sha256':result['teacher']['source_sha256'],'config_sha256':result['teacher']['config_sha256'],
        'end_kind':kind,'engine_flags':flags,'terminal':terminal,
        'confirmed_brutal_win':win and hard and not errors,'N':1,
        'strength':'SINGLE_CONFIG_WIN' if win and hard and not errors else 'NOT_PROVEN',
        'limits':['Config difficulty verified through active isolated spawn and legacy spawner source,not an exposed runtime difficulty field',
                  'One fixed seed/map is not a general win-rate claim','RPC acceptance is not an effect label']}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();out=pathlib.Path(a.out)
    if out.exists():raise FileExistsError('new audit path required')
    data=audit(a.run);write_json(out,data);print(json.dumps(data,ensure_ascii=True))
