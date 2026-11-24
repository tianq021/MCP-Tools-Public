r"""和平期值班：补空生产/空科技/空市政，结束回合；遇战争或阻塞立即停止。

用法: python 脚本\peace_driver.py [turns]
日志: E:\\Civ6\\logs\\peace_log.txt
"""
import re
import sys
import time

from civc import call

LOG = r'E:\Civ6\logs\peace_log.txt'
TECH_QUEUE = ['TECH_MATHEMATICS', 'TECH_MILITARY_ENGINEERING', 'TECH_BUTTRESS', 'TECH_CASTLES']
CIVIC_QUEUE = ['CIVIC_DRAMA_POETRY', 'CIVIC_GAMES_RECREATION', 'CIVIC_MILITARY_TRAINING']
DEFAULT_PROD = ['UNIT_ARCHER', 'BUILDING_GRANARY', 'BUILDING_MONUMENT', 'UNIT_WARRIOR']


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


def turn_now():
    text, _ = tl('get_game_overview')
    m = re.search(r'Turn (\d+)', text)
    return int(m.group(1)) if m else -1, text


def wars():
    text, _ = tl('get_diplomacy')
    out = []
    for line in text.splitlines():
        if 'AT WAR' in line:
            m = re.search(r'\[player (\d+)\]', line)
            out.append((line.strip()[:40], m.group(1) if m else '?'))
    return out


def fix_city_queues():
    text, _ = tl('get_cities')
    fixed = []
    for line in text.splitlines():
        if 'Building: nothing' in line:
            m = re.search(r'\[id:(\d+)\]', line)
            if not m:
                continue
            cid = int(m.group(1))
            for item in DEFAULT_PROD:
                kind = item.split('_', 1)[0]
                r, err = tl('set_city_production', city_id=cid, item_type=kind, item_name=item)
                if err or r.startswith('Error'):
                    continue
                fixed.append(f'{cid}:{item}')
                break
    return fixed


def fix_research():
    text, _ = tl('get_tech_civics')
    out = []
    if 'No technology being researched' in text:
        for t in TECH_QUEUE:
            r, err = tl('set_research', tech_or_civic=t, category='tech')
            if not err and not r.startswith('Error'):
                out.append(t)
                break
    if 'No civic being progressed' in text or 'Civic: None' in text:
        for c in CIVIC_QUEUE:
            r, err = tl('set_research', tech_or_civic=c, category='civic')
            if not err and not r.startswith('Error'):
                out.append(c)
                break
    return out


def main():
    turns = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    log('==== 和平期值班启动 ====')
    for i in range(turns):
        t, ov = turn_now()
        if 'GAME OVER' in ov:
            log('GAME OVER')
            return
        w = wars()
        if w:
            log(f'T{t} 检测到战争，停止自动: {w}')
            return
        fixed_p = fix_city_queues()
        fixed_r = fix_research()
        if fixed_p or fixed_r:
            log(f'T{t} 补生产={fixed_p} 补科技/市政={fixed_r}')
        tl('skip_remaining_units')
        dip, _ = tl('get_pending_diplomacy')
        trd, _ = tl('get_pending_trades')
        if not dip.startswith('No pending') or not trd.startswith('No pending'):
            log(f'T{t} 外交/交易弹窗，停止: {dip[:60]} | {trd[:60]}')
            return
        r, _ = tl('end_turn',
                  tactical=f'peace-driver turn {t}: keep garrisons, heal units, development only.',
                  strategic='peacetime build-up; economy and science lead is decisive.',
                  tooling='peace_driver.py: auto-fill empty queues/research then end turn.',
                  planning='hold, heal, wait for the human to start the next war.',
                  hypothesis='no war should start during peaceful development.')
        first = ' '.join(r.split())[:160]
        log(f'T{t} end_turn -> {first}')
        if 'BLOCKED' in r or 'GAME OVER' in r:
            log('  阻塞/结束，停止')
            return
        time.sleep(2)
    log('==== 和平期值班结束 ====')


if __name__ == '__main__':
    main()
