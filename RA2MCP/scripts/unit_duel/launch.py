"""Chinese launcher for bounded randomized native combat data collection."""
from pathlib import Path
import argparse,json,os,random,sys,time
from duel import ROOT,REPORT,CASES,build,play,dump
from scripts.unit_duel.analyze import NAMES,DEPLOYABLE
VEHICLES=['MTNK','HTNK','LTNK','TTNK','MGTK','APOC','TNKD','FV','HTK','YTNK','HOWI','BFRT','DRON','ZEP','DISK','SREF','SHAD','HIND','DTRUCK']
INFANTRY=['E1','E2','GGI','FLAKT','SHK','JUMPJET','SNIPE','VIRUS','INIT','BORIS','BRUTE','TANY','GHOST','ADOG','LUNR','IVAN','CIVAN','CLEG']
OPPONENTS=VEHICLES+INFANTRY

def measure(code):return '辆' if code in VEHICLES else '个'

def draw_cases(seed,episodes):
    rng=random.Random(seed);cases=[]
    for i in range(episodes):
        left=rng.choice(VEHICLES);right=rng.choice(OPPONENTS)
        ln=rng.randint(1,3);rn=rng.randint(1,4)
        deployed=right in DEPLOYABLE and (right=='GGI' or bool(rng.getrandbits(1)))
        name=f'{ln} 辆{NAMES[left]} vs {rn} {measure(right)}'+('部署' if deployed else '')+NAMES[right]
        cases.append(dict(case=dict(id=f'random-{i+1}',left=left,right=right,left_n=ln,right_n=rn,deployed=deployed,name=name),seed=rng.randrange(1,2147483647),swap=bool(rng.getrandbits(1))))
    return cases

def draw_melee(seed,episodes):
    """Free-for-all style mixed compositions: 2-3 types and 2-5 units per side."""
    rng=random.Random(seed);cases=[]
    for i in range(episodes):
        def side():
            units=[[code,rng.randint(1,2)] for code in rng.sample(OPPONENTS,rng.choice((2,2,3)))]
            while sum(n for _,n in units)>5:
                j=rng.randrange(len(units))
                if units[j][1]>1:units[j][1]-=1
                else:units.pop(j)
                if not units:units=[[rng.choice(OPPONENTS),1]]
            return units
        left=side();right=side()
        def label(units):return ' + '.join(f'{n} {measure(code)}{NAMES[code]}' for code,n in units)
        name=f'混战：{label(left)} vs {label(right)}'
        cases.append(dict(case=dict(id=f'melee-{i+1}',left_units=left,right_units=right,deployed=False,name=name),seed=rng.randrange(1,2147483647),swap=bool(rng.getrandbits(1))))
    return cases

def batch(out,cases,visible,seed):
    out.mkdir(parents=True,exist_ok=False);dump(out/'BATCH_PLAN.json',dict(seed=seed,cases=cases,visible=visible,pool=dict(vehicles=VEHICLES,infantry=INFANTRY,deployable=list(DEPLOYABLE)),method='native engine; automatic target assignment; event-level provenance',melee=any('left_units' in spec['case'] for spec in cases),replay='Seeds, map/config hashes and exact issued order frames retained. Free-running bridge timing can vary.'))
    rows=[];complete=False
    try:
        for i,spec in enumerate(cases):
            run=out/f'{i+1:03d}';case=spec['case'];print(f'[{i+1}/{len(cases)}] {case["name"]}',flush=True)
            plan=build(run,case,spec['seed'],spec['swap'],visible);r=play(run,plan)
            row=dict(run=str(run),name=case['name'],status=r['status'],winner=r['winner'],remaining_hp=[sum(o['hp'] for o in g) for g in r['remaining']])
            rows.append(row);dump(out/'BATCH_STATUS.json',dict(completed=len(rows),planned=len(cases),rows=rows,running=True))
            print(json.dumps(row,ensure_ascii=False),flush=True)
            if r['status']!='VERIFIED':print('本局未通过完整数据校验，已保留证据并停止后续开局。',flush=True);break
        complete=len(rows)==len(cases) and all(r['status']=='VERIFIED' for r in rows)
    finally:
        dump(out/'BATCH_STATUS.json',dict(status='VERIFIED' if complete else 'INCOMPLETE',completed=len(rows),planned=len(cases),rows=rows,running=False))
        root=ROOT/'_runs_duel';root.mkdir(exist_ok=True);dump(root/'latest.json',dict(batch=str(out),completed=len(rows)))
        import html
        links=''.join(f'<li>{html.escape(r["name"])} — {r["status"]} — <a href="{Path(r["run"]).name}/战斗明细.html">攻击、伤害和击败者</a></li>' for r in rows)
        (out/'本批对抗记录.html').write_text('<!doctype html><meta charset="utf-8"><title>随机兵种对抗</title><style>body{font:18px sans-serif;max-width:1100px;margin:40px auto;background:#f1f4f7}li{margin:14px}</style><h1>随机兵种自动对抗记录</h1><p>每一局保留配置、随机种子、逐帧状态、开火目标、伤害来源和原生击杀归属。仅采集数据，未更新模型。</p><ol>'+links+'</ol>',encoding='utf-8')
    return complete

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--validation',action='store_true');ap.add_argument('--melee-validation',action='store_true');ap.add_argument('--episodes',type=int);ap.add_argument('--out',type=Path);args=ap.parse_args()
    if args.melee_validation:
        cases=draw_melee(2026110702,args.episodes or 4)
        if not batch(args.out,cases,False,2026110702):sys.exit(1)
        return
    if args.validation:
        cases=[dict(case=dict(c),seed=2026110528,swap=s) for s in (False,True) for c in CASES]+draw_cases(2026110601,8)
        if not batch(args.out,cases,False,2026110601):sys.exit(1)
        return
    print('单位自动对抗：原版引擎出兵、自动交战、自动收集数据。默认第 5 档（60 FPS）。\n')
    print('1. 随机兵种自动对抗并采集数据（推荐）\n2. 观看指定基础对打\n3. 打开最近采集的数据\n4. 混战：双方混编多种兵种自动对战并采集数据')
    option=input('选择 [默认 1]：').strip() or '1'
    if option=='3':
        latest=json.loads((ROOT/'_runs_duel/latest.json').read_text());os.startfile(str(Path(latest['batch'])/'本批对抗记录.html'));return
    if option=='1':
        print('载具池（左方 1–3 辆；右方也可能抽到）：'+'、'.join(NAMES[c] for c in VEHICLES)+'。')
        print('步兵池（右方 1–4 个；美国大兵/重装大兵随机部署或站立）：'+'、'.join(NAMES[c] for c in INFANTRY)+'。')
        print('每局随机：一方载具，另一方从载具池或步兵池抽取；随机出生方向和种子。')
        episodes=int(input('本批局数 [默认 10，最多 100]：').strip() or '10')
        if not 1<=episodes<=100:raise ValueError('局数必须为 1–100')
        value=input('随机种子 [回车自动生成]：').strip();seed=int(value) if value else random.SystemRandom().randrange(2147483647)
        cases=draw_cases(seed,episodes)
    elif option=='2':
        for c in CASES:print(f'{c["id"]}. {c["name"]}')
        n=int(input('选择 [默认 1]：').strip() or '1')
        if not 1<=n<=4:raise ValueError('编号必须为 1–4')
        seed=2026110528;cases=[dict(case=dict(CASES[n-1]),seed=seed,swap=False)]
    elif option=='4':
        print('混战：双方各自随机 2–3 种兵种、每侧合计 2–5 个单位，原生引擎自动交战并采集数据；不做部署。')
        episodes=int(input('本批局数 [默认 10，最多 100]：').strip() or '10')
        if not 1<=episodes<=100:raise ValueError('局数必须为 1–100')
        value=input('随机种子 [回车自动生成]：').strip();seed=int(value) if value else random.SystemRandom().randrange(2147483647)
        cases=draw_melee(seed,episodes)
    else:raise ValueError('选项无效')
    visible=(input('1 可见观看 / 2 后台采集 [默认 1]：').strip() or '1')!='2'
    out=ROOT/'_runs_duel'/time.strftime('%Y%m%d-%H%M%S')
    print('无需操作游戏。每局结果保存后自动关闭并开下一局；可见模式额外停留 8 秒。')
    print('请保持此命令窗口打开；Ctrl+C 停止当前批次。\n数据位置：'+str(out),flush=True)
    ok=batch(out,cases,visible,seed)
    print('采集完成。' if ok else '采集已停止，请查看未通过项目。')
    os.startfile(str(out/'本批对抗记录.html'))

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('已停止；已完成对局和当前可保存记录保留。')
    except Exception as exc:print('无法继续：'+str(exc));sys.exit(1)
