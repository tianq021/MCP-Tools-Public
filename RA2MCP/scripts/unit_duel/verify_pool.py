"""Re-run the data gate over stored pool evidence and write POOL_VERIFICATION.json.

Bounded maintenance helper: it only reads archived duel evidence and rewrites
derived analysis files, so the whole pool can be re-scored by the current gate
version without re-running the game. No model, reward or bridge change.
"""
from pathlib import Path
import json,sys
# duel.py prepends scripts/wave_defense (which also has a launch.py) to sys.path,
# so keep this directory first and import the unit-duel launcher before duel.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from launch import VEHICLES,INFANTRY
from scripts.unit_duel import analyze as gate
NAMES=gate.NAMES
from duel import ROOT

REPORT=ROOT/'reports/unit-duel-pool-20261106'
# Kept on purpose by the 2026-11-05 delivery as an empty-projectile-source
# diagnostic; it is not part of the accepted fixture set.
KNOWN_DIAGNOSTIC_FAILURES={('legacy-v2','011')}
BATCHES=[('run-v2',REPORT/'run-v2'),('run-v3-candidates',REPORT/'run-v3-candidates'),('run-v5-extras',REPORT/'run-v5-extras'),('run-v6-random',REPORT/'run-v6-random'),('run-v7-random',REPORT/'run-v7-random'),('melee-v4',REPORT/'melee-validation-v4'),
         ('legacy-v2',ROOT/'reports/unit-duel-20261105/validation-v2'),('legacy-v3-tail',ROOT/'reports/unit-duel-20261105/validation-v3-tail'),('legacy-v4-tail',ROOT/'reports/unit-duel-20261105/validation-v4-tail')]

def main():
    pool=VEHICLES+INFANTRY
    units={code:dict(code=code,role='vehicle' if code in VEHICLES else 'infantry',name=NAMES.get(code,code),episodes=[]) for code in pool}
    rows=[];skipped=[];dest_root=REPORT/'reanalysis-gate-v4'
    for batch,base in BATCHES:
        if not base.exists():continue
        for run in sorted(p for p in base.iterdir() if p.is_dir()):
            if not (run/'RESULT.json').exists():continue
            if (batch,run.name) in KNOWN_DIAGNOSTIC_FAILURES:continue
            analysis=gate.analyze(run,dest_root/batch/run.name)
            case=analysis['case']
            if 'left_units' in case:side_codes=[[code for code,_ in case['left_units']],[code for code,_ in case['right_units']]]
            else:side_codes=[[case['left']],[case['right']]]
            if any(code not in units for side in side_codes for code in side):
                skipped.append(dict(batch=batch,run=run.name,name=case['name'],status=analysis['status'],reason='unit not in final pool'))
                continue
            totals={side:dict(shots=0,damage_dealt=0,damage_received=0,kills=0,regenerated_hp=0) for side in (0,1)}
            for unit in analysis['units'].values():
                row=totals[unit['side']]
                row['shots']+=unit['shots'];row['damage_dealt']+=unit['damage_dealt'];row['damage_received']+=unit['damage_received'];row['kills']+=len(unit['kills']);row['regenerated_hp']+=unit.get('hp_regenerated',0)
            row=dict(id=f'{batch}/{run.name}',batch=batch,run=run.name,case=case,status=analysis['status'],
                passed=analysis['status']=='VERIFIED',failed=[k for k,v in analysis['checks'].items() if not v],
                environmental_damage=analysis['environmental_damage'],crushing_or_removal_hp=analysis['crushing_or_removal_hp'],
                weapon_slots=analysis['weapon_slots'],totals=totals)
            rows.append(row)
            for side,codes in enumerate(side_codes):
                for code in codes:
                    units[code]['episodes'].append(dict(id=row['id'],role='left' if side==0 else 'right',status=analysis['status'],name=case['name']))
    verified=all(r['passed'] for r in rows) and bool(rows)
    each={code:any(e['status']=='VERIFIED' for e in info['episodes']) for code,info in units.items()}
    summary=dict(status='VERIFIED' if verified and all(each.values()) else 'INCOMPLETE',
        gate='v4: 14 checks including per-frame native HP steps; regeneration, crushing and unattributed death collateral measured separately; deployment weapon slot asserted only for deployable defenders',
        episodes=len(rows),episodes_verified=sum(1 for r in rows if r['passed']),rows=rows,skipped_episodes=skipped,units=units,units_with_verified_episode=[c for c,v in each.items() if v],units_without_episode=[c for c,v in each.items() if not v],
        limits=['每兵种 1 局定向检查 + 少量随机/混战局；不是胜率或兵种强弱排名','原生死亡附带伤害、碾压、精英自愈等已单独计数，不是逐次武器伤害','双方造价与数量不对等；固定地图与出生点；自由运行时钟非逐位确定','未训练或更新任何模型；未改动奖励、动作/观测契约与桥接二进制'])
    (REPORT/'POOL_VERIFICATION.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=summary['status'],episodes=summary['episodes'],verified=summary['episodes_verified'],skipped=len(skipped),units_without_episode=summary['units_without_episode'],failed=[r['id'] for r in rows if not r['passed']]),ensure_ascii=False))

if __name__=='__main__':main()
