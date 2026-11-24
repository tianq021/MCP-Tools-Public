"""Archive every native unit-duel data artifact into one self-describing dataset.

Read-only for the sources: it copies raw evidence (reports/unit-duel*, _runs_duel)
into a dataset directory, then writes an episode index, consolidated combat-event
logs, a SHA256 manifest and a README. No model, reward or bridge change.
"""
from pathlib import Path
import argparse,collections,csv,gzip,hashlib,json,shutil,sys,time
ROOT=Path(__file__).resolve().parents[2]
SOURCES=[ROOT/'reports/unit-duel-20261105',ROOT/'reports/unit-duel-display-20261106',ROOT/'reports/unit-duel-pool-20261106',ROOT/'_runs_duel']
DEFAULT_DEST=ROOT/'datasets/unit-duel-20261106'

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):h.update(block)
    return h.hexdigest()

def raw_files(base):
    for p in sorted(base.rglob('*')):
        if p.is_file():yield p

def episode_dirs(base):
    for p in sorted(base.rglob('RESULT.json')):
        yield p.parent

def load(path,default=None):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def episode_record(run,raw_root):
    result=load(run/'RESULT.json',{}) or {}
    analysis=load(run/'COMBAT_ANALYSIS.json',{}) or {}
    plan=load(run/'PLAN.json',{}) or {}
    units=analysis.get('units',{}) or {}
    totals={f'side{side}':dict(shots=sum(u.get('shots',0) for u in units.values() if u.get('side')==side),damage_dealt=sum(u.get('damage_dealt',0) for u in units.values() if u.get('side')==side),damage_received=sum(u.get('damage_received',0) for u in units.values() if u.get('side')==side),kills=sum(len(u.get('kills',[])) for u in units.values() if u.get('side')==side)) for side in (0,1)}
    case=result.get('case',{}) or {}
    rel=str(run.relative_to(raw_root)).replace('\\','/')
    return dict(id=rel,
        source=rel.split('/')[0],
        name=case.get('name'),
        mode='melee' if 'left_units' in case else 'single',
        seed=result.get('seed',plan.get('seed')),
        swap=result.get('swap',plan.get('swap')),
        winner=result.get('winner'),
        status=analysis.get('status') or result.get('status') or 'UNKNOWN',
        failed_checks=[k for k,v in (analysis.get('checks') or {}).items() if not v],
        end_frame=result.get('end_frame'),spawn_frame=result.get('spawn_frame'),
        peak_counts=result.get('peak_counts'),full_health_start=result.get('full_health_start'),
        case=case,
        units={str(address):dict(side=u.get('side'),type=u.get('type'),initial_hp=u.get('initial_hp'),final_hp=u.get('final_hp'),observed_final_hp=u.get('observed_final_hp'),damage_received=u.get('damage_received'),damage_dealt=u.get('damage_dealt'),shots=u.get('shots'),kills=u.get('kills',[]),defeated_by=bool(u.get('defeated_by')),hp_regenerated=u.get('hp_regenerated'),environmental_damage=u.get('environmental_damage')) for address,u in units.items()},
        event_counts=analysis.get('event_counts'),totals=totals,
        environmental_damage=analysis.get('environmental_damage'),crushing_or_removal_hp=analysis.get('crushing_or_removal_hp'),
        removed_without_killer=analysis.get('removed_without_killer'),weapon_slots=analysis.get('weapon_slots'),
        map_sha256=plan.get('map_sha256'),game_exe_sha256=plan.get('game_exe_sha256'),source_code_sha256=plan.get('source_code_sha256'))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dest',type=Path,default=DEFAULT_DEST);ap.add_argument('--force',action='store_true');ap.add_argument('--no-copy',action='store_true');args=ap.parse_args()
    dest=args.dest
    if dest.exists():
        if not args.force:raise SystemExit(f'目标已存在：{dest}（加 --force 覆盖）')
        shutil.rmtree(dest)
    raw=dest/'raw';events_dir=dest/'combat-events';raw.mkdir(parents=True);events_dir.mkdir()
    sources=[p for p in SOURCES if p.exists()]
    copied=[];episodes=[]
    for source in sources:
        label=source.name
        target=raw/label
        if args.no_copy:target=source
        else:shutil.copytree(source,target)
        copied.append(dict(name=label,path=str(source.relative_to(ROOT)).replace('\\','/'),files=sum(1 for _ in raw_files(source))))
        for run in episode_dirs(target):
            record=episode_record(run,target);record['source_root']=str(target.relative_to(dest)).replace('\\','/')
            events=run/'COMBAT_EVENTS.jsonl.gz'
            if events.exists():
                name=record['id'].replace('/','__')+'.jsonl.gz'
                shutil.copy2(events,events_dir/name);record['combat_events']='combat-events/'+name
            episodes.append(record)
    index=dest/'EPISODES.jsonl'
    with index.open('w',encoding='utf-8') as f:
        for record in episodes:f.write(json.dumps(record,ensure_ascii=False)+'\n')
    fields=['id','source','name','mode','seed','swap','winner','status','end_frame','peak_counts','environmental_damage','crushing_or_removal_hp','combat_events']
    with (dest/'EPISODES.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for record in episodes:writer.writerow(record)
    by_status=collections.Counter(r['status'] for r in episodes);by_mode=collections.Counter(r['mode'] for r in episodes)
    native_frames=0;event_counts=collections.Counter()
    for record in episodes:
        native_frames+=int((load(Path(dest)/record['source_root']/record['id']/'NATIVE_COVERAGE.json',{}) or {}).get('records',0) or 0)
        for key,value in (record.get('event_counts') or {}).items():event_counts[key]+=int(value)
    manifest=[]
    for p in sorted(dest.rglob('*')):
        if p.is_file() and p.name!='MANIFEST.json':
            manifest.append(dict(path=str(p.relative_to(dest)).replace('\\','/'),bytes=p.stat().st_size,sha256=sha(p)))
    summary=dict(dataset=dest.name,created_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),root=str(dest.relative_to(ROOT)).replace('\\','/'),
        sources=copied,episodes=len(episodes),episodes_by_status=dict(by_status),episodes_by_mode=dict(by_mode),native_frames=native_frames,event_counts=dict(event_counts),
        files=len(manifest),bytes=sum(m['bytes'] for m in manifest),
        notes=['原始证据按来源目录原样复制，不改写、不删减；失败/未通过局也保留。','EPISODES.jsonl 是逐局索引；combat-events/ 是逐局开火/伤害/击杀事件（原生回调记录）。','本数据集是数据采集与验收证据，不是模型训练结果；未训练/未更新模型。','单兵种局与混战局都按同一门禁 v4 评分；status 字段来自各局 COMBAT_ANALYSIS.json。'])
    (dest/'MANIFEST.json').write_text(json.dumps(dict(summary=summary,files=manifest),ensure_ascii=False,indent=1),encoding='utf-8')
    with (dest/'SHA256SUMS.txt').open('w',encoding='utf-8') as f:
        for item in manifest:f.write(f"{item['sha256']}  {item['path']}\n")
    readme=f"""# 单位自动对抗数据集（{dest.name}）

创建时间：{summary['created_at']}；来源：{', '.join(c['name'] for c in copied)}

## 概况

- 局数：**{summary['episodes']}**（{', '.join(f'{k}: {v}' for k,v in by_status.items())}）
- 模式：{', '.join(f'{k}: {v}' for k,v in by_mode.items())}
- 原生逐帧状态记录：{native_frames} 帧；事件：{', '.join(f'{k}: {v}' for k,v in event_counts.items())}
- 文件：{summary['files']} 个 / {summary['bytes']/1048576:.1f} MB

## 目录结构

```
raw/                      原始证据原样复制（按来源目录）
  unit-duel-20261105/     首批交付：6 兵种、16 局验收 + 探针/显示证据
  unit-duel-display-20261106/  速度与 27px 字体验证
  unit-duel-pool-20261106/     兵种池扩展：逐兵种验证、门禁 v4 重评分、混战验证
  _runs_duel/             平时双击入口跑出的原始批次
combat-events/            逐局开火/伤害/击杀事件（.jsonl.gz）
EPISODES.jsonl            逐局索引（配置、种子、胜负、检查项、逐单位统计）
EPISODES.csv              同上，表格版
MANIFEST.json             全部文件的大小与 SHA256 + 数据集概况
SHA256SUMS.txt            校验清单
```

单局原始证据在 `raw/<来源>/<批次>/<局号>/`，通常含：`PLAN.json`（配置/种子/地图与代码哈希）、`RESULT.json`（胜负与验收状态）、`COMBAT_ANALYSIS.json`（逐单位伤害/击杀/检查项）、`COMBAT_EVENTS.jsonl.gz`（逐次回调事件）、`NATIVE_STATES*.pb.gz`（逐帧原生状态，可用于重建血量轨迹）、`战斗明细.html`（人类可读明细）、`TRACE.jsonl.gz`（观察器采样）。

## 用途与限制

- 可用于单位关系/战斗数据研究：逐次开火、弹丸来源链、逐次伤害、原生击杀归属、逐帧血量都可追溯。
- 这是数据采集与验收证据，**不是模型训练结果**，也不代表任何胜率或兵种强弱排名。
- 失败/未通过/诊断局保留在原始目录中，指数索引里 status 会标出；不得把它们当有效样本。
- 未训练/未更新模型；未改动奖励、动作/观测契约、桥接二进制。

## 后续批次如何再归档

新批次数据会写到 `_runs_duel/日期时间/`。重新归档（生成新数据集目录）：

```
python scripts/unit_duel/archive_dataset.py --dest datasets/unit-duel-YYYYMMDD --force
```
"""
    (dest/'README.md').write_text(readme,encoding='utf-8')
    print(json.dumps(dict(dataset=summary['root'],episodes=summary['episodes'],by_status=summary['episodes_by_status'],by_mode=summary['episodes_by_mode'],files=summary['files'],MB=round(summary['bytes']/1048576,1)),ensure_ascii=False))

if __name__=='__main__':main()
