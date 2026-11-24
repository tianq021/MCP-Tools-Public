"""User-facing launcher: last saved terrain + chosen curriculum + live observer."""
from pathlib import Path
import sys,time
from classroom import ROOT,SOURCE,roster,preflight,build
from record import play

def main():
    rows,_=roster()
    print('波次防守教学：每波 60 个敌军，100 万开局资金。')
    print('我方：盟军（法国），一辆基地车；电脑基地车位于隔离区。')
    print('请先在地图编辑器保存。这里会读取最后保存的版本：\n'+str(SOURCE))
    print('开局准备 5 分钟，之后每 4 分钟一波，提前 1 分钟预警。')
    print('默认第 5 档速度，60 FPS 目标；以上均为游戏倒计时。\n')
    for i in range(0,len(rows),3):
        print('  |  '.join(f'{j+1:02} {rows[j]["id"]}: {rows[j]["name"]}' for j in range(i,min(i+3,len(rows)))))
    print('\n输入起始波编号：从该波继续后续课程；直接回车从第 1 波开始。')
    print('也可输入兵种代码单练，例如 HTNK；用英文逗号排列多种兵种。')
    answer=input('起始波 / 单练代码：').strip().upper() or '1'
    if answer.isdigit():
        start=int(answer)
        if not 1<=start<=len(rows):raise ValueError('起始波编号超出范围')
        units=[r['id'] for r in rows[start-1:]]
    else:
        units=[x.strip() for x in answer.split(',') if x.strip()]
        if not units or any(x not in {r['id'] for r in rows} for x in units):raise ValueError('兵种代码无效，请按上表输入')
    preflight();out=ROOT/'_runs_wave'/time.strftime('%Y%m%d-%H%M%S');build(out,units)
    print('\n正在启动正常画面。由你操作；记录器只读游戏状态，不代替你下令。')
    print('记录位置：'+str(out));print('退出时请从游戏菜单结束对局。')
    play(out)
    print('本局已结束，观测记录已保存。')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('无法开始：'+str(exc));sys.exit(1)
