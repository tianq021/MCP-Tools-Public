r"""胶东DSH 防区值班：轮询回合，按保守规则操作本防区单位。

规则（只做低风险的攻防，不做长距离调动、不结束回合）：
  1. 弓箭手：射程 2 内若有敌方单位 -> 打残血最低的那个（远程不吃反击）。
  2. 勇士：相邻敌方单位 HP<=60 且自身移动力>0 -> 补刀。
  3. 其余情况：原地待命（已被用户指定为屏幕位置，不乱走）。
  4. 每回合只执行一次；回合未推进则继续等待。

用法: python 脚本\sector_dsh.py [minutes]
日志: E:\\Civ6\\logs\\sector_log.txt
"""
import re
import sys
import time

from civc import call

CITY_X, CITY_Y = 14, 23
WAR_PLAYERS = {2, 4}
LOG = r'E:\Civ6\logs\sector_log.txt'
LUA = r'E:\Civ6\lua\sector_scan.lua'
SLEEP = 10


def log(msg):
    line = time.strftime('[%H:%M:%S] ') + msg
    with open(LOG, 'a', encoding='utf-8') as fh:
        fh.write(line + '\n')
    print(line, flush=True)


def tl(name, **args):
    text, err = call(name, args or None)
    return text, err


def hexdist(x1, y1, x2, y2):
    u1, v1 = x1 - y1 // 2, y1
    u2, v2 = x2 - y2 // 2, y2
    du, dv = u2 - u1, v2 - v1
    return (abs(du) + abs(dv) + abs(du + dv)) // 2


def turn_now():
    text, _ = tl('get_game_overview')
    m = re.search(r'Turn (\d+)', text)
    return int(m.group(1)) if m else -1


def scan():
    code = open(LUA, encoding='utf-8').read()
    text, _ = tl('run_lua', code=code, context='ingame')
    city = None
    mine, foreign = [], []
    for line in text.splitlines():
        p = line.split('|')
        if p[0] == 'CITY':
            city = {'hp': int(p[1]), 'max': int(p[2]), 'def': int(p[3]), 'found': p[4] == 'yes'}
        elif p[0] == 'MINE':
            mine.append({'id': int(p[1]), 'type': p[2], 'x': int(p[3]), 'y': int(p[4]),
                         'moves': int(p[5]), 'maxmoves': int(p[6]), 'hp': int(p[7]), 'maxhp': int(p[8])})
        elif p[0] == 'FOREIGN':
            foreign.append({'owner': int(p[1]), 'type': p[2], 'x': int(p[3]), 'y': int(p[4]),
                            'hp': int(p[5]), 'maxhp': int(p[6])})
    enemies = [u for u in foreign if u['owner'] in WAR_PLAYERS]
    return city, mine, enemies


def act(turn):
    city, mine, enemies = scan()
    if not city or not city['found']:
        log(f'T{turn} 找不到胶东DSH，暂停')
        return
    real = [u for u in mine if u['type'].startswith('UNIT_') and any(
        k in u['type'] for k in ('ARCHER', 'SLINGER', 'WARRIOR', 'SPEARMAN', 'BLOOD_GUARD'))]
    log(f'T{turn} 城 HP={city["hp"]}/{city["max"]} Def={city["def"]} | 我方 {len(real)} 兵 | 敌 {len(enemies)} 兵')

    archers = [u for u in real if 'ARCHER' in u['type'] or 'SLINGER' in u['type']]
    melee = [u for u in real if 'WARRIOR' in u['type'] or 'SPEARMAN' in u['type'] or 'BLOOD' in u['type']]

    acted = False
    for a in archers:
        if a['moves'] <= 0:
            continue
        inrange = [e for e in enemies if hexdist(a['x'], a['y'], e['x'], e['y']) <= 2]
        if not inrange:
            continue
        target = min(inrange, key=lambda e: e['hp'])
        text, _ = tl('unit_action', unit_id=a['id'], action='attack',
                     target_x=target['x'], target_y=target['y'])
        log(f'  弓手{a["id"]} -> 打 {target["type"]}@{target["x"]},{target["y"]} ({target["hp"]}hp): '
            + ' '.join(text.split())[:160])
        acted = True

    for m in melee:
        if m['moves'] <= 0:
            continue
        adj = [e for e in enemies if hexdist(m['x'], m['y'], e['x'], e['y']) <= 1 and e['hp'] <= 60]
        if not adj:
            continue
        target = min(adj, key=lambda e: e['hp'])
        text, _ = tl('unit_action', unit_id=m['id'], action='attack',
                     target_x=target['x'], target_y=target['y'])
        log(f'  勇士{m["id"]} -> 补刀 {target["type"]}@{target["x"]},{target["y"]} ({target["hp"]}hp): '
            + ' '.join(text.split())[:160])
        acted = True

    if not acted:
        log('  无需动作（按兵不动）')


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    deadline = time.time() + minutes * 60
    last = -1
    log('==== 胶东DSH 值班启动 ====')
    while time.time() < deadline:
        try:
            t = turn_now()
            if t != last and t > 0:
                last = t
                act(t)
        except Exception as exc:  # noqa: BLE001
            log('异常: ' + str(exc)[:200])
        time.sleep(SLEEP)
    log('==== 值班结束 ====')


if __name__ == '__main__':
    main()
