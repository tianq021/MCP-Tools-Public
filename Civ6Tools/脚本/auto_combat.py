r"""全权战斗值班：每回合操作系统内所有战斗单位，并结束回合。

分工（用户 2026-10-27 明确）：战斗单位与回合结束权归 DSH；生产/建造归用户。
策略：守城优先、远程白嫖输出、近战只在有利时补刀、不追残兵进敌阵。
用法: python 脚本\auto_combat.py [minutes]
日志: E:\\Civ6\\logs\\combat_log.txt
"""
import re
import sys
import time

from civc import call

LOG = r'E:\Civ6\logs\combat_log.txt'
LUA_SCAN = r'E:\Civ6\lua\battle_scan.lua'
SLEEP = 8
RANGED = ('ARCHER', 'SLINGER', 'CROSSBOW', 'BALLISTA')
MELEE = ('WARRIOR', 'SPEARMAN', 'SWORDSMAN', 'BLOOD_GUARD', 'MAN_AT_ARMS', 'PIKEMAN', 'MUSKETMAN')
CIVIL = ('BUILDER', 'SETTLER', 'TRADER', 'SCOUT', 'SPY')


def log(msg):
    line = time.strftime('[%H:%M:%S] ') + msg
    with open(LOG, 'a', encoding='utf-8') as fh:
        fh.write(line + '\n')
    print(line, flush=True)


def tl(name, **args):
    try:
        text, err = call(name, args or None)
        return text, err
    except Exception as exc:  # noqa: BLE001
        return f'TRANSPORT:{exc}', True


def hexdist(x1, y1, x2, y2):
    u1, v1 = x1 - y1 // 2, y1
    u2, v2 = x2 - y2 // 2, y2
    du, dv = u2 - u1, v2 - v1
    return (abs(du) + abs(dv) + abs(du + dv)) // 2


def war_players():
    text, _ = tl('get_diplomacy')
    out = set()
    for line in text.splitlines():
        if 'AT WAR' in line:
            m = re.search(r'\[player (\d+)\]', line)
            if m:
                out.add(int(m.group(1)))
    return out


def scan():
    code = open(LUA_SCAN, encoding='utf-8').read()
    text, _ = tl('run_lua', code=code, context='ingame')
    cities, mine, foreign = [], [], []
    for line in text.splitlines():
        p = line.split('|')
        if p[0] == 'CITY':
            cities.append({'id': int(p[1]), 'x': int(p[2]), 'y': int(p[3]),
                           'hp': int(p[4]), 'max': int(p[5]), 'def': int(p[6]), 'gar': p[7] == '1'})
        elif p[0] == 'MINE':
            mine.append({'id': int(p[1]), 'type': p[2], 'x': int(p[3]), 'y': int(p[4]),
                         'moves': int(p[5]), 'maxmoves': int(p[6]), 'hp': int(p[7]), 'maxhp': int(p[8])})
        elif p[0] == 'FOREIGN':
            foreign.append({'owner': int(p[1]), 'type': p[2], 'x': int(p[3]), 'y': int(p[4]),
                            'hp': int(p[5]), 'maxhp': int(p[6])})
    return cities, mine, foreign


def kind(utype):
    for k in RANGED:
        if k in utype:
            return 'ranged'
    for k in MELEE:
        if k in utype:
            return 'melee'
    for k in CIVIL:
        if k in utype:
            return 'civil'
    return 'other'


def do_combat(turn, cities, mine, enemies):
    acted = []
    combat = [u for u in mine if kind(u['type']) in ('ranged', 'melee')]

    # 1) 城市无驻军且可及 -> 派近战进城
    for city in cities:
        if city['gar']:
            continue
        cands = [u for u in combat if kind(u['type']) == 'melee' and u['moves'] > 0
                 and hexdist(u['x'], u['y'], city['x'], city['y']) <= u['moves']]
        if not cands:
            continue
        u = max(cands, key=lambda z: z['hp'])
        text, _ = tl('unit_action', unit_id=u['id'], action='move', target_x=city['x'], target_y=city['y'])
        acted.append(f'驻军{city["id"]} <- 勇士{u["id"]}: {" ".join(text.split())[:120]}')

    # 2) 远程优先输出（不吃反击）
    for u in combat:
        if kind(u['type']) != 'ranged' or u['moves'] <= 0:
            continue
        tg = [e for e in enemies if hexdist(u['x'], u['y'], e['x'], e['y']) <= 2]
        if not tg:
            continue
        t = min(tg, key=lambda e: e['hp'])
        text, _ = tl('unit_action', unit_id=u['id'], action='attack', target_x=t['x'], target_y=t['y'])
        acted.append(f'弓{u["id"]}->[{t["type"]}@{t["x"]},{t["y"]} {t["hp"]}hp]: '
                     + ' '.join(text.split())[-160:])

    # 3) 近战补刀：残血敌人 / 贴近我方城市的敌人 / 自己血厚且敌人过半
    for u in combat:
        if kind(u['type']) != 'melee' or u['moves'] <= 0:
            continue
        adj = [e for e in enemies if hexdist(u['x'], u['y'], e['x'], e['y']) <= 1]
        if not adj:
            continue
        near_city = [e for e in adj if any(hexdist(e['x'], e['y'], c['x'], c['y']) <= 2 for c in cities)]
        pick = None
        weak = [e for e in adj if e['hp'] <= 45]
        if weak:
            pick = min(weak, key=lambda e: e['hp'])
        elif near_city and u['hp'] >= 55:
            pick = min(near_city, key=lambda e: e['hp'])
        elif u['hp'] >= 70 and min(adj, key=lambda e: e['hp'])['hp'] <= 60:
            pick = min(adj, key=lambda e: e['hp'])
        if not pick:
            continue
        text, _ = tl('unit_action', unit_id=u['id'], action='attack', target_x=pick['x'], target_y=pick['y'])
        acted.append(f'勇{u["id"]}->[{pick["type"]}@{pick["x"]},{pick["y"]} {pick["hp"]}hp]: '
                     + ' '.join(text.split())[-160:])

    if acted:
        for a in acted:
            log('  ' + a)
    else:
        log('  无战斗动作')
    return acted


def resolve_empty_production():
    text, _ = tl('get_cities')
    fixed = []
    for line in text.splitlines():
        if 'Building: nothing' in line:
            m = re.search(r'\[id:(\d+)\]', line)
            if m:
                cid = int(m.group(1))
                r, _ = tl('set_city_production', city_id=cid, item_type='UNIT', item_name='UNIT_ARCHER')
                fixed.append(f'{cid}:{" ".join(r.split())[:80]}')
    if fixed:
        log('  补生产: ' + ' | '.join(fixed))


def advance(turn):
    text, _ = tl('skip_remaining_units')
    ref = dict(
        tactical=f'autopilot turn {turn}: combat units handled by script (garrison cities, ranged focus-fire, melee finishers).',
        strategic='automated defense posture; see logs/combat_log.txt',
        tooling='auto_combat.py driver: scan via lua/battle_scan.lua, act via MCP unit_action, end via end_turn.',
        planning='hold cities, trade only favorable fights, end turn.',
        hypothesis='defensive war of attrition funded by economy.',
    )
    r, _ = tl('end_turn', **ref)
    first = ' '.join(r.split())[:200]
    log('  end_turn -> ' + first)
    if 'BLOCKED' in r:
        # 尝试解决软阻塞
        if 'PRODUCTION' in r or '选择生产项目' in r:
            resolve_empty_production()
            r2, _ = tl('end_turn', **ref)
            log('  end_turn retry -> ' + ' '.join(r2.split())[:200])
            r = r2
        if 'BLOCKED' in r:
            return 'blocked'
    if 'GAME OVER' in r:
        return 'over'
    if 'WAITING' in r:
        r3, _ = tl('end_turn', **ref)
        log('  end_turn wait2 -> ' + ' '.join(r3.split())[:160])
        if 'WAITING' in r3:
            code = ("pcall(function() UI.RequestAction(ActionTypes.ACTION_ENDTURN) end)"
                    "\nprint('FORCED')\nprint('---END---')")
            tl('run_lua', code=code, context='ingame')
            log('  forced UI ENDTURN')
    return 'ok'


def turn_now():
    text, _ = tl('get_game_overview')
    m = re.search(r'Turn (\d+)', text)
    return int(m.group(1)) if m else -1, text


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    deadline = time.time() + minutes * 60
    last = -1
    fails = 0
    log('==== auto_combat 启动 ====')
    while time.time() < deadline:
        try:
            t, ov = turn_now()
            if 'GAME OVER' in ov:
                log('GAME OVER 检测到，停止')
                break
            if t and t != last:
                last = t
                dip, _ = tl('get_pending_diplomacy')
                trd, _ = tl('get_pending_trades')
                if not dip.startswith('No pending') or not trd.startswith('No pending'):
                    log(f'T{t} 有外交/交易弹窗，停止自动：{dip[:80]} / {trd[:80]}')
                    break
                wars = war_players()
                cities, mine, foreign = scan()
                enemies = [u for u in foreign if u['owner'] in wars and kind(u['type']) in ('ranged', 'melee')]
                log(f'T{t} 城{len(cities)} 我兵{len(mine)} 敌{len(enemies)} 交战方{sorted(wars)}')
                do_combat(t, cities, mine, enemies)
                st = advance(t)
                if st == 'over':
                    log('GAME OVER，结束')
                    break
                if st == 'blocked':
                    fails += 1
                    log(f'  阻塞未解决 ({fails}/3)')
                    if fails >= 3:
                        log('连续阻塞，停止自动')
                        break
                else:
                    fails = 0
        except Exception as exc:  # noqa: BLE001
            log('异常: ' + str(exc)[:200])
        time.sleep(SLEEP)
    log('==== auto_combat 结束 ====')


if __name__ == '__main__':
    main()
