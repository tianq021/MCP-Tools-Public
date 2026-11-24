"""Build native teaching scenarios from the user's last saved map, never edit it.

Separate mutable game copy; only immutable MIX archives are hard-linked.
Native triggers spawn troops. The optional observer never sends orders.
"""
from pathlib import Path
import argparse,configparser,hashlib,json,os,shutil,socket,stat,struct,sys,time

ROOT=Path(__file__).resolve().parents[2]
REPORT=ROOT/'reports/wave-defense-map-20261105'
SOURCE=ROOT/'maps/training/wave-defense-20261105/wave-defense-v1.map'
GAME=ROOT/'test-game-wave'
PORT=14624
OWNER='<Player @ B>'

def dump(path,data):
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def ini(path):
    p=configparser.ConfigParser(interpolation=None,strict=False,inline_comment_prefixes=(';',));p.optionxform=str
    p.read(path,encoding='utf-8-sig');return p

def save_ini(p,path):
    with Path(path).open('w',encoding='utf-8') as f:p.write(f,space_around_delimiters=False)

def csf_read(data):
    magic,version,labels,strings,unused,language=struct.unpack('<4s5I',data[:24]);assert magic==b' FSC'
    pos=24;result={};seen=0
    for _ in range(labels):
        marker,n,length=struct.unpack_from('<4sII',data,pos);pos+=12;assert marker==b' LBL'
        label=data[pos:pos+length].decode('ascii');pos+=length;texts=[]
        for _ in range(n):
            marker,length=struct.unpack_from('<4sI',data,pos);pos+=8;assert marker in (b' RTS',b'WRTS')
            text=bytes(v^255 for v in data[pos:pos+2*length]).decode('utf-16le');pos+=2*length;texts.append(text);seen+=1
            if marker==b'WRTS':
                length=struct.unpack_from('<I',data,pos)[0];pos+=4+length
        result[label.upper()]=texts[0] if texts else ''
    assert pos==len(data) and seen==strings
    return result

def append_csf(original,labels):
    old=csf_read(original);header=list(struct.unpack('<4s5I',original[:24]));payload=bytearray(original[24:])
    for label,value in labels.items():
        assert label.upper() not in old
        b=label.encode('ascii');u=value.encode('utf-16le')
        payload+=struct.pack('<4sII',b' LBL',1,len(b))+b
        payload+=struct.pack('<4sI',b' RTS',len(u)//2)+bytes(x^255 for x in u)
    header[2]+=len(labels);header[3]+=len(labels)
    result=struct.pack('<4s5I',*header)+payload
    check=csf_read(result);assert all(check[k.upper()]==v for k,v in labels.items())
    return result

def ensure_copy():
    marker=GAME/'CLASSROOM_COPY.json'
    if marker.exists():return
    GAME.mkdir(exist_ok=False)
    source=ROOT/'test-game-1';files=[]
    allowed={'.mix','.dll','.exe','.ini','.csf','.fnt','.shp','.dat','.lcf','.dsk','.tlb'}
    for p in source.iterdir():
        if not p.is_file() or p.suffix.lower() not in allowed:continue
        if p.name.lower().startswith(('unins','dsh-')):continue
        dest=GAME/p.name
        if p.suffix.lower()=='.mix':os.link(p,dest);mode='immutable_asset_hardlink'
        else:shutil.copy2(p,dest);mode='independent_copy'
        files.append(dict(name=p.name,mode=mode,size=p.stat().st_size))
    exe=(ROOT/'test-game/gamemd-spawn-ra2yrcpp.exe').read_bytes();names=[]
    for guid in ['29e3bb2a-2f36-11d3-a72c-0090272fa661','01AF9993-3492-11d3-8F6F-0060089C05B1']:
        assert exe.count(guid.encode())==1
        new=guid[:-2]+'04';exe=exe.replace(guid.encode(),new.encode());names.append(new)
    (GAME/'gamemd-spawn-ra2yrcpp.exe').write_bytes(exe)
    dump(GAME/'ra2mcp-instance.json',dict(schema_version=1,instance=3,patched=True,patched_sha256=sha(GAME/'gamemd-spawn-ra2yrcpp.exe'),mutex_names=names))
    dump(GAME/'ra2yrcpp.json',dict(port=PORT,allowedHostsRegex=r'127\.0\.0\.1',singleStep=False,maxConnections=4,logFilename='ra2yrcpp.log'))
    d=ini(GAME/'ddraw.ini');d['ddraw'].update(dict(windowed='true',fullscreen='false',posX='100',posY='100',savesettings='0',width='1280',height='800'));save_ini(d,GAME/'ddraw.ini')
    d=ini(GAME/'RA2MD.ini');d['Video'].update(dict(ScreenWidth='1280',ScreenHeight='800'));save_ini(d,GAME/'RA2MD.ini')
    dump(marker,dict(source=str(source),files=files,port=PORT,instance=3))

def roster():
    rules=json.loads((REPORT/'ROSTER_RULES.json').read_text(encoding='utf-8'))
    csf=csf_read((ROOT/'test-game-1/ra2md.csf').read_bytes())
    # All land/air military units, including stolen-tech and campaign combat units.
    order='E2 E1 INIT FLAKT GGI SHK BRUTE DOG ADOG SNIPE VIRUS GHOST TANY BORIS DESO IVAN TERROR CLEG YURI YURIPR PTROOP CCOMAND CIVAN MTNK HTNK LTNK HTK FV YTNK TNKD TTNK SREF MGTK APOC DRON CAOS TELE MIND DTRUCK ROBO BFRT HARV SMIN JUMPJET SHAD SCHP ZEP DISK LUNR ORCA BEAG'.split()
    special={'ROBO':'需要机器人控制中心和电力','BFRT':'空载战斗要塞；载员组合另设课程','FV':'空载多功能步兵车；载员组合另设课程','SCHP':'飞行模式；部署炮击另设课程','GGI':'行进模式；部署反坦克另设课程','E1':'行进模式；部署机枪另设课程','DESO':'行进模式；辐射部署另设课程','ORCA':'有限弹药战机；返航机制待实测','BEAG':'有限弹药战机；返航机制待实测','LUNR':'战役月球步兵','HARV':'武装矿车','SMIN':'武装奴隶矿场车','SREF':'光棱溅射','YURI':'心灵控制','YURIPR':'心灵控制','MIND':'心灵控制','CAOS':'混乱效果','CLEG':'超时空抹除','DRON':'寄生效果'}
    byid={r['id']:r for r in rules};out=[]
    for code in order:
        r=byid[code];assert r.get('naval','').lower()!='yes'
        air=r['kind']=='AIRCRAFTTYPES' or r.get('consideredaircraft','').lower()=='yes'
        out.append(dict(id=code,name=csf.get(r['uiname'].upper(),r['name']),count=60,domain='air' if air else 'ground',cost=int(r['cost'] or 0),note=special.get(code,''),validation='PROPOSED'))
    excluded=[]
    for r in rules:
        if r['id'] in order:continue
        reason='naval' if r.get('naval','').lower()=='yes' else 'support_civilian_unarmed_or_engine_projectile'
        excluded.append(dict(id=r['id'],name=r['name'],reason=reason))
    return out,excluded

def action(code,p2=0,p7='A'):
    # P1 is the serialized operand kind, not unused padding. WAE Actions.ini
    # uses negative constants: team = -1, CSF = -4. Native maps store 1 and 4.
    kind=1 if code in (7,80,107) else 4 if code in (11,103) else 0
    return [str(code),str(kind),str(p2),'0','0','0','0',p7]

def build(run,unit_ids=None,smoke=False):
    ensure_copy();run=Path(run);run.mkdir(parents=True,exist_ok=False)
    video=ini(GAME/'RA2MD.ini');video['Video'].update(dict(ScreenWidth='1920',ScreenHeight='1080'));video['Options']['GameSpeed']='1';save_ini(video,GAME/'RA2MD.ini')
    display=ini(GAME/'ddraw.ini');display['ddraw'].update(dict(width='1920',height='1080',windowed='true',fullscreen='false',posX='100',posY='100',maxfps='60',maxgameticks='0'));save_ini(display,GAME/'ddraw.ini')
    bridge=json.loads((GAME/'ra2yrcpp.json').read_text(encoding='utf-8-sig'))
    bridge.update(recordFilename=str((run/'NATIVE_STATES.pb.gz').resolve()) if not smoke else '',trafficFilename=str((run/'NATIVE_TRAFFIC.pb.gz').resolve()) if not smoke else '',parseMapDataInterval=1)
    dump(GAME/'ra2yrcpp.json',bridge)
    for name in ['ra2md.csf','spawnmap.ini','spawn.ini']:
        path=GAME/name
        if path.exists():
            assert path.stat().st_nlink==1,'mutable file must not share a hardlink'
            path.chmod(stat.S_IREAD|stat.S_IWRITE)
    sourcebytes=SOURCE.read_bytes();p=configparser.ConfigParser(interpolation=None,strict=False,inline_comment_prefixes=(';',));p.optionxform=str;p.read_string(sourcebytes.decode('utf-8-sig'));(run/'user-map-snapshot.map').write_bytes(sourcebytes)
    # Skirmish supplies the MCVs. Keep the user's moved MCV as the player start.
    if 'Units' in p:
        for key,value in list(p['Units'].items()):
            fields=value.split(',')
            if fields[0]=='<Player @ A>' and fields[1]=='AMCV':
                p['Waypoints']['0']=str(int(fields[4])*1000+int(fields[3]));p['Waypoints']['98']=p['Waypoints']['0'];del p['Units'][key]
    waves,excluded=roster()
    if unit_ids:
        byid={r['id']:r for r in waves};waves=[byid[x] for x in unit_ids]
    labels={};first=15 if smoke else 300;interval=5 if smoke else 240
    # Registries are merged; a user collision is refused rather than overwritten.
    def put(section,key,value):
        if section not in p:p.add_section(section)
        assert key not in p[section],f'generated ID collision: {section}/{key}'
        p[section][key]=str(value)
    def register(section,ident):
        if section not in p:p.add_section(section)
        idx=max([int(k) for k in p[section] if k.isdigit()]+[-1])+1;put(section,str(idx),ident)
    def trigger(ident,when,actions,owner='Neutral'):
        # Scenario triggers are neutral; reinforcement ownership belongs to TeamType.
        put('Triggers',ident,f'{owner},<none>,{ident},0,1,1,1,0')
        put('Events',ident,f'1,13,0,{max(1,when)}')
        flat=[str(len(actions))]+[v for a in actions for v in a];assert len(flat)==1+8*len(actions)
        put('Actions',ident,','.join(flat));put('Tags',ident+'G',f'0,{ident},{ident}')
    p['Basic']['IgnoreGlobalAITriggers']='yes';p['Basic']['Name']='RA2MCP Wave Classroom 60'
    p['Basic'].update(dict(MultiplayerOnly='yes',SkipScore='yes',FreeRadar='yes',InitTime='0'))
    # No unrelated AI production or automatic sale of scripted support buildings.
    if 'IQ' not in p:p['IQ']={}
    p['IQ'].update(dict(Production='6',RepairSell='6',SellBack='6'))
    from overlay import cage
    enemy_start=int(p['Waypoints']['1']);cage_cells=cage(p,enemy_start%1000,enemy_start//1000)
    # MCV deployment can remove ordinary walls. Four unarmed neutral 2x2
    # blockers leave precisely one vehicle cell, with no construction footprint.
    cx,cy=enemy_start%1000,enemy_start//1000
    for i,(dx,dy) in enumerate([(-2,-1),(-1,1),(1,0),(0,-2)]):
        put('Structures','WC_CAGE'+str(i),f'Neutral,GAPOWR,256,{cx+dx},{cy+dy},0,None,0,0,1,0,0,None,None,None,0,0')
    # Keep the user's selected start and route waypoints. Six pads near their spawn waypoint.
    base=int(p['Waypoints'].get('10','100160'));sx,sy=base%1000,base//1000
    spawnids=[10,110,111,112,113,114]
    for wp,(dx,dy) in zip(spawnids,[(0,0),(6,0),(12,0),(0,6),(6,6),(12,6)]):
        p['Waypoints'][str(wp)]=str((sy+dy)*1000+sx+dx)
    def letters(n):
        s='';n+=1
        while n:s=chr(65+(n-1)%26)+s;n=(n-1)//26
        return s
    # Native MoveToWaypoint waits for every member within one cell; large waves
    # deadlock on shared intermediate cells. AttackQuarry uses native pathfinding
    # along this open corridor. User's intermediate waypoints remain editable.
    script='WC_SCRIPT';put(script,'Name','Attack enemy along open corridor');put(script,'0','0,1');register('ScriptTypes',script)
    # Allow the robot tank without changing its rules or creating enemy production factories.
    # Stock support base, with automatic base building disabled below. A real
    # yard is required by the engine to prevent AI liquidation of its support.
    for i,(code,x,y) in enumerate([('GACNST',185,90),('GAPOWR',181,84),('GAROBO',180,90)]):
        ident='WC_OWN'+str(i);trigger(ident,1,[action(14,4476)])
        put('Structures','WC_'+code,f'Neutral,{code},256,{x},{y},0,{ident}G,0,0,1,0,0,None,None,None,0,0')
    # Distinct countries make this native trigger unambiguous: French human,
    # American AI (observed default of this legacy spawner). Disable auto-base
    # after the House exists; scripted reinforcements keep their normal missions.
    trigger('WC_AI_OFF',2,[action(30,0)],'Americans')
    # No auto-base action at scenario load: a skirmish House may not exist yet.
    # The three fixed support structures above are the only enemy base;
    # production factories are absent and global AI triggers stay disabled.
    for i,w in enumerate(waves):
        number=i+1;at=first+i*interval;w.update(number=number,at_seconds=at,at_frame=at*15,spawn=[sx,sy],groups=6,group_size=10)
        force=f'WC_F{number:02}';put(force,'Name',w['id']+' x10');put(force,'0','10,'+w['id']);put(force,'Group','-1');register('TaskForces',force)
        actions=[]
        unit_script=script
        if w['id'] in ('ORCA','BEAG'):
            unit_script='WC_AIR';
            if unit_script not in p:
                put(unit_script,'Name','Air raid on enemy structures');put(unit_script,'0','0,2');register('ScriptTypes',unit_script)
        for group,wp in enumerate(spawnids):
            team=f'WC_T{number:02}{group}';register('TeamTypes',team)
            for key,value in dict(Name=team,House=OWNER,Script=unit_script,TaskForce=force,Group=-1,Max=1,Priority=50,TechLevel=0,VeteranLevel=1,Waypoint=letters(wp),Aggressive='yes',Autocreate='no',Recruiter='no',Reinforce='no',Prebuild='no',AvoidThreats='no',OnlyTargetHouseEnemy='no',Suicide='no',Full='yes').items():put(team,key,value)
            actions.append(action(80,team,letters(wp)))
        prefix=f'WC_{number:02}';labels[prefix+'_NOW']=f'第{number}波：{w["name"]} ×60 已在敌方集结区出发！'
        labels[prefix+'_PREP']=f'下一波 {number}/{len(waves)}：{w["name"]} ×60；从敌方集结区经中路进攻，请准备！'
        labels[prefix+'_TIMER']=f'第{number}波 {w["name"]} ×60'
        labels[prefix+'_WARN']=f'预警：第{number}波 {w["name"]} ×60 即将出发！'
        prep=0 if i==0 else first+(i-1)*interval+1
        timer=first if i==0 else interval-1
        trigger(prefix+'P',prep,[action(11,prefix+'_PREP'),action(103,prefix+'_TIMER'),action(27,timer),action(23)],'Americans')
        if not smoke:
            for reminder,offset in enumerate(range(20,timer-60,60)):
                label=prefix+f'_R{reminder}';labels[label]=f'第{number}波 {w["name"]} ×60：还有{at-prep-offset}游戏秒，从敌方集结区进攻。'
                trigger(prefix+f'R{reminder}',prep+offset,[action(11,label)])
        trigger(prefix+'W',max(prep+1,at-(5 if smoke else 60)),[action(11,prefix+'_WARN')],'Americans')
        actions.append(action(11,prefix+'_NOW'));trigger(prefix+'S',at,actions)
    labels['WC_DONE']='全部波次已出发。清理残敌后可退出；本次过程已记录。'
    trigger('WC_END',waves[-1]['at_seconds']+2,[action(24),action(11,'WC_DONE')],'Americans')
    if smoke:
        # A nonattacking, durable target keeps the validation match alive. Probe only.
        put('AMCV','Strength','200000000');put('AMCV','Immune','yes');put('AMCV','ImmuneToPsionics','yes')
        # Durable unarmed infantry target for anti-infantry-only attackers.
        sys.path.insert(0,str(ROOT/'scripts'))
        from ai_teacher.wdzs_source import parse_ini
        rules=parse_ini((REPORT/'RULES_REFERENCE.ini').read_bytes())['sections'];p['WCDUMMY']=dict(rules['E2'])
        p['WCDUMMY'].update(dict(name='Probe infantry target',image='E2',uiname='Name:E2',primary='none',secondary='none',strength='100000000',immune='yes',immunetopsionics='yes',techlevel='-1'))
        register('InfantryTypes','WCDUMMY')
        for n,(x,y) in enumerate([(89,160),(93,160),(89,164),(93,164)]):put('Infantry','WC_D'+str(n),f'<Player @ A>,WCDUMMY,256,{x},{y},0,Guard,0,None,0,-1,0,-1,0')
        put('GAPOWR','Strength','100000000');put('GAPOWR','Immune','yes');put('GAPOWR','ImmuneToPsionics','yes')
        trigger('WC_TARG',1,[action(14,4475)])
        put('Structures','WC_TARGET',f'Neutral,GAPOWR,256,88,165,0,WC_TARGG,0,0,1,0,0,None,None,None,0,0')
    save_ini(p,run/'spawnmap.ini');shutil.copy2(run/'spawnmap.ini',GAME/'spawnmap.ini')
    csf=append_csf((ROOT/'test-game-1/ra2md.csf').read_bytes(),labels)
    (GAME/'ra2md.csf').write_bytes(csf);(run/'ra2md.csf').write_bytes(csf);dump(run/'MESSAGES.json',labels)
    cfg=ini(ROOT/'test-game-1/spawn.ini');cfg['Settings'].update(dict(Name='Wave-Classroom',Side='2',UIMapName='Wave Classroom 60',IsSinglePlayer='No',PlayerCount='2',AIPlayers='1',Bases='Yes',UnitCount='0',Credits='1000000',GameSpeed='1',ShortGame='No',Superweapons='No',Scenario='spawnmap.ini',Seed='2026110527'))
    cfg['SpawnLocations']={'Multi1':'0','Multi2':'1'};cfg['HouseCountries']['Multi1']='0';cfg['HouseCountries']['Multi2']='8';save_ini(cfg,GAME/'spawn.ini');save_ini(cfg,run/'spawn.ini')
    plan=dict(schema='wave-classroom/1',status='PROPOSED' if smoke else 'BUILT',smoke=smoke,source=str(SOURCE),source_sha256=hashlib.sha256(sourcebytes).hexdigest(),map_sha256=sha(run/'spawnmap.ini'),csf_sha256=sha(run/'ra2md.csf'),first_seconds=first,interval_seconds=interval,timer_unit='15 logic frames per displayed second; wall time depends on game speed',credits=1000000,waves=waves,excluded=excluded,overlap='fixed schedule; previous survivors remain',special_note='support units are not counted as combat waves; aircraft attack still requires native validation',run=str(run))
    plan['mode']='skirmish';plan['cage_cells']=cage_cells
    plan['speed']=dict(user_level=5,native_GameSpeed=1,target_fps=60,render_maxfps=60,maxgameticks=0,status='CONFIGURED_NOT_MEASURED')
    plan['resolution']=[1920,1080];plan['recording']={'native_state':'every engine frame, gzip length-delimited GameState protobuf','native_traffic':'native TunnelPacket stream','observer':'read-only compact snapshots and deduplicated event buffer','initial_map':'full MapDataSoA protobuf','coverage':'bridge-exposed data; sampling/frame gaps measured separately'}
    for name in ['RA2MD.ini','ddraw.ini','ra2yrcpp.json']:
        shutil.copy2(GAME/name,run/name)
    for name in ['classroom.py','record.py','overlay.py','native_events.py']:
        shutil.copy2(Path(__file__).with_name(name),run/name)
    dump(run/'PLAN.json',plan);return plan

def preflight():
    sys.path.insert(0,str(ROOT/'scripts'))
    from observer.batch_runtime import copy_processes
    if copy_processes(GAME):raise RuntimeError('教学游戏已运行，请先在游戏中退出再开始新局。')
    with socket.socket() as s:
        s.bind(('127.0.0.1',PORT))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path);ap.add_argument('--units');ap.add_argument('--smoke',action='store_true');ap.add_argument('--play',action='store_true');args=ap.parse_args()
    preflight();out=args.out or ROOT/'_runs_wave'/time.strftime('%Y%m%d-%H%M%S')
    plan=build(out,args.units.split(',') if args.units else None,args.smoke)
    print(json.dumps(dict(run=str(out),waves=len(plan['waves']),smoke=args.smoke),ensure_ascii=False),flush=True)
    if args.play:
        from record import play
        play(out,smoke=args.smoke)

if __name__=='__main__':main()
