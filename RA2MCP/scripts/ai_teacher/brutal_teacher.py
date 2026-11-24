"""Raw, bounded observable-state RTS script aimed at full Hard-AI games.

Candidate until an actual engine win. Public spawn and past visible positions
only. No command to hidden targets, economy cheating, generated code or models.
"""
import dataclasses,hashlib,json,math,pathlib
from collections import Counter
from ai_teacher.formation_teacher import alive,NON_COMBAT,semantic
from ai_teacher.strategic_teacher import StrategicTeacher
from dsh_rl import full_spec as S
from dsh_episode.hostility import is_opponent

VERSION='brutal_teacher:1.3.0'

@dataclasses.dataclass(frozen=True)
class Config:
    miners:int=5
    infantry_cap:int=12
    army_cap:int=96
    launch_seconds:float=150
    launch_vehicles:int=15
    launch_prisms:int=5
    defense_radius:float=32
    refresh_seconds:float=6


def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
def centroid(xs):return [sum(o['cell'][i] for o in xs)/max(1,len(xs)) for i in (0,1)]
def match(name,hints):
    return any(name.lower()==h.lower() if len(h)<=3 else h.lower() in name.lower() for h in hints)


class BrutalTeacher:
    def __init__(self,config=None):
        self.config=config or Config();self.orders={};self.memory={};self.phase='BUILDUP';self.tick=0
        self.search_index=0;self.search_time=-100

    def metadata(self):
        return {'teacher_id':VERSION,'source_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
            'config':dataclasses.asdict(self.config),'config_sha256':digest(dataclasses.asdict(self.config)),
            'teacher_strength':'UNKNOWN','student_model_loaded':False,'hidden_info_used':False}

    def plan(self,observation,spec,starts,map_soa):
        row=StrategicTeacher.visible(observation);self.tick+=1;t=float(row.get('t') or 0)
        own=[o for o in row.get('own_objects') or [] if alive(o)]
        army=[o for o in own if o.get('abstract') in ('ABSTRACT_TYPE_UNIT','ABSTRACT_TYPE_INFANTRY')
              and not match(o.get('type_name',''),NON_COMBAT)]
        enemies=[e for e in row.get('visible_enemies') or [] if alive(e) and is_opponent(e)
            and e.get('is_visible',True) and e.get('cell_shrouded') is False and e.get('cloak_state',0) not in (1,2)]
        buildings=[o for o in own if S.is_building(o)];counts=Counter(o['type_name'] for o in own)
        soviet=any(match(k['name'],('Rhino Heavy Tank',)) for k in spec.types)
        count=lambda hints:sum(v for n,v in counts.items() if match(n,hints))
        base=S.base_cell(row);commands=[];budget=float(row.get('money') or 0)
        def emit(role,skill,intent):
            signature=digest(semantic(intent));previous=self.orders.get(role)
            age=t-previous['t'] if previous else 10000
            refresh=1.2 if skill.endswith('kite') else self.config.refresh_seconds
            if age<.85 or previous and signature==previous['signature'] and age<refresh:return
            self.orders[role]={'t':t,'signature':signature}
            commands.append({'role':role,'skill_id':skill,'intent':intent,'mapping_301':{'classification':'NOT_ENCODED','action_idx':None}})
        def produce(hints,role):
            nonlocal budget
            for i,kind in enumerate(spec.types):
                if match(kind['name'],hints) and spec.can_produce(i,row) and kind['cost']<=budget:
                    emit(role,'economy.'+role,{'action':'PRODUCE','object_addresses':[],
                        'production_type_ptr':kind['pointer_self'],'production_type':kind['name'],'production_action':'BEGIN'})
                    budget-=kind['cost'];return True
            return False
        if base is None:
            mcv=next((o for o in own if S.is_mcv(o)),None)
            if mcv:emit('deployment','economy.deploy',{'action':'DEPLOY','object_addresses':[mcv['addr']]})
            return self.result(row,army,commands)
        goal=S.enemy_start(row,starts) or base
        dx,dy=goal[0]-base[0],goal[1]-base[1];norm=max(1,math.hypot(dx,dy));direction=[dx/norm,dy/norm]
        def offset(cell,d):return [cell[i]+direction[i]*d for i in (0,1)]
        def move(members,cell):
            point=[max(0,min(int(getattr(map_soa,'map_width' if i==0 else 'map_height'))-1,int(cell[i]))) for i in (0,1)]
            return {'action':'MOVE','object_addresses':sorted(o['addr'] for o in members),'coordinates':[x*256+128 for x in point]}
        threats=[e for e in enemies if math.dist(e['cell'],base)<self.config.defense_radius]
        ground=[e for e in enemies if e.get('abstract')!='ABSTRACT_TYPE_AIRCRAFT']
        aircraft=[e for e in enemies if e.get('abstract')=='ABSTRACT_TYPE_AIRCRAFT' or match(e['type_name'],('Kirov','Harrier','Black Eagle','Rocketeer','Floating Disc'))]
        for e in enemies:
            if S.is_building(e):self.memory[e['addr']]={'cell':e['cell'],'seen':t,'type_name':e['type_name']}
        self.memory={a:v for a,v in self.memory.items() if t-v['seen']<180}
        # Power from our house, never opponent resources. No fixed three-plant ceiling.
        house=next((h for h in row.get('houses') or [] if h.get('current_player') and h.get('is_human')), {})
        output=float(house.get('power_output') or 0);drain=float(house.get('power_drain') or 0)
        pending=S.pending(row)
        if pending:
            b=pending[0];defense=match(b['type_name'],('Pillbox','Prism Tower','Prism Cannon','Patriot','Grand Cannon'))
            ring=14 if defense else 6+((self.tick//8)%3)*3
            side=S.DIRECTIONS[(self.tick//2)%8]
            anchor=offset(base,ring) if defense else [base[i]+side[i]*ring for i in (0,1)]
            emit('placement','economy.place',{'action':'PLACE','object_addresses':[],
                'building_address':b['addr'],'production_type_ptr':b['type_ptr'],'production_type':b['type_name'],
                'anchor':[int(x) for x in anchor]})
        else:
            chain=[(('Power Plant','Tesla Reactor','Bio Reactor'),1),(('Ore Refinery',),1),
                (('Barracks',),1),(('War Factory',),1),(('Ore Refinery',),2),
                (('Airforce Command','Air Force Command','Radar','Psychic Radar'),1),(('Battle Lab',),1)]
            selected=False
            if output<drain+75: selected=produce(('Power Plant','Tesla Reactor','Bio Reactor'),'building')
            if not selected:
                for hints,n in chain:
                    if count(hints)<n:
                        selected=produce(hints,'building');break
                else:
                    cannon=(('Tesla Coil',) if soviet else ('Grand Cannon',) if any(match(k['name'],('Grand Cannon',)) for k in spec.types) else ('Prism Tower','Prism Cannon'))
                    aa=('Flak Cannon',) if soviet else ('Patriot Missile','Patriot')
                    defense_chain=[(aa,2),(cannon,3),(('Ore Refinery',),3),(('War Factory',),2),(aa,4)]
                    if threats and t<180:defense_chain=[(('Sentry Gun',) if soviet else ('Pill Box','Pillbox'),2)]+defense_chain
                    for hints,n in defense_chain:
                        if count(hints)<n:
                            produce(hints,'building');break
        vehicles=[o for o in army if o.get('abstract')=='ABSTRACT_TYPE_UNIT']
        prisms=[o for o in vehicles if match(o['type_name'],('Prism Tank','V3 Launcher'))]
        ifvs=[o for o in vehicles if match(o['type_name'],('IFV','Flak Track'))]
        if count(('War Factory',)):
            if count(('Miner','Harvester'))<self.config.miners and (not threats or len(vehicles)>=6 or count(('Miner','Harvester'))<2):
                produce(('Chrono Miner','War Miner'),'vehicle')
            elif len(army)<self.config.army_cap:
                choices=[]
                if len(ifvs)<(7 if aircraft else 3):choices.append(('IFV','Flak Track'))
                if len(prisms)<max(4 if soviet else 6,len(vehicles)//3):choices.append(('V3 Launcher',) if soviet else ('Prism Tank',))
                if soviet:
                    if count(('Tesla Tank',))<3:choices.append(('Tesla Tank',))
                    if count(('Apocalypse Tank',))<max(3,len(vehicles)//4):choices.append(('Apocalypse Tank',))
                    choices.append(('Rhino Heavy Tank',))
                else:choices+=[('Mirage Tank',),('Grizzly Battle Tank',)]
                for hints in choices:
                    if produce(hints,'vehicle'):break
        if sum(o.get('abstract')=='ABSTRACT_TYPE_INFANTRY' for o in army)<self.config.infantry_cap:
            # Exact GI avoids alternating into undeployed Guardian GI during cooldowns.
            choices=[t['name'] for t in spec.types if t['name'] in ('GI','Conscript','Initiate')]
            if choices:produce(tuple(choices),'infantry')
        # Hold initial forces until a supported army exists; never rush eight starters at55s.
        if self.phase!='ASSAULT' and t>=self.config.launch_seconds and len(vehicles)>=self.config.launch_vehicles and len(prisms)>=(3 if soviet else self.config.launch_prisms):
            self.phase='ASSAULT'
        if self.phase=='ASSAULT' and len(vehicles)<6:self.phase='REGROUP'
        frontline=[o for o in vehicles if o not in prisms and o not in ifvs]
        support=[o for o in army if o.get('abstract')=='ABSTRACT_TYPE_INFANTRY']
        groups={'front':frontline,'fire':prisms,'antiair':ifvs,'support':support}
        self.phase='ASSAULT' if self.phase=='ASSAULT' else 'BUILDUP' if self.phase=='BUILDUP' else 'REGROUP'
        # Retreat valuable damaged units; visible immediate danger is required.
        retreat=[]
        visible_buildings=[e for e in ground if S.is_building(e)]
        strategic_goal=(min(visible_buildings,key=lambda e:math.dist(e['cell'],base))['cell'] if visible_buildings else
                        next(reversed(self.memory.values()))['cell'] if self.memory else goal)
        # Per-prism control avoids a centroid mixing newborns at base with front-line units.
        fire=groups.pop('fire')
        for unit in fire:
            position=unit['cell'];role=f"fire-{unit['addr']}"
            pool=[e for e in ground if math.dist(e['cell'],position)<(21 if soviet else 12.5)
                  and (self.phase=='ASSAULT' or math.dist(e['cell'],base)<self.config.defense_radius)]
            nearby_mobile=[e for e in ground if not S.is_building(e) and math.dist(e['cell'],position)<7]
            if nearby_mobile:
                enemy=min(nearby_mobile,key=lambda e:math.dist(e['cell'],position));length=max(1,math.dist(position,enemy['cell']))
                emit(role,'combat.kite',move([unit],[position[i]+(position[i]-enemy['cell'][i])*4/length for i in (0,1)]))
            elif pool:
                target=min(pool,key=lambda e:(0 if match(e['type_name'],('Prism Tank','Prism Cannon','Grand Cannon','V3')) else 1,
                    0 if not S.is_building(e) else 1,math.dist(e['cell'],position),e['addr']))
                emit(role,'combat.siege',{'action':'ATTACK','object_addresses':[unit['addr']],
                    'target_address':target['addr'],'target_type':target['type_name'],'coordinates':[int(c*256+128) for c in target['cell']]})
            else:
                dest=offset(strategic_goal,-7) if self.phase=='ASSAULT' and not threats else offset(base,9)
                if math.dist(position,dest)>4:emit(role,'combat.siege_advance',move([unit],dest))
        for role,members in groups.items():
            for o in list(members):
                maximum=spec.all_types.get(o.get('type_ptr'),{}).get('strength') or max(1,o['health'])
                if o['health']/maximum<.22 and any(math.dist(e['cell'],o['cell'])<10 for e in ground):
                    members.remove(o);retreat.append(o)
        if retreat:emit('retreat','combat.retreat',move(retreat,offset(base,-3)))
        visible_buildings=[e for e in ground if S.is_building(e)]
        strategic_goal=(min(visible_buildings,key=lambda e:math.dist(e['cell'],base))['cell'] if visible_buildings else
                        next(reversed(self.memory.values()))['cell'] if self.memory else goal)
        center=centroid(vehicles or army or [dict(cell=base)])
        if self.phase=='ASSAULT' and not enemies and math.dist(center,strategic_goal)<9:
            if t-self.search_time>12:self.search_index+=1;self.search_time=t
            offsets=((0,0),(18,0),(-18,0),(0,18),(0,-18),(25,20),(-25,-20))
            search=offsets[self.search_index%len(offsets)];strategic_goal=[goal[i]+search[i] for i in (0,1)]
        for role,members in groups.items():
            if not members:continue
            position=centroid(members)
            target_pool=(aircraft if role=='antiair' and aircraft else ground)
            if self.phase!='ASSAULT':target_pool=[e for e in target_pool if math.dist(e['cell'],base)<self.config.defense_radius]
            nearby=[e for e in target_pool if math.dist(e['cell'],position)<(18 if role=='fire' else 16)]
            if threats:
                nearby=[e for e in target_pool if e in threats and math.dist(e['cell'],position)<35] or nearby
            if nearby:
                def rank(e):
                    siege=match(e['type_name'],('V3','Prism Tank','Dreadnought','Master Mind','Yuri Clone','Magnetron'))
                    immediate=math.dist(e['cell'],position)<12 and not S.is_building(e)
                    return (0 if role=='antiair' and e in aircraft else 1,0 if immediate else 1,0 if siege else 1,
                        1 if S.is_building(e) and any(not S.is_building(x) for x in nearby) else 0,math.dist(e['cell'],position),e['addr'])
                target=min(nearby,key=rank)
                danger=min((math.dist(e['cell'],position) for e in ground if not S.is_building(e)),default=999)
                if role=='fire' and danger<7:
                    emit(role,'combat.kite',move(members,[position[i]-direction[i]*5 for i in (0,1)]))
                else:
                    emit(role,'combat.focus',{'action':'ATTACK','object_addresses':sorted(o['addr'] for o in members),
                        'target_address':target['addr'],'target_type':target['type_name'],
                        'coordinates':[int(c*256+128) for c in target['cell']]})
            else:
                dest=(strategic_goal if self.phase=='ASSAULT' and not threats else offset(base,14 if role=='front' else 10))
                if self.phase=='ASSAULT' and role=='fire':dest=offset(dest,-5)
                if any(math.dist(o['cell'],dest)>4 for o in members):emit(role,'combat.advance' if self.phase=='ASSAULT' else 'combat.guard',move(members,dest))
        return self.result(row,army,commands)

    def result(self,row,army,commands):
        return {'teacher_id':VERSION,'observation_frame':row['frame'],'phase':self.phase,'commands':commands,
            'army':len(army),'combat_addresses':[o['addr'] for o in army],
            'scripted_strategy':True,'hidden_info_used':False}
