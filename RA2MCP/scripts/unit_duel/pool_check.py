"""Bounded check for the random duel pool: one episode per unit type.

Every fixture reuses the verified native duel pipeline (duel.build/duel.play).
Each pool unit fights on the right side against a fixed Grizzly pair, so the
existing data gate also checks the requested weapon slot. A fixture passes when
the full data gate is VERIFIED; whether the unit actually dealt damage is
reported separately (some infantry can barely hurt heavy armor by design).
INCOMPLETE evidence is kept for diagnosis instead of being deleted.
"""
from pathlib import Path
import argparse,json,sys,time
# duel.py prepends scripts/wave_defense (which also has a launch.py) to sys.path,
# so keep this directory first and import the unit-duel launcher before duel.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from launch import draw_cases,VEHICLES,INFANTRY
from duel import ROOT,build,play,dump
from scripts.unit_duel.analyze import NAMES

OUT=ROOT/'reports/unit-duel-pool-20261106'

def fixtures():
    specs=[]
    for i,code in enumerate(VEHICLES):
        specs.append(dict(id=f'veh-{code}',case=dict(id=f'veh-{code}',left='MTNK',right=code,left_n=2,right_n=2,deployed=False,name=f'2 辆灰熊坦克 vs 2 辆{NAMES[code]}'),seed=2026110602+i,swap=False))
    for i,code in enumerate(INFANTRY):
        specs.append(dict(id=f'inf-{code}',case=dict(id=f'inf-{code}',left='MTNK',right=code,left_n=2,right_n=3,deployed=False,name=f'2 辆灰熊坦克 vs 3 个{NAMES[code]}'),seed=2026110650+i,swap=False))
    for i,spec in enumerate(draw_cases(2026110630,3)):
        spec=dict(spec);spec['id']=f'random-{i+1}';spec['case']=dict(spec['case']);spec['case']['id']=spec['id'];specs.append(spec)
    return specs

def side_totals(analysis,side):
    units=[u for u in analysis['units'].values() if u['side']==side]
    return dict(units=len(units),shots=sum(u['shots'] for u in units),damage_dealt=sum(u['damage_dealt'] for u in units),damage_received=sum(u['damage_received'] for u in units),kills=sum(len(u['kills']) for u in units),final_hp=sum(u['final_hp'] for u in units),regenerated_hp=sum(u.get('hp_regenerated',0) for u in units))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path);ap.add_argument('--only');ap.add_argument('--visible',action='store_true');args=ap.parse_args()
    specs=fixtures()
    if args.only:
        keep=set(x.strip() for x in args.only.split(',') if x.strip())
        specs=[s for s in specs if s['id'] in keep or s['case']['left'] in keep or s['case']['right'] in keep]
    if not specs:raise SystemExit('没有匹配的检查项')
    out=args.out or (OUT if not args.only else OUT/'candidate-check')
    out.mkdir(parents=True,exist_ok=False)
    rows=[];t0=time.monotonic();ok=False
    try:
        for i,spec in enumerate(specs):
            case=spec['case'];run=out/spec['id'];started=time.monotonic()
            print(f'[{i+1}/{len(specs)}] {case["name"]}',flush=True)
            try:
                plan=build(run,case,spec['seed'],spec['swap'],args.visible);r=play(run,plan)
                analysis=json.loads((run/'COMBAT_ANALYSIS.json').read_text(encoding='utf-8'))
                left=side_totals(analysis,0);right=side_totals(analysis,1)
                row=dict(id=spec['id'],name=case['name'],run=str(run.relative_to(ROOT)),status=r['status'],passed=r['status']=='VERIFIED',
                    effective=right['damage_dealt']>0,winner=r['winner'],left=left,right=right,checks=analysis['checks'],elapsed=round(time.monotonic()-started,1))
            except Exception as exc:
                row=dict(id=spec['id'],name=case['name'],run=str(run.relative_to(ROOT)),status='ERROR',passed=False,effective=False,error=repr(exc),elapsed=round(time.monotonic()-started,1))
            rows.append(row);dump(out/'POOL_CHECK_STATUS.json',dict(completed=len(rows),planned=len(specs),rows=rows,running=True))
            print(json.dumps(dict(id=row['id'],status=row['status'],passed=row['passed'],effective=row['effective'],winner=row.get('winner'),right_shots=row.get('right',{}).get('shots'),right_dmg=row.get('right',{}).get('damage_dealt'),failed=[k for k,v in row.get('checks',{}).items() if not v]),ensure_ascii=False),flush=True)
        ok=all(r['passed'] for r in rows)
    finally:
        dump(out/'POOL_CHECK_STATUS.json',dict(status='VERIFIED' if ok else 'INCOMPLETE',completed=len(rows),planned=len(specs),rows=rows,running=False,elapsed=round(time.monotonic()-t0,1)))
    lines=['# 单位对抗兵种池：逐兵种实机检查','','本检查只扩展随机兵种池并验证新兵种的实机数据完整性；没有更新模型、奖励或桥接。','']
    for r in rows:
        if 'right' in r:
            lines.append(f"- {r['name']} — {r['status']} — {'通过' if r['passed'] else '未通过'} — 右侧开火 {r['right']['shots']} 次、造成 {r['right']['damage_dealt']} 伤害、受击 {r['right']['damage_received']}、自愈 {r['right']['regenerated_hp']}、击杀 {r['right']['kills']}；左方剩余 HP {r['left']['final_hp']}，右方剩余 HP {r['right']['final_hp']}；{r['elapsed']}s")
        else:
            lines.append(f"- {r['name']} — {r['status']} — 未通过 — {r.get('error','')}；{r['elapsed']}s")
    (out/'POOL_CHECK.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('POOL_CHECK ' + ('VERIFIED' if ok else 'INCOMPLETE'),flush=True)
    if not ok:sys.exit(1)

if __name__=='__main__':main()
