"""Public observation snapshots and exact conditional command semantics.

Masks are conservative bounds/ownership/metadata checks. Native placement and
pathfinding remain authoritative; PLACE anchors may be resolved by PlaceQuery.
No alliance inference outside the existing no-alliance skirmish contract.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from dsh_rl import full_spec as S
from ai_teacher.formation_teacher import alive, NON_COMBAT
from dsh_episode.hostility import is_opponent

VERSION = 'structured-ground/1'
OBS_VERSION = 'structured-observation/1'
# PLACE anchor semantics are versioned separately: the JSON factor layout and
# observation schema did not change, so both modes share VERSION. Every new
# snapshot records its mode; snapshots without the field are v1 absolute cells.
PLACE_ANCHOR_ABSOLUTE = 'absolute-cell/1'
PLACE_ANCHOR_RELATIVE = 'base-relative-offset/1'
PLACE_ANCHOR = PLACE_ANCHOR_RELATIVE
PLACE_OFFSET_ORIGIN = 128
PLACE_OFFSET_RADIUS = 8
FAMILIES = ('NOOP','DEPLOY','PRODUCE','PLACE','STOP','MOVE','ATTACK')
ROLES = ('front','fire','support','scout','retreat','reserved5','reserved6','reserved7')
N_ENTITY, N_GROUP, N_TYPE, N_PENDING, N_CELL = 256,8,96,4,256
ENTITY_DIM, GLOBAL_DIM, PRODUCTION_DIM = 12,24,8


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


class Vocabulary:
    def __init__(self, names):
        self.names = tuple(sorted(set(str(n) for n in names)))
        self.ids = {name:i+1 for i,name in enumerate(self.names)}
        self.sha256 = digest({'version':'type-vocabulary/1','names':self.names,'unknown':0})

    @classmethod
    def from_catalog(cls,catalog):
        return cls(f"{t['type']}:{t['name']}" for t in catalog)

    def encode(self,obj):
        kind={'ABSTRACT_TYPE_BUILDING':'ABSTRACT_TYPE_BUILDINGTYPE',
              'ABSTRACT_TYPE_UNIT':'ABSTRACT_TYPE_UNITTYPE','ABSTRACT_TYPE_INFANTRY':'ABSTRACT_TYPE_INFANTRYTYPE'}.get(obj.get('abstract'),obj.get('abstract'))
        return self.ids.get(f"{kind}:{obj.get('type_name')}",0)

    def manifest(self):
        return {'schema':'type-vocabulary/1','names':list(self.names),'unknown':0,'sha256':self.sha256}


def visible_enemy(obj):
    return (alive(obj) and is_opponent(obj) and obj.get('is_visible',True)
            and obj.get('cell_shrouded') is False and obj.get('cloak_state',0) not in (1,2)
            and obj.get('abstract')!='ABSTRACT_TYPE_AIRCRAFT')


def combat(obj):
    return (alive(obj) and obj.get('abstract') in ('ABSTRACT_TYPE_UNIT','ABSTRACT_TYPE_INFANTRY')
            and not any(n in obj.get('type_name','') for n in NON_COMBAT))


def place_anchor_mode(snapshot):
    return snapshot.get('place_anchor',PLACE_ANCHOR_ABSOLUTE)


def base_cell_of(snapshot):
    """Construction-yard cell, else any friendly placed building cell; None before a base."""
    buildings=[e['object'] for e in snapshot['entities']
               if e['friendly'] and e['object'].get('on_map') and e['object'].get('in_limbo') is not True
               and e['object'].get('abstract')=='ABSTRACT_TYPE_BUILDING']
    cy=next((o for o in buildings if 'Construction Yard' in o.get('type_name','')),None)
    chosen=cy or (buildings[0] if buildings else None)
    return None if chosen is None else [int(chosen['cell'][0]),int(chosen['cell'][1])]


class SnapshotBuilder:
    """Episode-local identity epochs; explicit overflow errors, never silent truncation."""
    def __init__(self,vocabulary,episode):
        self.vocab,self.episode=vocabulary,str(episode)
        self.tracks={};self.serial=0;self.last_frame=None

    def build(self,observation,spec,previous=None):
        row=copy.deepcopy({k:observation.get(k) for k in ('frame','t','money','map','own_objects','visible_enemies','factories')})
        frame=row['frame']
        if not isinstance(frame,int) or self.last_frame is not None and frame<=self.last_frame:
            raise ValueError('snapshot frames must strictly increase within an episode')
        width,height=(int((row['map'] or {}).get(k,0)) for k in ('width','height'))
        if not(0<width<=N_CELL and 0<height<=N_CELL):raise ValueError('map outside coordinate capacity')
        own=[o for o in row['own_objects'] or [] if o.get('health',0)>0]
        enemies=[o for o in row['visible_enemies'] or [] if visible_enemy(o)]
        objects=sorted([(True,o) for o in own]+[(False,o) for o in enemies],key=lambda p:(not p[0],p[1]['addr']))
        addresses=[o['addr'] for _,o in objects]
        if len(objects)>N_ENTITY or len(addresses)!=len(set(addresses)):raise ValueError('entity capacity/duplicate address')
        if len(spec.types)>N_TYPE or len(S.pending(row))>N_PENDING:raise ValueError('catalog/pending overflow')
        completed={f.get('object_ptr') for f in row['factories'] or [] if f.get('completed')}
        all_pending=[o for o in own if S.is_building(o) and o['addr'] in completed and (o.get('in_limbo') or not o.get('on_map'))]
        if len(all_pending)>N_PENDING:raise ValueError('pending overflow')
        entities=[];current={}
        for friendly,o in objects:
            key=(o.get('type_name'),o.get('owner'),o.get('owner_ptr'),friendly)
            old=self.tracks.get(o['addr'])
            if old is None or old['key']!=key or not old['present']:
                self.serial+=1;old={'track_id':f'{self.episode}:{self.serial}','key':key}
            current[o['addr']]={**old,'present':True}
            cell=o.get('cell') or [0,0]
            if len(cell)<2 or not all(math.isfinite(float(c)) for c in cell[:2]):raise ValueError('invalid entity position')
            maximum=float(spec.all_types.get(o.get('type_ptr'),{}).get('strength') or max(1,o['health']))
            features=[float(friendly),float(not friendly),float(S.is_building(o)),
                float(o.get('abstract')=='ABSTRACT_TYPE_UNIT'),float(o.get('abstract')=='ABSTRACT_TYPE_INFANTRY'),
                min(2,o['health']/max(1,maximum)),cell[0]/256,cell[1]/256,
                float(bool(o.get('on_map'))),float(bool(o.get('in_limbo'))),float(bool(o.get('deployed'))),float(combat(o))]
            entities.append({'addr':o['addr'],'track_id':old['track_id'],'type_id':self.vocab.encode(o),
                'friendly':friendly,'targetable':not friendly,'object':o,'features':features})
        for address,old in self.tracks.items():
            if address not in current:current[address]={**old,'present':False}
        self.tracks=current;self.last_frame=frame
        groups=[[] for _ in ROLES]
        army=[e for e in entities if e['friendly'] and combat(e['object'])]
        scout=next((e['addr'] for e in army if e['object'].get('type_name')=='GI'),None)
        for e in army:
            o=e['object'];name=o.get('type_name','')
            near=any(math.dist(o['cell'],enemy['cell'])<12 for enemy in enemies)
            role=(4 if e['features'][5]<.25 and near else 3 if e['addr']==scout else
                  1 if 'Prism Tank' in name else 2 if o.get('abstract')=='ABSTRACT_TYPE_INFANTRY' or 'IFV' in name else 0)
            groups[role].append({'addr':e['addr'],'track_id':e['track_id']})
        production=[]
        for i,t in enumerate(spec.types):
            production.append({'slot':i,'ptr':t['pointer_self'],'name':t['name'],'legal':bool(spec.can_produce(i,row)),
                'features':[t['cost']/2000,float(t['type']==S.KINDS[0]),float(t['type']==S.KINDS[1]),
                    float(t['type']==S.KINDS[2]),t.get('strength',0)/1000,t.get('power_bonus',0)/1000,
                    t.get('power_drain',0)/1000,max(0,t.get('tech_level',0))/10]})
        mcv=next((e for e in entities if e['friendly'] and alive(e['object']) and S.is_mcv(e['object'])),None)
        pending=S.pending(row)
        family=[True,bool(mcv),any(t['legal'] for t in production),bool(pending and S.base_cell(row)),
                any(groups),any(groups),bool(any(groups) and enemies)]
        g=[float(row.get('money') or 0)/20000,float(row.get('t') or 0)/600,width/256,height/256,len(own)/256,len(enemies)/256,0.,0.]
        # Power and all other scalar inputs come from our objects, never enemy houses.
        for o in own:
            t=spec.all_types.get(o.get('type_ptr'),{})
            if alive(o):g[6]+=float(t.get('power_bonus') or 0)/1000;g[7]+=float(t.get('power_drain') or 0)/1000
        prev=[0.]*16
        if previous:
            a=previous['action'];intent=previous.get('intent') or {};f=int(a['family']);prev[f]=1.
            point=intent.get('coordinates') or []
            prev[7:13]=[a.get('group',0)/8,point[0]/65536 if point else 0,point[1]/65536 if point else 0,
                         a.get('production',0)/96,float(intent.get('production_type_ptr') is not None),
                         len(intent.get('object_addresses') or [])/256]
            status=previous.get('outcome','UNKNOWN');prev[13:16]=[float(status=='ACCEPTED'),float(status.endswith('REJECTED')),float(status=='UNKNOWN')]
        snapshot={'schema':OBS_VERSION,'action_contract':VERSION,'episode':self.episode,'frame':frame,
            'vocabulary_sha256':self.vocab.sha256,'map_width':width,'map_height':height,
            'entities':entities,'groups':groups,'roles':list(ROLES),'production':production,'pending':pending,
            'deploy_actor':None if mcv is None else {'addr':mcv['addr'],'track_id':mcv['track_id']},
            'family_mask':family,'global_features':g+prev,'identity':'OBSERVATION_EPOCH_HEURISTIC',
            'place_anchor':PLACE_ANCHOR,
            'legality':'CONSERVATIVE_NATIVE_FINAL','group_builder':'fixed-role/1; permanent first GI scout; no learned membership'}
        snapshot['snapshot_sha256']=digest(snapshot)
        return snapshot


def mask(snapshot,factor,action):
    if factor=='family':return list(snapshot['family_mask'])
    if factor=='group':return [bool(g) for g in snapshot['groups']]
    if factor=='target':return [bool(e['targetable']) for e in snapshot['entities']]+[False]*(257-len(snapshot['entities']))
    if factor=='production':return [p['legal'] for p in snapshot['production']]+[False]*(96-len(snapshot['production']))
    if factor=='pending':return [i<len(snapshot['pending']) for i in range(4)]
    if factor in ('x','y'):
        if (action or {}).get('family')==3 and place_anchor_mode(snapshot)==PLACE_ANCHOR_RELATIVE:
            return [abs(i-PLACE_OFFSET_ORIGIN)<=PLACE_OFFSET_RADIUS for i in range(256)]
        size=snapshot['map_width'] if factor=='x' else snapshot['map_height']
        return [i<size for i in range(256)]
    raise ValueError(f'unknown factor {factor}')


def factors(family):
    return {0:(),1:(),2:('production',),3:('pending','x','y'),4:('group',),5:('group','x','y'),6:('group','target')}[family]


def validate_action(snapshot,action):
    if type(action.get('family')) is not int or not 0<=action['family']<7:raise ValueError('family out of range')
    if set(action)!={'family',*factors(action['family'])}:raise ValueError('inactive or missing factors')
    for name in ('family',*factors(action['family'])):
        legal=mask(snapshot,name,action);value=action[name]
        if type(value) is not int or not 0<=value<len(legal) or not legal[value]:raise ValueError(f'illegal {name}')


def intent_of(snapshot,action):
    validate_action(snapshot,action);family=FAMILIES[action['family']]
    if family=='NOOP':return None
    intent={'action':family,'object_addresses':[]}
    if family=='DEPLOY':intent['object_addresses']=[snapshot['deploy_actor']['addr']]
    if family=='PRODUCE':
        p=snapshot['production'][action['production']]
        intent.update(production_type_ptr=p['ptr'],production_type=p['name'],production_action='BEGIN')
    if family=='PLACE':
        p=snapshot['pending'][action['pending']]
        if place_anchor_mode(snapshot)==PLACE_ANCHOR_RELATIVE:
            base=base_cell_of(snapshot)
            if base is None:raise ValueError('relative PLACE anchor requires a placed base building')
            anchor=[base[0]+action['x']-PLACE_OFFSET_ORIGIN,base[1]+action['y']-PLACE_OFFSET_ORIGIN]
            anchor=[min(max(anchor[0],0),snapshot['map_width']-1),min(max(anchor[1],0),snapshot['map_height']-1)]
        else:anchor=[action['x'],action['y']]
        intent.update(building_address=p['addr'],production_type_ptr=p['type_ptr'],production_type=p['type_name'],anchor=anchor)
    if family in ('MOVE','ATTACK','STOP'):intent['object_addresses']=[m['addr'] for m in snapshot['groups'][action['group']]]
    if family=='MOVE':intent['coordinates']=[action['x']*256+128,action['y']*256+128]
    if family=='ATTACK':
        e=snapshot['entities'][action['target']];o=e['object']
        intent.update(target_address=e['addr'],target_type=o['type_name'],coordinates=[int(c*256+128) for c in o['cell']])
    return intent


def revalidate(snapshot,action,fresh):
    """Reject stale actor/target/building/type bindings; never silently retarget."""
    validate_action(snapshot,action)
    validate_action(fresh,action)
    if snapshot['episode']!=fresh['episode'] or snapshot['vocabulary_sha256']!=fresh['vocabulary_sha256']:
        raise ValueError('episode/vocabulary mismatch')
    f=action['family']
    if f in (4,5,6) and snapshot['groups'][action['group']]!=fresh['groups'][action['group']]:raise ValueError('stale group membership')
    if f==6:
        i=action['target']
        if snapshot['entities'][i]['track_id']!=fresh['entities'][i]['track_id']:raise ValueError('stale target')
    if f==1 and snapshot['deploy_actor']!=fresh['deploy_actor']:raise ValueError('stale deploy actor')
    if f==2 and snapshot['production'][action['production']]!=fresh['production'][action['production']]:raise ValueError('stale production binding')
    if f==3:
        p=action['pending']
        old=snapshot['pending'][p];new=fresh['pending'][p]
        tracks=lambda s,a:next(e['track_id'] for e in s['entities'] if e['addr']==a)
        if old['addr']!=new['addr'] or tracks(snapshot,old['addr'])!=tracks(fresh,new['addr']):raise ValueError('stale pending building')
    return intent_of(fresh,action)
