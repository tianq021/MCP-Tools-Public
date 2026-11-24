"""Offline configuration and guards for 2/3-AI observer collection."""
from pathlib import Path
import json,random,time

FAMILIES={'Allied':(0,1,2,3,4),'Soviet':(5,6,7,8),'Yuri':(9,)}
COLORS=(0,1,3,4,5,6,7)
SUCCESS=('AGENT_STOP_SIM_LIMIT','AGENT_STOP_CONFIRMED_WINNER_TAIL')

def make_cases(master_seed,count,maps,*,start=100,ai_count=None):
    rng=random.Random(master_seed);used=set();rows=[]
    for i in range(count):
        seed=rng.randrange(1,2**31)
        while seed in used:seed=rng.randrange(1,2**31)
        used.add(seed);n=ai_count or rng.choice((2,3));families=[rng.choice(tuple(FAMILIES)) for _ in range(n)]
        rows.append({'case_id':f'case-{start+i:04d}','seed':seed,'ai_count':n,'factions':families,
                     'countries':[rng.choice(FAMILIES[f]) for f in families],'colors':rng.sample(COLORS,n),'map':rng.choice(maps)})
    return rows

def validate_case(case):
    n=case['ai_count']
    if n not in (2,3):raise ValueError('Only 2 or 3 AI participants are allowed')
    if not 0<case['seed']<2**31:raise ValueError('Positive signed-32-bit engine seed required')
    if len(case['countries'])!=n or len(case['colors'])!=n:raise ValueError('Roster length mismatch')
    if any(c not in range(10) for c in case['countries']):raise ValueError('Country ID out of range')
    if len(set(case['colors']))!=n or any(c not in COLORS for c in case['colors']):raise ValueError('Invalid/duplicate colors')
    if len(case.get('factions',[]))!=n:raise ValueError('Missing faction provenance')
    if any(country not in FAMILIES[faction] for country,faction in zip(case['countries'],case['factions'])):raise ValueError('Faction/country mismatch')
    return case

def load_case(path):return validate_case(json.loads(Path(path).read_text(encoding='utf-8')))

def build_spawn_ini(**kwargs):
    """Spawner AI selection arrays exclude the human observer node.

    Four native probes showed AI countries [0]+requested[:-1] when the
    observer occupied Multi1. Human observer side/color come from Settings;
    HouseCountries/Colors/Handicaps Multi1 therefore belongs to the first AI.
    Keep this correction scoped to the new small-match collector.
    """
    from observer import config
    import configparser,io
    ini=configparser.ConfigParser();ini.optionxform=str
    ini.read_string(config.build_observer_spawn_ini(**kwargs))
    n=kwargs['ai_count']
    for section,values in (('HouseCountries',kwargs['countries']),('HouseColors',kwargs['colors']),
                           ('HouseHandicaps',[kwargs.get('difficulty',0)]*n)):
        ini.remove_section(section);ini.add_section(section)
        for i,value in enumerate(values[:n]):ini.set(section,f'Multi{i+1}',str(value))
    output=io.StringIO();ini.write(output,space_around_delimiters=False)
    return '; Small observer protocol: AI selection arrays exclude observer\n'+output.getvalue()

def validate_native_roster(houses,countries):
    ordered=sorted(houses,key=lambda h:h['array_index'])
    actual=[h['country_index'] for h in ordered]
    if actual!=list(countries):raise ValueError('Native country assignment differs from recorded random roster: '+str(actual))
    return True

class FrameGate:
    def __init__(self,now=None):self.last=None;self.last_accepted=time.monotonic() if now is None else now;self.skipped=0
    def accept(self,frame,now=None):
        now=time.monotonic() if now is None else now
        if self.last is not None and frame<=self.last:
            self.skipped+=1
            if now-self.last_accepted>30:raise RuntimeError('No strictly newer frame for 30 seconds')
            return False
        self.last=frame;self.last_accepted=now;return True

class WinnerTail:
    def __init__(self):self.candidate=None;self.since=None;self.confirmed_frame=None
    def update(self,frame,living_ids):
        if len(living_ids)!=1 or living_ids[0]!=self.candidate:
            self.candidate=living_ids[0] if len(living_ids)==1 else None
            self.since=frame if self.candidate is not None else None;self.confirmed_frame=None
        if self.candidate is not None and self.confirmed_frame is None and frame-self.since>=188:self.confirmed_frame=frame
        return self.confirmed_frame is not None and frame-self.confirmed_frame>=3750
