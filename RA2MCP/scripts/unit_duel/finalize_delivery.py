"""Freeze and register this bounded validation; not part of normal launcher."""
from pathlib import Path
import collections,datetime,gzip,hashlib,html,json,shutil,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.coordination import context_store as s
R=ROOT/'reports/unit-duel-20261105'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,o):p.write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    selected=[(R/'validation-v2'/f'{i:03d}',None) for i in range(1,11)]
    selected.append((R/'validation-v3-tail/001',R/'validation-v3-tail/001/reanalysis-source-chain'))
    selected += [(R/'validation-v4-tail'/f'{i:03d}',None) for i in range(1,6)]
    rows=[];events=collections.Counter();frames=0;units=0;source_chain=0;links=[]
    for run,recheck in selected:
        ap=(recheck or run)/'COMBAT_ANALYSIS.json';a=json.loads(ap.read_text(encoding='utf-8'));p=json.loads((run/'PLAN.json').read_text(encoding='utf-8'));res=json.loads((run/'RESULT.json').read_text(encoding='utf-8'))
        assert a['status']=='VERIFIED' and all(a['checks'].values()),str(run)
        events.update(a['event_counts']);frames+=a['native_coverage']['records'];units+=len(a['units'])
        with gzip.open(run/'COMBAT_EVENTS.jsonl.gz','rt',encoding='utf-8') as f:source_chain+=sum(json.loads(x).get('attacker_source')=='tracked_projectile_origin' for x in f)
        row=dict(run=str(run.relative_to(ROOT)),analysis=str(ap.relative_to(ROOT)),case=p['case'],seed=p['seed'],swap=p['swap'],winner=a['winner'],remaining_hp=[sum(o['hp'] for o in g) for g in res['remaining']],map_sha256=p['map_sha256'],config_sha256=sha(run/'spawn.ini'))
        rows.append(row);target=((recheck or run)/'战斗明细.html').relative_to(R).as_posix()
        links.append(f'<tr><td>{len(rows)}</td><td>{html.escape(p["case"]["name"])}</td><td>{p["seed"]}</td><td>{p["swap"]}</td><td>{dict(left="左方",right="右方",draw="同归于尽")[a["winner"]]}</td><td><a href="{target}">逐次开火、伤害、击败者</a></td></tr>')
    visible=R/'visible-camera-check-v2';v=json.loads((visible/'COMBAT_ANALYSIS.json').read_text(encoding='utf-8'));assert v['status']=='VERIFIED'
    summary=dict(status='VERIFIED',task_id='TASK-20261105-028',N=16,units=units,native_frames=frames,event_counts=dict(events),damage_sources_recovered_from_projectile=source_chain,random_pool=['MTNK','HTNK','LTNK','E1','E2','GGI'],model_training_performed=False,runs=rows,visible_validation=str(visible.relative_to(ROOT)),limits=['Ground pool of six types; no naval or air yet','Variable counts/costs; not a fair equal-cost counter ranking','Free-running command timing is recorded but not bitwise deterministic','Environmental, temporal and mind-control deaths not covered by this pool'])
    dump(R/'ACCEPTED_SUMMARY.json',summary)
    (R/'本批对抗记录.html').write_text('<!doctype html><meta charset="utf-8"><title>自动单位对抗验收</title><style>body{font:17px sans-serif;background:#f2f5f8;color:#172331;margin:35px}table{border-collapse:collapse;width:100%}td,th{padding:11px;border-bottom:1px solid #b6c4d0;text-align:left}</style><h1>自动单位对抗：16 组已验收</h1><p>双方原生刷新并自动交战；每次开火、伤害来源、扣血与击败者均可查看。仅采集数据，没有更新模型。</p><p>灰熊、犀牛、狂风、美国大兵、动员兵、重装大兵；同归于尽和阵亡后炮弹命中也保留来源链。</p><table><tr><th>局</th><th>配置</th><th>种子</th><th>交换方向</th><th>结果</th><th>明细</th></tr>'+''.join(links)+'</table>',encoding='utf-8')
    text=f'''# 随机单位自动对抗：交付与验证

VERIFIED：16 组配置通过数据验收，涵盖 6 个地面兵种、两个出生方向、单兵和多兵、部署与站立、双方同归于尽。额外可见测试通过，客户区 1920×1080，双方出现在交战镜头中。

入口：项目根 `开始单位自动对抗.cmd`，选 1「随机兵种自动对抗并采集数据」。可选局数（默认 10，1–100）、随机种子、可见/后台。每局一方随机 1–3 辆坦克，另一方随机 1–4 个池内兵种；固定场景选 2，最近数据选 3。可见模式结束后停留 8 秒再自动退出，不需要人工战斗。

未来数据保存在 `_runs_duel/日期时间/`；本次验收总览为 `reports/unit-duel-20261105/本批对抗记录.html`。

正式样本合计：{units} 个参战单位，{frames} 个原生状态帧，{events['fire']} 次开火记录，{events['damage']} 次受伤回调，{events['kill_credit']} 次击杀登记。{source_chain} 次伤害通过弹丸发射来源链恢复攻击者；原生空来源仍原样保留。来源、目标均保留对象地址及原生 UniqueID，另有兵种、发生帧、弹头、武器槽、原始/结算伤害、实际扣血、前后血量。

最终验收名单与配置哈希：`ACCEPTED_SUMMARY.json`。基础 8 局用种子 2026110528，随机 8 局由 2026110601 生成，各局引擎种子独立保存。不同造价、数量和控制方式的结果不可直接推成普遍兵种强弱排名。这是原生引擎的受控数据采集，不是模型已经学会克制、打赢电脑或完成自对抗。

原始证据不覆盖：validation-v2 的前 10 局通过；第 11 局发现阵亡弹丸空来源，保留为诊断失败。validation-v3-tail/001 的原生记录完整，首次分析错误要求引擎来源 House 非零，后经发射帧、弹丸地址、攻击者 ID 和弹头交叉验证，通过独立 `reanalysis-source-chain/` 修订，原分析保留。其余 5 局在 validation-v4-tail 完成。所有已接受单位累计扣血与独立终局 HP 一致，原生录像 CRC 和帧连续性通过。

一次额外可见启动的观察器附加失败，已保留 `visible-camera-check` 并清理 owned 进程；加入初始化等待后 `visible-camera-check-v2` 通过。若未来记录器无法就绪或数据验收失败，批次会停止并保留证据，不把失败当有效样本。

方法、原生入口和参考资料见 `PROTOCOL_REVISION.md`。敌方全局调度置为被动，机动单位由固定最近目标脚本自动下令；部署兵驻守。规则的战斗难度倍率统一为 1；无训练优化器运行，不更改受保护模型、奖励、动作/观测契约或桥接二进制。地形沿用已保存地图；原波次教学地图和人类示范档案保留。
'''
    (R/'交付说明.md').write_text(text,encoding='utf-8')
    protected={};state=s.load_state()
    for name,item in state['model_artifacts'].items():
        p=ROOT/item['path'];protected[name]=dict(path=item['path'],sha256=sha(p),expected=item['sha256']);assert protected[name]['sha256']==item['sha256']
    for name,path,expected in [('wave_map','maps/training/wave-defense-20261105/wave-defense-v1.map','45a67473278fdd5a12701d6ab401ac4f87abb197f1d6b88f72f32ebf01161bbb'),('small_arena','maps/training/multiskill-20261105/arena-v1.map','0b34e1c82b1770e9cdb01942dbac58b275e8da9373ba0810461316376a72d52d')]:
        protected[name]=dict(path=path,sha256=sha(ROOT/path),expected=expected);assert protected[name]['sha256']==expected
    dump(R/'PROTECTED_CHECK.json',dict(status='VERIFIED',files=protected))
    dump(visible/'VISUAL_CHECK.json',dict(status='VERIFIED',client_size=[1920,1080],both_units_in_camera=True,evidence='交战画面.png',manual_input_by_codex=False))
    code=R/'delivery-code';code.mkdir(exist_ok=False)
    for p in (ROOT/'scripts/unit_duel').glob('*.py'):shutil.copy2(p,code/p.name)
    shutil.copy2(ROOT/'开始单位自动对抗.cmd',code/'开始单位自动对抗.cmd')
    manifest=[]
    for run,recheck in selected:
        for p in run.rglob('*'):
            if p.is_file():manifest.append(dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size,sha256=sha(p)))
    for p in visible.rglob('*'):
        if p.is_file():manifest.append(dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size,sha256=sha(p)))
    dump(R/'DATA_MANIFEST.json',dict(status='VERIFIED',files=manifest))
    (ROOT/'_runs_duel').mkdir(exist_ok=True);dump(ROOT/'_runs_duel/latest.json',dict(batch=str(R),completed=16))
    note=ROOT/'coordination/notes/2026-11-06-unit-duel-random.md';note.write_text(text,encoding='utf-8')
    with s.Lock():
        st=s.load_state();base=st['context_version'];s.check_base_version(base);q=s.load_tasks();idx=s.load_artifacts()
        task=s.update_task(q,'TASK-20261105-028',status='COMPLETED',result_summary=f'VERIFIED random native unit combat launcher; 16 accepted fixtures, {events["damage"]} damage callbacks, {events["kill_credit"]} kill registrations; per-unit HP reconciled; visible 1080 verified. No model training.',outputs=[str(R.relative_to(ROOT)),'开始单位自动对抗.cmd','_runs_duel',str(note.relative_to(ROOT))])
        task['user_scope_additions']=['记录谁攻击谁、攻击来源、伤害及最终击败者','整理成随机兵种对抗并收集数据的选项']
        for p in [R/'ACCEPTED_SUMMARY.json',R/'DATA_MANIFEST.json',R/'交付说明.md',R/'本批对抗记录.html',R/'PROTOCOL_REVISION.md',R/'PROTECTED_CHECK.json',ROOT/'开始单位自动对抗.cmd',note,*code.glob('*')]:s.register_artifact(idx,p,role='unit_duel_verified_delivery',task_id=task['task_id'],context_version=base+1)
        s.save_tasks(q);s.save_artifacts(idx)
        for item in st.get('next_actions',[]):
            if item.get('id')==task['task_id']:item['status']='COMPLETED'
        st['status_detail']='UNIT_DUEL_RANDOM_COLLECTION_READY'
        st['unit_duel_result']={k:v for k,v in summary.items() if k!='runs'};st['unit_duel_result']['report']=str((R/'交付说明.md').relative_to(ROOT))
        st['current_training_progress']=dict(checked_at=s.now_iso(),owner='Codex',summary=['VERIFIED 16 native combat fixtures; random ground-unit collection launcher ready.','Fire, damage, projectile provenance and defeated-by are logged and reconciled.','No model training; all owned test games closed.'],report=str((R/'交付说明.md').relative_to(ROOT)))
        assert not s.validate_state(st)
        st=s.bump_context(st,'Completed native random unit combat collection and causal damage/kill validation',actor='Codex');s.regenerate_current_context(st,q)
    event=s.append_event('EXPERIMENT_COMPLETED','VERIFIED 随机单位自动对抗入口：16组通过，攻击、逐次伤害、弹丸来源与击败者可追溯；未更新模型。',actor='Codex',context_version=base+1,payload=dict(base_context_version=base,task_id=task['task_id'],summary=str((R/'ACCEPTED_SUMMARY.json').relative_to(ROOT)),manifest=str((R/'DATA_MANIFEST.json').relative_to(ROOT))))
    print(json.dumps(dict(context_version=base+1,event_id=event['event_id'],N=16,units=units,frames=frames,events=events),ensure_ascii=False))

if __name__=='__main__':main()
