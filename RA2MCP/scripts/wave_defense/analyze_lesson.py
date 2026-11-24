"""Describe a human wave-defense demonstration without inferring causal counters."""
from pathlib import Path
import bisect,collections,datetime,gzip,html,json,math,sys
from inspect_recording import chunks,varint,GameState
ROOT=Path(__file__).resolve().parents[2]

def analyze(run,out):
    out.mkdir(parents=True,exist_ok=False)
    plan=json.loads((run/'PLAN.json').read_text());catalog=json.loads((run/'CATALOG.json').read_text())
    rules={r['id']:r for r in json.loads((ROOT/'reports/wave-defense-map-20261105/ROSTER_RULES.json').read_text())}
    types={t['pointer_self']:t for t in catalog['object_types']}
    sys.path.insert(0,str(ROOT/'scripts'))
    from ai_teacher.wdzs_source import parse_ini
    from classroom import csf_read
    rawrules=parse_ini((ROOT/'reports/wave-defense-map-20261105/RULES_REFERENCE.ini').read_bytes())['sections']
    csf=csf_read((run/'ra2md.csf').read_bytes())
    chinese={v['name']:csf.get(v.get('uiname','').upper(),v['name']) for v in rawrules.values() if v.get('name')}
    # Native names are not unique: HARV and the unbuildable HORV are both
    # called War Miner. Match native array type and registry position instead.
    pointers={}
    for section,native in [('VEHICLETYPES','ABSTRACT_TYPE_UNITTYPE'),('INFANTRYTYPES','ABSTRACT_TYPE_INFANTRYTYPE'),('AIRCRAFTTYPES','ABSTRACT_TYPE_AIRCRAFTTYPE')]:
        for index,code in enumerate(rawrules[section].values()):
            matches=[p for p,t in types.items() if t.get('type')==native and t.get('array_index',0)==index]
            if matches:pointers[code]=matches[0]
    assert all(types[pointers[w['id']]].get('name')==rules[w['id']]['name'] for w in plan['waves'])
    with gzip.open(run/'OBSERVATIONS.jsonl.gz','rt',encoding='utf-8') as f:samples=[json.loads(line) for line in f]
    with gzip.open(run/'EXECUTED_NATIVE_EVENTS/events.jsonl.gz','rt',encoding='utf-8') as f:events=[json.loads(line) for line in f]
    frames=[s['frame'] for s in samples];starts=[w['at_frame'] for w in plan['waves']]
    rows=[];lifetimes={};previous={};damage=[0.0]*len(samples);healing=[0.0]*len(samples);transfers=[]
    for i,s in enumerate(samples):
        now={o[8]:o for o in s['objects']}
        for o in s['objects']:
            key=(o[8],o[9]);life=lifetimes.setdefault(key,dict(first=i,last=i,last_object=o));life.update(last=i,last_object=o)
            prev=previous.get(o[8]);t=types.get(o[9],{});strength=t.get('strength',0);cost=t.get('cost',0)
            if prev and prev[9]==o[9] and prev[0]==o[0]==0 and prev[2]>0 and strength>0:
                delta=prev[2]-max(0,o[2]);damage[i]+=max(0,delta)*cost/strength;healing[i]+=max(0,-delta)*cost/strength
            if prev and prev[9]==o[9] and prev[0]!=o[0]:transfers.append(dict(frame=s['frame'],name=o[1],address=o[8],from_house=prev[0],to_house=o[0]))
        previous=now
    def alive(o):return o[2]>0 and o[5] and not o[6]
    def inventory(sample,owner):return collections.Counter(o[1] for o in sample['objects'] if o[0]==owner and alive(o))
    def top_defenders(sample):
        c=inventory(sample,0);noncombat={'Allied Construction Yard','Allied Power Plant','Allied Ore Refinery','Allied Barracks','Allied War Factory','Allied Airforce Command Headquarters','Allied Battle Lab','Allied SpySat Uplink','Allied Robot Control Center','Allied Service Depot','Allied Ore Processor','Chrono Miner','Engineer'}
        return dict((k,v) for k,v in c.most_common() if k not in noncombat and not k.startswith('Tech '))
    for j,w in enumerate(plan['waves']):
        start=w['at_frame'];end=starts[j+1] if j+1<len(starts) else frames[-1]+1
        lo=bisect.bisect_left(frames,start);hi=bisect.bisect_left(frames,end);pre=samples[max(0,lo-1)];post=samples[min(len(samples)-1,hi)]
        ptr=pointers[w['id']];active=[];seen=set();losses=collections.Counter();proxy=0
        for si,s in enumerate(samples[lo:],lo):
            units=[o for o in s['objects'] if o[0]==1 and o[9]==ptr and alive(o)]
            if units:active.append((si,len(units)));seen.update(o[8] for o in units)
        for (addr,typ),life in lifetimes.items():
            o=life['last_object'];li=life['last']
            # A disappearance is not a confirmed kill: transport, deploy,
            # sale, mind control, and object-address reuse can confound it.
            if o[0]==0 and lo<=li<hi and li<len(samples)-2 and o[5] and not o[6] and 'Construction Vehicle' not in o[1]:
                losses[o[1]]+=1;proxy+=types.get(typ,{}).get('cost',0)
        ev=[e for e in events if start<=e['frame']<end]
        row=dict(number=w['number'],id=w['id'],name=w['name'],scheduled_start_frame=start,end_frame=end,observed_peak_1hz=max((n for _,n in active),default=0),observed_addresses_1hz=len(seen),last_seen_enemy_frame=frames[active[-1][0]] if active else None,enemy_duration_game_seconds=round((frames[active[-1][0]]-start)/15,1) if active else None,still_active_next_wave=bool(active and frames[active[-1][0]]>=end),defenders_before=top_defenders(pre),defenders_after=top_defenders(post),own_hp_damage_cost_equivalent_lower_bound=round(sum(damage[lo:hi]),1),own_hp_recovery_cost_equivalent=round(sum(healing[lo:hi]),1),own_permanent_disappearance_candidates=dict(losses),disappearance_cost_proxy=proxy,start_cash=pre['houses'][0][2],end_cash=post['houses'][0][2],cash_delta=post['houses'][0][2]-pre['houses'][0][2],events=dict(collections.Counter(e['name'] for e in ev)),event_count=len(ev),owned_transfer_events=[x for x in transfers if start<=x['frame']<end],native_spawn_check={})
        rows.append(row)
    # Verify the fast-spawning / teleporting waves against every native frame
    # around each scheduled spawn, rather than relying on 1 Hz snapshots.
    buf=b'';native_count=0;native_sets=[set() for w in rows];ownerptr=next(h['self'] for h in catalog['houses'] if h.get('array_index')==1)
    for chunk in chunks(run/'NATIVE_STATES.pb.gz'):
        buf+=chunk;pos=0
        while pos<len(buf):
            try:length,begin=varint(buf,pos)
            except EOFError:break
            end=begin+length
            if end>len(buf):break
            tag,p=varint(buf,begin);frame=varint(buf,p)[0] if tag==8 else 0;j=bisect.bisect_right(starts,frame)-1
            if j>=0 and frame-starts[j]<=600:
                state=GameState.FromString(buf[begin:end]);ptr=pointers[rows[j]['id']]
                objects=[o for o in state.objects if o.pointer_house==ownerptr and o.pointer_technotypeclass==ptr and o.health>0]
                check=rows[j]['native_spawn_check']
                if objects:
                    check.setdefault('first_seen_frame',frame);check['peak']=max(check.get('peak',0),len(objects));native_sets[j].update(o.pointer_self for o in objects)
            pos=end;native_count+=1
        buf=buf[pos:]
    for row,seen in zip(rows,native_sets):row['native_spawn_check']['distinct_addresses_in_first_600_frames']=len(seen)
    action_types=collections.Counter(e['name'] for e in events);missions=collections.Counter(e.get('args',{}).get('mission') for e in events if e['type']==4)
    summary=dict(status='VERIFIED_DESCRIPTIVE_ANALYSIS',run=str(run),sample_count=len(samples),native_frames_scanned=native_count,first_frame=frames[0],last_frame=frames[-1],game_seconds=round(frames[-1]/15,1),wall_seconds=round(samples[-1]['wall']-samples[0]['wall'],1),waves=len(rows),native_spawn_peak_60=sum(r['native_spawn_check'].get('peak',0)>=60 for r in rows),native_spawn_addresses_60=sum(r['native_spawn_check'].get('distinct_addresses_in_first_600_frames',0)>=60 for r in rows),start_cash=samples[0]['houses'][0][2],end_cash=samples[-1]['houses'][0][2],final_houses=samples[-1]['houses'],final_own_inventory=dict(inventory(samples[-1],0)),final_enemy_inventory=dict(inventory(samples[-1],1)),events=len(events),event_types=dict(action_types),mission_types=dict(missions),overlapping_wave_windows=sum(r['still_active_next_wave'] for r in rows),limitations=['One human-controlled game with 1,000,000 starting credits, not a trained-model evaluation.','A count of native per-unit events is not a count of mouse clicks or independent tactical decisions.','HP drops are lower bounds from 1 Hz observations; disappearing objects are not automatically kills.','Cash change includes ore, oil, construction, repairs, refunds; it is not a spending or exchange ratio.','Fixed defenses, quantity, veterancy, repair and overlap confound unit-counter attribution.','Consumed-ring recovery retained the beginning at frame 21 before the first ring wrap; asynchronous polling is not a formally lossless hook.','All 51 wave types were tested, but deployed/passenger variants and navy were excluded.'])
    (out/'ANALYSIS.json').write_text(json.dumps(dict(summary=summary,waves=rows),ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'ACTION_SUMMARY.json').write_text(json.dumps(dict(event_types=dict(action_types),mission_types=dict(missions),ownership_changes=transfers),ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'NAME_TRANSLATIONS.json').write_text(json.dumps(chinese,ensure_ascii=False,indent=2),encoding='utf-8')
    columns=['波次','兵种','原生峰值','结束前存留至下一波','受损折价下界','消失对象价值参考','主要防御兵力（波前）']
    table=[]
    for r in rows:
        defs='、'.join(f'{chinese.get(k,k)}×{v}' for k,v in list(r['defenders_before'].items())[:6])
        values=[r['number'],r['name'],r['native_spawn_check'].get('peak',0),'是' if r['still_active_next_wave'] else '否',r['own_hp_damage_cost_equivalent_lower_bound'],r['disappearance_cost_proxy'],defs]
        table.append('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in values)+'</tr>')
    doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>波次教学复盘</title><style>body{font:16px system-ui;margin:32px;color:#18202c}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccd2da;padding:8px;text-align:left}th{background:#e7eef7;position:sticky;top:0}tr:nth-child(even){background:#f6f8fb}.note{padding:16px;background:#fff2cc}h1{font-size:28px}</style><h1>51 波教学局：数据复盘</h1>'
    doc+=f'<p>连续原生帧 {native_count:,}；观测 {len(samples):,}；已执行指令 {len(events):,}。起始资金 1,000,000，结尾资金 {summary["end_cash"]:,}。</p>'
    doc+='<p class="note">这是单局人类示范。受损折价＝观测到的血量下降×单位原价÷基础血量，是下界；对象消失不等于被击杀，两项均不能直接当作换血比。波前兵力不等于实际参战者。完整原始数据及限制见 ANALYSIS.json。</p><table><thead><tr>'+''.join('<th>'+x+'</th>' for x in columns)+'</tr></thead><tbody>'+''.join(table)+'</tbody></table></html>'
    (out/'波次复盘.html').write_text(doc,encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    print('TOP_DAMAGE',json.dumps([(r['number'],r['name'],r['own_hp_damage_cost_equivalent_lower_bound'],r['disappearance_cost_proxy'],r['own_permanent_disappearance_candidates']) for r in sorted(rows,key=lambda r:r['own_hp_damage_cost_equivalent_lower_bound'],reverse=True)[:8]],ensure_ascii=False),flush=True)

if __name__=='__main__':analyze(Path(sys.argv[1]),Path(sys.argv[2]))
