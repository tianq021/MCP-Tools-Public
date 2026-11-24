"""Serial demonstration evidence, deliberately ineligible for actor/PPO learning."""
import json
import pathlib
from .contract import VERSION,OBS_VERSION,digest,intent_of,validate_action,FAMILIES,combat
from dual_channel.data import sha,write_json

SCHEMA='ra2.structured-serial-transition/1'


def validate(row):
    errors=[]
    if row.get('schema')!=SCHEMA:errors.append('schema mismatch')
    try:
        s=row['snapshot'];a=row['action'];validate_action(s,a)
        if s['action_contract']!=VERSION or s['schema']!=OBS_VERSION:errors.append('contract mismatch')
        if s['snapshot_sha256']!=digest({k:v for k,v in s.items() if k!='snapshot_sha256'}):errors.append('snapshot hash mismatch')
        if row['intent']!=intent_of(s,a):errors.append('action/intention mismatch')
        if row['observation']['frame']!=s['frame'] or row['next_observation']['frame']<=s['frame']:errors.append('observation frame mismatch')
        actors=[m['addr'] for g in s['groups'] for m in g]
        army={e['addr'] for e in s['entities'] if e['friendly'] and combat(e['object'])}
        if len(actors)!=len(set(actors)) or set(actors)!=army:errors.append('group partition mismatch')
        native=row['execution']['native_commands']
        if len(native)>1 or a['family']==0 and native:errors.append('more than one native command/noop sent command')
        if a['family'] and row['execution']['classification']=='ACCEPTED' and len(native)!=1:errors.append('accepted action missing native send')
        for c in native:
            if c.get('action_idx') is not None:errors.append('structured command mislabeled as301')
            if c.get('decision_id')!=row['decision_id'] or c.get('issued_frame')!=s['frame']:errors.append('native provenance mismatch')
            if set(c.get('actor_unit_ids') or [])!=set((row['intent'] or {}).get('object_addresses') or []):errors.append('native actor mismatch')
            if c.get('target_id')!=(row['intent'] or {}).get('target_address'):errors.append('native target mismatch')
            if a['family'] in (5,6) and (c.get('coordinates') or [])[:2] != row['intent']['coordinates']:errors.append('native coordinate mismatch')
            if a['family']==2 and c.get('production_type_ptr')!=row['intent']['production_type_ptr']:errors.append('native production mismatch')
            if a['family']==3 and c.get('placed_building_ptr')!=row['intent']['building_address']:errors.append('native pending mismatch')
    except (KeyError,ValueError,TypeError,IndexError) as exc:errors.append(f'invalid structured row: {exc}')
    if row.get('reward') is not None or any(row.get(k) for k in ('actor_eligible','formal_training_eligible','effect_eligible','ppo_eligible')):
        errors.append('script demonstration incorrectly admitted for training')
    return errors


class SerialWriter:
    def __init__(self,out,metadata):
        self.out=pathlib.Path(out);self.out.mkdir(exist_ok=False,parents=True)
        write_json(self.out/'metadata.json',metadata)
        self.handle=(self.out/'transitions.jsonl').open('x',encoding='utf-8');self.n=0;self.last_next=None;self.seen=set()

    def append(self,row):
        errors=validate(row)
        if row['decision_id'] in self.seen:errors.append('duplicate decision')
        if self.last_next is not None and row['observation']!=self.last_next:errors.append('serial next observation discontinuity')
        if errors:raise ValueError(errors)
        self.handle.write(json.dumps(row,ensure_ascii=False)+'\n');self.handle.flush()
        self.n+=1;self.last_next=row['next_observation'];self.seen.add(row['decision_id'])

    def finish(self,kind,failure=None):
        self.handle.close()
        write_json(self.out/'summary.json',{'schema':'structured-serial-summary/1','decisions':self.n,
            'capture_complete':failure is None and kind in ('win','defeat','timeout'),
            'environment_kind':kind,'failure':failure,'teacher_strength':'UNKNOWN',
            'formal_training_eligible':False,'ppo_eligible':False})
        write_json(self.out/'manifest.json',{'schema':'structured-serial-manifest/1',
            'files':{p.name:{'sha256':sha(p),'size':p.stat().st_size} for p in self.out.iterdir() if p.is_file()}})


def verify(out):
    out=pathlib.Path(out);manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    errors=[];prior=None;seen=set();native=[];families={};classifications={};remaps=0
    for name,m in manifest['files'].items():
        p=out/name
        if not p.is_file() or sha(p)!=m['sha256'] or p.stat().st_size!=m['size']:errors.append(f'hash/size {name}')
    for line in (out/'transitions.jsonl').read_text(encoding='utf-8').splitlines():
        r=json.loads(line);errors+=validate(r)
        if prior is not None and r['observation']!=prior:errors.append('discontinuous observations')
        if r['decision_id'] in seen:errors.append('duplicate decision')
        seen.add(r['decision_id']);prior=r['next_observation'];native+=r['execution']['native_commands']
        f=FAMILIES[r['action']['family']];families[f]=families.get(f,0)+1
        c=r['execution']['classification'];classifications[c]=classifications.get(c,0)+1
        remaps+=bool(r['teacher_proposal']['membership_remapped'])
    journal=out.parent/'command-capture/action_command_records.jsonl'
    if journal.is_file():
        actual=[json.loads(l) for l in journal.read_text(encoding='utf-8').splitlines() if l.strip()]
        if actual!=native:errors.append('native journal does not exactly match transitions')
    else:errors.append('missing native send journal')
    return {'pass':not errors,'errors':errors,'decisions':len(seen),'native_commands':len(native),
        'families':families,'classifications':classifications,'membership_remaps':remaps,
        'formal_training_eligible':False,'outcome':'UNKNOWN unless independently engine verified'}
