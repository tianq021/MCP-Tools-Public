"""Validate causal native combat events against independent state snapshots."""
from pathlib import Path
import collections,gzip,html,json
NAMES={'MTNK':'灰熊坦克','HTNK':'犀牛坦克','LTNK':'狂风坦克','TTNK':'磁暴坦克','MGTK':'幻影坦克','APOC':'天启坦克','TNKD':'坦克杀手','FV':'多功能步兵车','HTK':'防空履带车','YTNK':'盖特机炮坦克','V3':'V3 火箭车','HOWI':'榴弹炮','ZEP':'基洛夫空艇','DISK':'镭射幽浮','DRON':'恐怖机器人','BFRT':'战斗要塞','SREF':'光棱坦克','SHAD':'夜鹰直升机','HIND':'雌鹿直升机','DTRUCK':'自爆卡车','E1':'美国大兵','E2':'动员兵','GGI':'重装大兵','FLAKT':'防空步兵','SHK':'磁暴步兵','JUMPJET':'火箭飞行兵','SNIPE':'狙击手','VIRUS':'病毒狙击手','INIT':'尤里新兵','BORIS':'鲍里斯','BRUTE':'狂兽人','TANY':'谭雅','GHOST':'海豹突击队','ADOG':'警犬','LUNR':'登月步兵','IVAN':'疯狂伊文','CIVAN':'超时空伊文','TERROR':'恐怖分子','CLEG':'超时空兵'}
DEPLOYABLE=('E1','GGI')
# Rules-based SelfHealing=yes units. Any unit can additionally gain SELF_HEAL
# through elite promotion, so per-frame measured HP increases are used for all
# units instead of trusting this list alone.
REGENERATING={'APOC','BORIS','BRUTE','TANY','ZEP','DISK'}

def native_hp_steps(out,participants,end_frame):
    """Reconstruct participant HP frame by frame up to the observed outcome frame.

    The native recorder keeps writing after the observer stops, so HP changes
    after end_frame (including regeneration) must not enter this accounting.
    HP can also disappear together with a destroyed object (crushing or normal
    death after the last seen frame), so the last observed HP is returned too.
    """
    import sys
    ROOT=Path(__file__).resolve().parents[2]
    for path in (ROOT,ROOT/'scripts',ROOT/'scripts/wave_defense',ROOT/'.venv-rl/Lib/site-packages'):
        if str(path) not in sys.path:sys.path.insert(0,str(path))
    from inspect_recording import chunks,varint
    from ra2yrproto.ra2yr_pb2 import GameState
    source=Path(out)/'NATIVE_STATES.pb.gz'
    if not source.exists():return None
    previous={};drops=collections.Counter();raises=collections.Counter();frames=0;first=None;last=None;gaps=[];buffer=b'';stopped=False;present_at_end=set()
    for chunk in chunks(source):
        buffer+=chunk;position=0
        while position<len(buffer):
            try:length,begin=varint(buffer,position)
            except EOFError:break
            end=begin+length
            if end>len(buffer):break
            state=GameState.FromString(buffer[begin:end]);frame=int(state.current_frame)
            if frame>end_frame:stopped=True;break
            frames+=1
            if last is not None and frame!=last+1:gaps.append([last,frame])
            if first is None:first=frame
            last=frame
            if frame==end_frame:present_at_end={item.pointer_self for item in state.objects if item.pointer_self in participants}
            for item in state.objects:
                address=item.pointer_self
                if address not in participants:continue
                hp=item.health
                if address in previous:
                    delta=hp-previous[address]
                    if delta<0:drops[address]+=-delta
                    elif delta>0:raises[address]+=delta
                previous[address]=hp
            position=end
        if stopped:break
        buffer=buffer[position:]
    return dict(frames=frames,first_frame=first,last_frame=last,gaps=gaps,drops=dict(drops),raises=dict(raises),last_hp=previous,present_at_end=present_at_end,stopped_after_outcome=stopped)

def case_units(case):
    """Per-side composition as [[code,count],...]. Legacy cases have one type."""
    if 'left_units' in case:return [list(case['left_units']),list(case['right_units'])]
    return [[[case['left'],case['left_n']]],[[case['right'],case['right_n']]]]

def write(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

def analyze(out,report_dir=None):
    out=Path(out);dest=Path(report_dir) if report_dir else out;dest.mkdir(parents=True,exist_ok=True)
    result=json.loads((out/'RESULT.json').read_text());plan=json.loads((out/'PLAN.json').read_text())
    sides=case_units(result['case']);expected=[sum(count for _,count in side) for side in sides]
    hook=json.loads((out/'COMBAT_HOOK_STATUS.json').read_text());coverage=json.loads((out/'NATIVE_COVERAGE.json').read_text())
    with gzip.open(out/'COMBAT_EVENTS.jsonl.gz','rt',encoding='utf-8') as f:all_events=[json.loads(x) for x in f]
    events=[e for e in all_events if e['frame']<=result['end_frame']]
    spawn=json.loads((out/'SPAWN_CHECK.json').read_text());participants={o['address']:side for side,g in enumerate(spawn['groups']) for o in g}
    remaining={o['address']:o['hp'] for g in result['remaining'] for o in g}
    units={}
    for side,g in enumerate(spawn['groups']):
        for o in g:
            units[o['address']]=dict(side=side,type=o.get('type_code') or result['case'].get('left' if side==0 else 'right') or 'UNKNOWN',initial_hp=o['hp'],final_hp=remaining.get(o['address'],0),damage_received=0,damage_dealt=0,environmental_damage=0,shots=0,kills=[],defeated_by=None,source_known=True)
    environmental=[]
    for e in events:
        if e['kind']=='damage':
            v=e['victim'] or {};a=e['attacker'] or {};hp=e.get('hp_loss',0)
            if v.get('address') in units:
                units[v['address']]['damage_received']+=hp
                units[v['address']]['id']=v.get('id')
                units[v['address']].setdefault('damage_events',[]).append((e['event_id'],v.get('hp'),(e.get('victim_after') or {}).get('hp'),hp))
                if hp>0 and a.get('address') not in units:
                    # No attacker and no tracked projectile means native death
                    # collateral/environment damage; it is counted separately.
                    if not a and not e.get('projectile_origin'):
                        units[v['address']]['environmental_damage']+=hp;environmental.append(e)
                    else:units[v['address']]['source_known']=False
            if a.get('address') in units:units[a['address']]['damage_dealt']+=hp
        elif e['kind']=='fire':
            a=e.get('attacker') or {}
            if a.get('address') in units:units[a['address']]['shots']+=1
        elif e['kind']=='kill_credit':
            v=e['victim'] or {};a=e['killer'] or {}
            if v.get('address') in units:units[v['address']]['defeated_by']=a;units[v['address']]['defeat_frame']=e['frame'];units[v['address']]['killer_source']=e.get('killer_source','native_register_destruction');units[v['address']]['engine_credited_killer']=e.get('engine_credited_killer',a)
            if a.get('address') in units:units[a['address']]['kills'].append(v.get('id'))
    steps=native_hp_steps(out,participants,result['end_frame'])
    if steps:
        for address,unit in units.items():
            unit['native_hp_drops']=steps['drops'].get(address,0)
            unit['hp_regenerated']=steps['raises'].get(address,0)
            unit['observed_final_hp']=steps['last_hp'].get(address,unit['initial_hp'])
            unit['present_at_end']=address in steps['present_at_end']
            unit['vanished_hp']=0 if unit['present_at_end'] else unit['observed_final_hp']
            unit['removed_without_killer']=bool(not unit['present_at_end'] and unit['observed_final_hp']>0 and not unit['defeated_by'])
    def bounds(unit):
        """Observed per-frame HP drops may never exceed damage callbacks.

        A damage callback can coincide with a self-heal tick, so damage totals can
        be *higher* than the visible HP drops; the reverse (a visible drop with no
        damage callback) would mean a missed damage event."""
        if 'native_hp_drops' not in unit:return False
        return unit['native_hp_drops']<=unit['damage_received']
    def chain_ok(unit):
        """Damage callbacks must form a continuous HP trace for the unit."""
        events=unit.get('damage_events') or []
        last_after=None
        for _,before,after,_ in events:
            if before is not None and after is not None and after>before:return False
            if last_after is not None and before is not None and before<last_after:return False
            if after is not None:last_after=after
        if events and events[0][1] is not None and events[0][1]>unit['initial_hp']:return False
        observed=unit.get('observed_final_hp')
        if observed is None:return False
        if last_after is None:
            # No damage callbacks: a survivor must still be at full spawn HP; a
            # unit removed without damage is recorded as a non-damage removal.
            if unit.get('present_at_end',False):return observed==unit['initial_hp']
            return True
        return observed>=last_after
    def reconciles(unit):
        """Per-frame HP bookkeeping: observed change equals drops minus heal ticks."""
        if 'observed_final_hp' not in unit or 'native_hp_drops' not in unit:return False
        return (unit['initial_hp']-unit['observed_final_hp'])==(unit['native_hp_drops']-unit.get('hp_regenerated',0))
    checks={
        'spawn_counts_and_full_hp':result['full_health_start'] and result['peak_counts']==expected,
        'winner_observed':bool(result['winner']),
        'hooks_before_spawn':hook['frame']<result['spawn_frame'],
        'hook_errors_empty':not hook['errors'],
        'event_sequence_complete':[e['event_id'] for e in all_events]==list(range(1,len(all_events)+1)),
        'native_frames_through_outcome':coverage.get('last_frame',-1)>=result['end_frame'] and not coverage.get('gaps',[]) and coverage.get('partial_tail_bytes',-1)==0,
        'native_hp_steps_complete':bool(steps) and steps['last_frame']==result['end_frame'] and not steps['gaps'] and steps['frames']==result['end_frame']-steps['first_frame']+1,
        'damage_matches_native_hp_steps':bool(steps) and all(bounds(u) and chain_ok(u) for u in units.values()),
        'damage_reconciles_with_all_hp':all(reconciles(u) for u in units.values()),
        'all_damaging_sources_identified':all(u['source_known'] for u in units.values()),
        'projectile_sources_match_emitted_shot':all(
            e.get('source_house') in (0,(e.get('attacker') or {}).get('house')) and
            e['projectile_origin']['created_frame']<=e['frame'] and e['projectile_origin']['warhead_id']==e['warhead_id'] and
            any(s['kind']=='fire' and s['projectile']==e['projectile_origin']['projectile'] and s['frame']==e['projectile_origin']['created_frame'] and (s.get('attacker') or {}).get('id')==(e.get('attacker') or {}).get('id') for s in events)
            for e in events if e['kind']=='damage' and e.get('attacker_source')=='tracked_projectile_origin'),
        'every_defeat_has_traced_killer':all(u['final_hp']>0 or u['defeated_by'] or u.get('removed_without_killer',False) for u in units.values()),
    }
    shots=[e for e in events if e['kind']=='fire' and participants.get((e.get('attacker') or {}).get('address'))==1]
    slots=dict(collections.Counter(e['weapon_index'] for e in shots))
    right_type={address:units[address]['type'] for address in units if units[address]['side']==1}
    if result['case'].get('deployed'):
        checks['requested_deployment_weapon']=all(e['weapon_index']==1 for e in shots) and result['right_deployed_observed']
    else:
        # Only units that were asked to stay undeployed as deployable infantry
        # must use their native slot-0 weapon; other units choose their own slot
        # (recorded in weapon_slots) exactly as in the single-type mode.
        deployable_shots=[e for e in shots if right_type.get((e.get('attacker') or {}).get('address')) in DEPLOYABLE]
        checks['requested_deployment_weapon']=all(e['weapon_index']==0 for e in deployable_shots)
    try:
        with gzip.open(out/'NATIVE_STATES_RAW.pb.gz','rb') as f:
            while f.read(1024*1024):pass
        checks['native_gzip_crc']=True
    except (OSError,EOFError):checks['native_gzip_crc']=False
    summary=dict(status='VERIFIED' if all(checks.values()) else 'INCOMPLETE',checks=checks,units=units,case=result['case'],seed=result['seed'],swap=result['swap'],winner=result['winner'],event_counts=dict(collections.Counter(e['kind'] for e in events)),source='Native Fire, ReceiveDamage and RegisterDestruction callbacks; per-frame native HP cross-check; measured regeneration, crushing and unattributed death collateral accounted separately',native_coverage=coverage,native_hp_steps=None if not steps else dict(frames=steps['frames'],first_frame=steps['first_frame'],last_frame=steps['last_frame'],gaps=steps['gaps'],regenerated_hp=sum(steps['raises'].values()),stopped_after_outcome=steps['stopped_after_outcome']),environmental_damage=dict(events=len(environmental),hp=sum(e.get('hp_loss',0) for e in environmental)),weapon_slots=slots,crushing_or_removal_hp=sum(u.get('vanished_hp',0) for u in units.values()),removed_without_killer=[dict(type=u['type'],side=u['side'],observed_hp=u['observed_final_hp']) for u in units.values() if u.get('removed_without_killer')],training_performed=False)
    write(dest/'COMBAT_ANALYSIS.json',summary)
    regen=sum(u.get('hp_regenerated',0) for u in units.values());crush=summary['crushing_or_removal_hp'];env=summary['environmental_damage'];removed=summary['removed_without_killer']
    notes=[]
    if regen:notes.append(f'本局逐帧原生状态测得自愈 {regen} HP（含单位精英化后的 SELF_HEAL）；自愈不是伤害事件，已在扣血对账中单独扣除。')
    if crush:notes.append(f'有 {crush} HP 随单位阵亡瞬间消失（碾压或销毁，非逐次武器伤害），按“消失 HP”单独记录。')
    if env['events']:notes.append(f'有 {env["events"]} 次无攻击者、无弹丸来源的原生伤害共 {env["hp"]} HP（单位阵亡附带伤害），已单独计数。')
    if removed:notes.append('有 {0} 个单位在血量仍大于 0 时被非伤害方式移除（自爆/碾压/抹除类），无原生击杀归属，已单独记录：{1}。'.format(len(removed),'、'.join(f"{NAMES.get(u['type'],u['type'])}（剩余 {u['observed_hp']} HP）" for u in removed)))
    extra=''.join(f'<p>{html.escape(n)}</p>' for n in notes)
    def label(o):
        if not o:return '未知来源'
        code=o.get('type') or units.get(o.get('address'),{}).get('type','未知对象')
        return f"{NAMES.get(code,code)} #{o.get('id','?')}"
    rows=[]
    for e in events:
        kind=e['kind'];a=e.get('attacker') if kind!='kill_credit' else e.get('killer');v=e.get('victim') if kind!='fire' else e.get('target')
        detail=(f"武器槽 {e['weapon_index']}；弹丸 {e['projectile']}" if kind=='fire' else f"{e.get('warhead_id','?')}；原始伤害 {e['damage_input']}；结算伤害 {e.get('damage_adjusted')}；实际扣血 {e.get('hp_loss')}；血量 {v.get('hp')} → {e.get('victim_after',{}).get('hp')}" if kind=='damage' else '原生击杀归属' if e.get('killer_source','native_register_destruction')=='native_register_destruction' else '原生击杀者为空；致命来源由弹丸发射链确认')
        rows.append(f'<tr data-kind="{kind}"><td>{e["event_id"]}</td><td>{e["frame"]}</td><td>{dict(fire="开火",damage="受伤",kill_credit="击败")[kind]}</td><td>{html.escape(label(a))}</td><td>{html.escape(label(v))}</td><td>{html.escape(detail)}</td></tr>')
    (dest/'战斗明细.html').write_text('''<!doctype html><meta charset="utf-8"><title>单位自动对打记录</title><style>body{font:16px sans-serif;background:#151b22;color:#dbe4ed;margin:30px}table{border-collapse:collapse;width:100%}th,td{padding:8px;text-align:left;border-bottom:1px solid #38414b}th{position:sticky;top:0;background:#26323e}button{padding:8px;margin:8px;color:#17212b}p{line-height:1.6}</style>'''+f'<h1>{html.escape(result["case"]["name"])}</h1><p>数据验收：{summary["status"]}。种子 {result["seed"]}；交换出生位置 {result["swap"]}；获胜方 {result["winner"]}。记录的是原生事件，不是模型预测。</p><p>“实际扣血”不包含过量伤害；受伤前的伤害参数不等于最终扣血。死亡事件可能在受伤函数返回前登记，因此同帧显示顺序以回调完成顺序为准。</p>'+extra+'<p><a href="COMBAT_ANALYSIS.json">单位总伤害与击败者（JSON）</a></p>'+''.join(f'<button onclick="filter(\'{k}\')">{t}</button>' for k,t in [('', '全部'),('fire','开火'),('damage','伤害'),('kill_credit','击败')])+'<table><thead><tr><th>序号</th><th>帧</th><th>事件</th><th>来源</th><th>目标</th><th>详情</th></tr></thead><tbody>'+''.join(rows)+'''</tbody></table><script>function filter(k){document.querySelectorAll('tbody tr').forEach(r=>r.hidden=k&&r.dataset.kind!==k)}</script>''',encoding='utf-8')
    return summary

if __name__=='__main__':
    import sys
    print(json.dumps(analyze(Path(sys.argv[1])),ensure_ascii=False))
