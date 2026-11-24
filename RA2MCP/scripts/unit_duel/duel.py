"""Native spawned, automatically directed combat fixtures; no model training."""
from pathlib import Path
import argparse,asyncio,collections,configparser,gzip,hashlib,json,os,shutil,struct,subprocess,sys,threading,time
ROOT=Path(__file__).resolve().parents[2]
for path in [ROOT,ROOT/'scripts',ROOT/'scripts/wave_defense',ROOT/'vendor/pyra2yr']:sys.path.insert(0,str(path))
sys.path.append(str(ROOT/'.venv-rl/Lib/site-packages'))
import classroom as common
from observer import private_runtime as runtime,collect_private as read
from record import hide_owned_default_windows
from overlay import pack
from inspect_recording import chunks,varint
from native_events import Reader
from combat_trace import CombatTrace
from display_style import NativeDisplay
from google.protobuf.json_format import MessageToDict
from pyra2yr.network import DualClient
from ra2yrproto import commands_yr,ra2yr_pb2
GAME=ROOT/'test-game-duel';PORT=14626
REPORT=ROOT/'reports/unit-duel-20261105'
UNIT_RULES={r['id']:r for r in json.loads((ROOT/'reports/wave-defense-map-20261105/ROSTER_RULES.json').read_text(encoding='utf-8'))}
CASES=[
 dict(id=1,left='MTNK',right='E1',left_n=1,right_n=1,deployed=False,name='灰熊坦克 vs 站立美国大兵'),
 dict(id=2,left='MTNK',right='E1',left_n=1,right_n=1,deployed=True,name='灰熊坦克 vs 部署美国大兵'),
 dict(id=3,left='MTNK',right='GGI',left_n=1,right_n=1,deployed=True,name='灰熊坦克 vs 部署重装大兵'),
 dict(id=4,left='MTNK',right='E1',left_n=1,right_n=4,deployed=True,name='一辆灰熊 vs 四名部署大兵'),
]

def case_units(case):
    """Per-side composition as [[code,count],...]. Legacy cases have one type."""
    if 'left_units' in case:return [list(case['left_units']),list(case['right_units'])]
    return [[[case['left'],case['left_n']]],[[case['right'],case['right_n']]]]

def dump(path,value):common.dump(path,value)
def ensure_copy():
    common.GAME=GAME;common.PORT=PORT
    common.preflight()
    marker=GAME/'DUEL_COPY.json'
    if marker.exists():return
    common.ensure_copy()
    data=(ROOT/'test-game/gamemd-spawn-ra2yrcpp.exe').read_bytes();names=[]
    for guid in ['29e3bb2a-2f36-11d3-a72c-0090272fa661','01AF9993-3492-11d3-8F6F-0060089C05B1']:
        assert data.count(guid.encode())==1;new=guid[:-2]+'06';data=data.replace(guid.encode(),new.encode());names.append(new)
    (GAME/'gamemd-spawn-ra2yrcpp.exe').write_bytes(data)
    dump(GAME/'ra2mcp-instance.json',dict(schema_version=1,instance=5,patched=True,patched_sha256=common.sha(GAME/'gamemd-spawn-ra2yrcpp.exe'),mutex_names=names))
    for name in ['game.fnt','cncnet.fnt']:
        p=GAME/name;p.chmod(0o600);shutil.copyfile(ROOT/'reports/wave-defense-map-20261105/native-font-18px/game-18px.fnt',p)
    dump(marker,dict(port=PORT,instance=5,source='test-game-1',immutable_mix_hardlinks=True))

def letters(n):
    out='';n+=1
    while n:out=chr(65+(n-1)%26)+out;n=(n-1)//26
    return out

def build(out,case,seed,swap,visible):
    ensure_copy();out.mkdir(parents=True,exist_ok=False)
    font_asset=ROOT/'reports/unit-duel-display-20261106/game-27px.fnt'
    for name in ['game.fnt','cncnet.fnt']:
        target=GAME/name
        assert target.stat().st_nlink==1
        if common.sha(target)!=common.sha(font_asset):
            target.chmod(0o600);shutil.copy2(font_asset,target)
    source=ROOT/'maps/training/wave-defense-20261105/wave-defense-v1.map'
    original=source.read_bytes();p=configparser.ConfigParser(interpolation=None,strict=False);p.optionxform=str;p.read_string(original.decode('utf-8-sig'))
    for section in list(p.sections()):
        if section not in ['Map','Basic','Lighting','IsoMapPack5','Preview','PreviewPack']:p.remove_section(section)
    p['OverlayPack']=pack(bytes([255])*262144);p['OverlayDataPack']=pack(bytes(262144))
    p['Basic'].update(dict(Name='RA2MCP Native Unit Duel',IgnoreGlobalAITriggers='yes',FreeRadar='yes',InitTime='0',HomeCell='98',AltHomeCell='98',MultiplayerOnly='yes',SkipScore='yes'))
    p['SpecialFlags']=dict(TiberiumGrows='no',TiberiumSpreads='no',MCVDeploy='no')
    p['IQ']=dict(Production='6',RepairSell='6',SellBack='6')
    p['Americans']=dict(MultiplayPassive='yes',SmartAI='no')
    for difficulty in ['Easy','Normal','Difficult']:
        p[difficulty]=dict(Armor='1.0',ROF='1.0',Firepower='1.0',Groundspeed='1.0',Airspeed='1.0')
    a,b=([124,128],[132,128])
    if swap:a,b=b,a
    p['Waypoints']={str(i):str(y*1000+x) for i,(x,y) in {0:(92,168),1:(170,90),10:a,11:b,98:(128,128)}.items()}
    def put(section,key,value):
        if section not in p:p[section]={}
        p[section][key]=str(value)
    def reg(section,key):put(section,str(len(p[section]) if section in p else 0),key)
    def trigger(key,second,actions,owner='Neutral'):
        put('Triggers',key,f'{owner},<none>,{key},0,1,1,1,0');put('Events',key,f'1,13,0,{second}')
        put('Actions',key,','.join([str(len(actions))]+[x for action in actions for x in action]));put('Tags',key+'G',f'0,{key},{key}')
    # Keep skirmish Houses alive, out of the combat area, without production.
    for side,(cx,cy) in enumerate([(92,168),(170,90)]):
        for i,(dx,dy) in enumerate([(-2,-1),(-1,1),(1,0),(0,-2)]):put('Structures',f'DC{side}{i}',f'Neutral,GAPOWR,256,{cx+dx},{cy+dy},0,None,0,0,1,0,0,None,None,None,0,0')
    trigger('D_OFF',2,[common.action(30,0)],'Americans')
    # Native teams create and deploy units; the observer directs mobile units.
    sides=case_units(case)
    for side,(units,waypoint,deploy) in enumerate([(sides[0],10,False),(sides[1],11,case['deployed'])]):
        force=f'D_F{side}';team=f'D_T{side}';script=f'D_S{side}'
        reg('TaskForces',force);put(force,'Name',' + '.join(f'{code} x{count}' for code,count in units));put(force,'Group','-1')
        for index,(code,count) in enumerate(units):put(force,str(index),f'{count},{code}')
        reg('ScriptTypes',script);put(script,'Name','Native automatic duel');line=0
        if deploy:put(script,str(line),'9,0');line+=1;put(script,str(line),'5,999');line+=1
        else:put(script,str(line),'5,3');line+=1
        # Player tank targets infantry; opponent targets vehicles. Remote MCVs
        # remain far away, so engagement position/target behavior is validated.
        if not deploy:put(script,str(line),'5,999')
        reg('TeamTypes',team)
        values=dict(Name=team,House='<Player @ A>' if side==0 else '<Player @ B>',Script=script,TaskForce=force,Group=-1,Max=1,Priority=50,TechLevel=0,VeteranLevel=1,Waypoint=letters(waypoint),Aggressive='yes',Autocreate='no',Recruiter='no',Reinforce='no',Prebuild='no',AvoidThreats='no',OnlyTargetHouseEnemy='no',Suicide='no',Full='yes')
        for key,value in values.items():put(team,key,value)
    labels={'DUEL_PREP':f'自动对打：{case["name"]}。双方自动出兵和攻击，无需操作。','DUEL_START':'双方已刷新，正在自动对打；结果将自动保存。'}
    trigger('D_REVEAL',1,[common.action(16),common.action(48,4,letters(98)),common.action(11,'DUEL_PREP')])
    spawn_second=5;trigger('D_SPAWN',spawn_second,[common.action(80,'D_T0',letters(10)),common.action(80,'D_T1',letters(11)),common.action(11,'DUEL_START')])
    common.save_ini(p,out/'spawnmap.ini')
    cfg=common.ini(ROOT/'_runs_wave/saved-classroom-20261105-2310/spawn.ini')
    cfg['Settings'].update(dict(Name='Unit-Duel',UIMapName='Native Unit Duel',GameSpeed='1',Credits='0',UnitCount='0',AIDifficulty='1',AutoSaveCount='0',AutoSaveInterval='0',MCVRedeploy='No',ShortGame='No',Seed=str(seed)))
    cfg['HouseHandicaps'].update(Multi1='1',Multi2='1');common.save_ini(cfg,out/'spawn.ini')
    csf=common.append_csf((ROOT/'test-game-1/ra2md.csf').read_bytes(),labels);(out/'ra2md.csf').write_bytes(csf)
    # Keep the native recording inside the isolated copy; archive after exit.
    native_name='duel-native-'+str(time.time_ns())+'.pb.gz'
    bridge=dict(port=PORT,allowedHostsRegex=r'127\.0\.0\.1',singleStep=False,maxConnections=4,logFilename='ra2yrcpp.log',recordFilename=native_name,parseMapDataInterval=1)
    dump(out/'ra2yrcpp.json',bridge)
    video=common.ini(GAME/'RA2MD.ini');video['Video'].update(ScreenWidth='1920',ScreenHeight='1080')
    if 'Options' not in video:video['Options']={}
    video['Options']['GameSpeed']='1';common.save_ini(video,out/'RA2MD.ini')
    display=common.ini(GAME/'ddraw.ini');display['ddraw'].update(width='1920',height='1080',windowed='true',fullscreen='false',posX='100',posY='100',savesettings='0',maxfps='60',maxgameticks='0');common.save_ini(display,out/'ddraw.ini')
    for name in ['spawnmap.ini','spawn.ini','ra2md.csf','ra2yrcpp.json','RA2MD.ini','ddraw.ini']:
        target=GAME/name
        if target.exists():assert target.stat().st_nlink==1;target.chmod(0o600)
        shutil.copy2(out/name,target)
    plan=dict(status='BUILT',case=case,seed=seed,swap=swap,visible=visible,positions=[a,b],distance_cells=8,spawn_frame=75,guard_seconds=3,combat_handicap_multipliers=1.0,rules='stock unit stats; all three difficulty combat multipliers neutralized in map',crushing='native behavior retained; not labeled as pure weapon damage',source_map_sha256=hashlib.sha256(original).hexdigest(),map_sha256=common.sha(out/'spawnmap.ini'),game_exe_sha256=common.sha(GAME/'gamemd-spawn-ra2yrcpp.exe'),bridge_sha256=common.sha(GAME/'libra2yrcpp.dll'),case_costs=[])
    plan['sides']=[[dict(code=code,count=count,name=UNIT_RULES[code]['name'],strength=int(UNIT_RULES[code]['strength']),cost=int(UNIT_RULES[code]['cost'])) for code,count in side] for side in sides]
    plan['case_costs']=[sum(entry['count']*entry['cost'] for entry in side) for side in plan['sides']]
    plan['unit_strength']={code:int(UNIT_RULES[code]['strength']) for side in sides for code,_ in side}
    plan['strengths']=[int(UNIT_RULES[side[0][0]]['strength']) for side in sides]
    plan['mixed']=any(len(side)>1 for side in sides)
    plan['control']='Native spawn; passive enemy global AI; automatic nearest-target orders for mobile units; deployed infantry hold position.'
    plan['presentation']=dict(game_speed=1,target_fps=60,font_rows=27,message_line_height=29,font_sha256=common.sha(font_asset))
    plan['source_code_sha256']={name:common.sha(Path(__file__).parent/name) for name in ['duel.py','combat_trace.py','analyze.py','display_style.py']}
    dump(out/'PLAN.json',plan);shutil.copy2(__file__,out/'duel.py');return plan

def freeze_native(out):
    source=out/'NATIVE_STATES_RAW.pb.gz';buf=b'';count=0;first=None;last=None;gaps=[]
    if not source.exists():return dict(status='MISSING')
    with gzip.open(out/'NATIVE_STATES.pb.gz','wb',compresslevel=1) as writer:
        for chunk in chunks(source):
            buf+=chunk;pos=0
            while pos<len(buf):
                try:length,begin=varint(buf,pos)
                except EOFError:break
                end=begin+length
                if end>len(buf):break
                tag,p=varint(buf,begin);frame=varint(buf,p)[0] if tag==8 else 0
                if last is not None and frame!=last+1:gaps.append([last,frame])
                if first is None:first=frame
                last=frame;count+=1;writer.write(buf[pos:end]);pos=end
            buf=buf[pos:]
    return dict(records=count,first_frame=first,last_frame=last,gaps=gaps,partial_tail_bytes=len(buf),note='Native writer may buffer final frames at process exit; FINAL_STATE.pb preserves the observed outcome separately.')

async def observe(out,proc,plan):
    read._patch_aiohttp_message_size();client,error=await read.wait_for_bridge(PORT,30)
    if client is None:raise RuntimeError(error)
    case=plan['case'];names={};cohort=[{},{}];peaks=[0,0];started=None;first_damage=None;missing_since=None;winner=None;last=None;errors=[];rows=0;damage_seen=[False,False];deployed_seen=False
    sides=case_units(case);target_names=[{UNIT_RULES[code]['name'] for code,_ in side} for side in sides];code_by_name=[{UNIT_RULES[code]['name']:code for code,_ in side} for side in sides]
    expected=[sum(count for _,count in side) for side in sides];expected_hp=[[int(UNIT_RULES[code]['strength']) for code,count in side for _ in range(count)] for side in sides]
    begin=time.monotonic();first_hp=None;last_group=[[],[]];memory=Reader(proc.pid);orders=[];ordered_targets={}
    try:
        with gzip.open(out/'TRACE.jsonl.gz','wt',encoding='utf-8',compresslevel=1) as trace:
            while proc.poll() is None and time.monotonic()-begin<(240 if plan['visible'] else 90):
                state,error=await read.get_state(client)
                if error:
                    errors.append(error)
                    if len(errors)>10:break
                    await asyncio.sleep(.1);continue
                if state.current_frame<1:await asyncio.sleep(.05);continue
                if not names:
                    initial=await read.read_initial(client)
                    if initial is None:continue
                    dump(out/'CATALOG.json',MessageToDict(initial,preserving_proto_field_name=True));(out/'INITIAL_STATE.pb').write_bytes(initial.SerializeToString())
                    names={t.pointer_self:t.name for t in initial.object_types}
                owners={h.self:h.array_index for h in state.houses};groups=[[],[]]
                for o in state.objects:
                    owner=owners.get(o.pointer_house,-1)
                    if owner not in (0,1) or names.get(o.pointer_technotypeclass) not in target_names[owner]:continue
                    if o.health<=0:continue
                    item=dict(address=o.pointer_self,hp=o.health,cell=[o.coordinates.x//256,o.coordinates.y//256],mission=int(o.current_mission),deployed=o.deployed,deploying=o.deploying,on_map=o.on_map,limbo=o.in_limbo)
                    item['type_code']=code_by_name[owner][names.get(o.pointer_technotypeclass)]
                    if UNIT_RULES[item['type_code']]['kind']=='INFANTRYTYPES':
                        # InfantryClass::SequenceAnim at 0x6C4, native YRpp
                        # Sequence 28/29/30 = Deployed/DeployedFire/DeployedIdle.
                        # Existing bridge deployed field is vehicle-only.
                        try:
                            sequence=struct.unpack('<i',memory.read(o.pointer_self+0x6C4,4))[0]
                            item.update(infantry_sequence=sequence,deployed=sequence in (28,29,30),deploying=sequence==27,deployment_source='read-only native infantry animation')
                        except OSError as exc:item['sequence_read_error']=str(exc)
                    groups[owner].append(item);cohort[owner].setdefault(o.pointer_self,item)
                frame=int(state.current_frame);last=state;last_group=groups;rows+=1
                trace.write(json.dumps(dict(frame=frame,groups=groups),ensure_ascii=False)+'\n');trace.flush()
                for side in (0,1):peaks[side]=max(peaks[side],len(groups[side]))
                if groups[1] and all(o['deployed'] for o in groups[1]):deployed_seen=True
                if started is None and [len(g) for g in groups]==expected:
                    started=frame;first_hp=[[o['hp'] for o in group] for group in groups];dump(out/'SPAWN_CHECK.json',dict(frame=frame,groups=groups,counts=expected))
                if started is not None:
                    if frame-started>=45 and groups[0] and groups[1]:
                        for side in (0,1):
                            if side==1 and case['deployed']:continue
                            enemy_ids={o['address'] for o in groups[1-side]}
                            for unit in groups[side]:
                                if ordered_targets.get(unit['address']) in enemy_ids:continue
                                target=min(groups[1-side],key=lambda o:sum((a-b)**2 for a,b in zip(o['cell'],unit['cell'])))
                                await client.exec_command(commands_yr.MissionClicked(object_addresses=[unit['address']],event=ra2yr_pb2.Mission_Attack,target_object=target['address']))
                                ordered_targets[unit['address']]=target['address'];orders.append(dict(frame=frame,action='ATTACK',source=unit['address'],target=target['address'],reason='automatic nearest target; deployed infantry hold position'))
                        dump(out/'AUTO_ORDERS.json',orders)
                    for side in (0,1):
                        if any(o['hp']<plan['unit_strength'][o['type_code']] for o in groups[side]):
                            damage_seen[side]=True
                            if first_damage is None:first_damage=frame
                    absent=2 if not groups[0] and not groups[1] else 0 if not groups[0] and groups[1] else 1 if not groups[1] and groups[0] else None
                    if absent is not None:
                        if missing_since is None:missing_since=frame
                        if frame-missing_since>=30:winner='draw' if absent==2 else 'right' if absent==0 else 'left';break
                    else:missing_since=None
                    if frame-started>1800:break
                elif frame>600:break
                await asyncio.sleep(.05)
    finally:
        memory.close()
        try:await asyncio.wait_for(client.stop(),5)
        except BaseException:pass
    if last:(out/'FINAL_STATE.pb').write_bytes(last.SerializeToString())
    full_health_start=first_hp is not None and all(sorted(first_hp[side])==sorted(expected_hp[side]) for side in (0,1))
    result=dict(status='VERIFIED' if winner and peaks==expected and full_health_start and (not case['deployed'] or deployed_seen) else 'INCOMPLETE',case=case,seed=plan['seed'],swap=plan['swap'],winner=winner,spawn_frame=started,first_damage_frame=first_damage,end_frame=int(last.current_frame) if last else None,combat_game_seconds=(int(last.current_frame)-first_damage)/15 if last and first_damage else None,peak_counts=peaks,initial_hp=first_hp,full_health_start=full_health_start,right_deployed_observed=deployed_seen,damage_seen=damage_seen,remaining=last_group,samples=rows,errors=errors,orders_sent_by_python=0,mode='native team scripts on both sides; native engine micro and crushing allowed')
    result['orders_sent_by_python']=len(orders);result['mode']='native spawns; explicit automatic tank target assignment; native firing and damage'
    dump(out/'RESULT.json',result);return result

def play(out,plan):
    controller=None
    if plan['visible']:
        with (out/'game-stdout.log').open('wb') as so,(out/'game-stderr.log').open('wb') as se:proc=subprocess.Popen([str(GAME/'gamemd-spawn-ra2yrcpp.exe'),'-SPAWN','-CD'],cwd=GAME,stdout=so,stderr=se)
    else:
        proc=runtime.launch_hidden(GAME,out);threading.Thread(target=hide_owned_default_windows,args=(proc,out),daemon=True).start()
    import psutil
    dump(out/'OWNED_GAME.json',dict(pid=proc.pid,path=str(GAME/'gamemd-spawn-ra2yrcpp.exe'),create_time=psutil.Process(proc.pid).create_time()))
    result=None;combat=None;display=None
    try:
        # Let the game finish initial DLL loading before attaching the observer.
        # Readiness before unit spawn is checked again by the data gate.
        time.sleep(1)
        combat=CombatTrace(proc,out)
        display=NativeDisplay(combat.session,out)
        if not plan['visible']:controller=runtime.enable_headless(proc,out)
        result=asyncio.run(observe(out,proc,plan))
        if plan['visible']:
            async def message():
                client=DualClient('127.0.0.1',PORT,timeout=3);client.connect()
                try:
                    text=f"DUEL {plan['case']['left']} vs {plan['case']['right']}: {result['winner'] or 'TIMEOUT'} wins. Result saved."
                    await client.exec_command(commands_yr.AddMessage(message=text,duration_frames=240,color=5))
                finally:await client.stop()
            try:asyncio.run(message())
            except Exception as exc:dump(out/'MESSAGE_ERROR.json',dict(error=repr(exc)))
            time.sleep(8)
    finally:
        if display:display.close()
        if combat:combat.close()
        if proc.poll() is None:
            async def request_exit():
                client=DualClient('127.0.0.1',PORT,timeout=3);client.connect()
                try:
                    await client.exec_command(commands_yr.AddEvent(event=ra2yr_pb2.Event(event_type=ra2yr_pb2.NETWORK_EVENT_Exit),frame_delay=1))
                finally:
                    try:await asyncio.wait_for(client.stop(),2)
                    except Exception:pass
            try:asyncio.run(asyncio.wait_for(request_exit(),6));dump(out/'EXIT_REQUEST.json',dict(event='NETWORK_EVENT_Exit',after_combat=True))
            except Exception as exc:dump(out/'EXIT_REQUEST.json',dict(error=repr(exc)))
            deadline=time.monotonic()+5
            while proc.poll() is None and time.monotonic()<deadline:time.sleep(.1)
        cleanup=runtime.cleanup_owned(proc,controller);dump(out/'CLEANUP.json',cleanup)
        native_name=json.loads((out/'ra2yrcpp.json').read_text())['recordFilename']
        raw=GAME/native_name
        if raw.exists():shutil.move(str(raw),str(out/'NATIVE_STATES_RAW.pb.gz'))
        shutil.copy2(GAME/'ra2yrcpp.log',out/'ra2yrcpp.log')
        coverage=freeze_native(out);dump(out/'NATIVE_COVERAGE.json',coverage)
    if result:
        from scripts.unit_duel.analyze import analyze
        verified=analyze(out);result['status']=verified['status'];result['data_checks']=verified['checks']
        dump(out/'RESULT.json',result)
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--case',type=int,choices=range(1,5),default=1);ap.add_argument('--seed',type=int,default=2026110528);ap.add_argument('--swap',action='store_true');ap.add_argument('--visible',action='store_true');ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    plan=build(args.out,dict(CASES[args.case-1]),args.seed,args.swap,args.visible)
    result=play(args.out,plan);print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
