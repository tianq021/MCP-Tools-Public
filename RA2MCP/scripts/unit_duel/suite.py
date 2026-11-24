from pathlib import Path
import json,sys,time
from duel import CASES,REPORT,build,play,dump

def main():
    root=REPORT/'suite-v1';root.mkdir(exist_ok=False);rows=[]
    for seed in (2026110528,2026110529):
        for swap in (False,True):
            for case in CASES:
                out=root/f'case{case["id"]}-seed{seed}-swap{int(swap)}'
                start=time.monotonic();plan=build(out,dict(case),seed,swap,False);r=play(out,plan)
                row=dict(run=str(out.relative_to(REPORT)),status=r['status'],case=case['id'],seed=seed,swap=swap,winner=r['winner'],remaining_hp=[sum(o['hp'] for o in g) for g in r['remaining']],seconds=round(time.monotonic()-start,1))
                rows.append(row);dump(root/'PROGRESS.json',dict(completed=len(rows),planned=16,rows=rows))
                print(json.dumps(row,ensure_ascii=False),flush=True)
                if r['status']!='VERIFIED':raise RuntimeError('Verification failed; stopped before remaining fixtures')
    dump(root/'SUMMARY.json',dict(status='VERIFIED',runs=rows,N=len(rows),training_performed=False))

if __name__=='__main__':main()
