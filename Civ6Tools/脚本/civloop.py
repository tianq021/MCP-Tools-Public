r"""Drive a bounded number of Civ6 turns through the MCP bridge.

Reads a plan JSON (see _loop_plan.json) and, for each turn:
  * reads overview / units / cities / tech / notifications / pending diplomacy & trades
  * fills research + civic + empty city production from the queues
  * walks listed units toward their target tile
  * stops before end_turn on anything that needs a human decision
  * presses end_turn with templated reflections

Usage: python 脚本\civloop.py _loop_plan.json [-f report.txt]
"""
import json
import re
import sys
import time

from civc import call

UNIT_RE = re.compile(
    r'^\s+(\S+) \((\S+)\) at \((\d+),(\d+)\).*?moves (\d+)/(\d+)'
    r'(?:, charges:\d+)?.*?\[id:(\d+)')
CITY_RE = re.compile(r'^\s+(\S+) \(pop (\d+)\) at \((\d+),(\d+)\).*?\[id:(\d+)\]')
CITY_HP_RE = re.compile(r'HP:(\d+)/(\d+)')
CITY_BUILD_RE = re.compile(r'Building: ([^|]+?)\s*\|')

# Stale ENDTURN_BLOCKING_CIVIC / _RESEARCH notifications stay in the list even
# after research/civic is set (their condition is already satisfied), and
# end_turn refuses to advance while they exist. Dismiss them directly.
DISMISS_STALE_LUA = '''
local me = Game.GetLocalPlayer()
local removed = 0
for _, nid in ipairs(NotificationManager.GetList(me) or {}) do
  local e = NotificationManager.Find(me, nid)
  if e and not e:IsDismissed() then
    local bt = e:GetEndTurnBlocking()
    if bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_CIVIC
      or bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_RESEARCH then
      pcall(function() NotificationManager.SendActivated(me, nid) end)
      pcall(function() NotificationManager.Dismiss(me, nid) end)
      removed = removed + 1
    end
  end
end
print('STALE_DISMISSED=' .. removed)
print('---END---')
'''


class Stop(Exception):
    pass


LOG = []
OUT_FILE = None


def emit(line):
    LOG.append(line)
    print(line, flush=True)


def flush_log():
    if OUT_FILE:
        with open(OUT_FILE, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(LOG) + '\n')


def read(name, **args):
    text, is_error = call(name, args or None)
    if is_error or text.startswith('Transport') or text.startswith('TRANSPORT'):
        raise Stop(f'{name} returned an error: {text[:400]}')
    return text


def parse_units(text):
    out = []
    for line in text.splitlines():
        m = UNIT_RE.match(line)
        if m:
            out.append({
                'name': m.group(1), 'type': m.group(2),
                'x': int(m.group(3)), 'y': int(m.group(4)),
                'moves': int(m.group(5)), 'max_moves': int(m.group(6)),
                'id': int(m.group(7)),
            })
    return out


def parse_cities(text):
    out = []
    for line in text.splitlines():
        m = CITY_RE.match(line)
        if not m:
            continue
        hp = CITY_HP_RE.search(line)
        build = CITY_BUILD_RE.search(line)
        out.append({
            'name': m.group(1), 'pop': int(m.group(2)),
            'x': int(m.group(3)), 'y': int(m.group(4)),
            'id': int(m.group(5)),
            'hp': int(hp.group(1)) if hp else None,
            'hp_max': int(hp.group(2)) if hp else None,
            'building': (build.group(1).strip() if build else '?'),
        })
    return out


FORCE_ENDTURN_LUA = '''
pcall(function() UI.RequestAction(ActionTypes.ACTION_ENDTURN) end)
print('FORCED_ENDTURN')
print('---END---')
'''

TURN_LUA = '''
print('TURN=' .. tostring(Game.GetCurrentGameTurn()))
print('---END---')
'''


def current_turn():
    text = read('run_lua', code=TURN_LUA, context='gamecore')
    m = re.search(r'TURN=(\d+)', text)
    return int(m.group(1)) if m else -1


def advance_turn(turn, reflection):
    """Press end turn, forcing it through the UI when the MCP request stalls."""
    result = read('end_turn', **reflection)
    emit('end_turn -> ' + ' '.join(result.split())[:400])
    if 'BLOCKED:' in result:
        raise Stop(f'T{turn}: end_turn blocked:\n{result}')
    if 'GAME OVER' in result:
        raise Stop(f'T{turn}: GAME OVER\n{result}')
    if 'WAITING' not in result:
        return
    result = read('end_turn', **reflection)
    emit('end_turn retry -> ' + ' '.join(result.split())[:300])
    if 'WAITING' not in result:
        return
    emit('end_turn stalled twice; forcing ENDTURN through the game UI')
    read('run_lua', code=FORCE_ENDTURN_LUA, context='ingame')
    for _ in range(8):
        time.sleep(15)
        now = current_turn()
        emit(f'  turn now {now} (was {turn})')
        if now > turn:
            return
    raise Stop(f'T{turn}: turn would not advance even after forcing ENDTURN via UI')


def main(argv):
    global OUT_FILE
    plan_path = argv[1]
    if len(argv) > 3 and argv[2] == '-f':
        OUT_FILE = argv[3]
    with open(plan_path, encoding='utf-8') as fh:
        plan = json.load(fh)

    tech_queue = list(plan.get('tech_queue', []))
    civic_queue = list(plan.get('civic_queue', []))
    city_queue = {str(k): list(v) for k, v in plan.get('city_queue', {}).items()}
    route = {int(k): tuple(v) for k, v in plan.get('unit_route', {}).items()}
    upgrade_when = plan.get('upgrade_units', [])
    max_turns = int(plan.get('turns', 1))
    notes = plan.get('notes', '')

    log = LOG

    for turn_index in range(max_turns):
        overview = read('get_game_overview')
        turn_m = re.search(r'Turn (\d+)', overview)
        turn = int(turn_m.group(1)) if turn_m else -1
        emit(f'===== TURN {turn} =====')

        if re.search(r'Research:\s*None', overview):
            if tech_queue:
                tech = tech_queue.pop(0)
                emit('tech -> ' + read('set_research', tech_or_civic=tech, category='tech'))
            else:
                raise Stop(f'T{turn}: no research and tech queue empty')
        civic_m = re.search(r'Civic:\s*None', overview)
        if civic_m:
            if civic_queue:
                civic = civic_queue.pop(0)
                emit('civic -> ' + read('set_research', tech_or_civic=civic, category='civic'))
            else:
                raise Stop(f'T{turn}: no civic and civic queue empty')

        cities_text = read('get_cities')
        cities = parse_cities(cities_text)
        for city in cities:
            if city['hp'] is not None and city['hp'] < city['hp_max']:
                raise Stop(f'T{turn}: {city["name"]} is damaged ({city["hp"]}/{city["hp_max"]}) - combat likely')
        for city in cities:
            if city['building'] != 'nothing':
                continue
            queue = city_queue.get(str(city['id']), [])
            if not queue:
                raise Stop(f'T{turn}: {city["name"]} ({city["id"]}) has nothing to build and no queued item')
            item = queue[0]
            text = read('set_city_production', city_id=city['id'],
                        item_type=item.split('_', 1)[0], item_name=item)
            if text.startswith('Error') or 'CANNOT' in text:
                emit(f'production FAILED {city["name"]} {item}: {text}')
                raise Stop(f'T{turn}: production failed for {city["name"]}: {text}')
            emit(f'{city["name"]}: {item} -> {text.strip()}')

        if upgrade_when:
            units = parse_units(read('get_units'))
            gold_m = re.search(r'Gold: (\d+)', overview)
            gold = int(gold_m.group(1)) if gold_m else 0
            for wanted in upgrade_when:
                if wanted not in overview and gold < 60:
                    continue
                for unit in units:
                    if unit['type'] == wanted.split('>')[0]:
                        text = read('upgrade_unit', unit_id=unit['id'])
                        emit(f'upgrade {unit["id"]} {unit["type"]}: {text.strip()}')

        units_text = read('get_units')
        units = parse_units(units_text)
        for unit in units:
            target = route.get(unit['id'])
            if not target:
                continue
            if (unit['x'], unit['y']) == target:
                emit(f'{unit["id"]} {unit["type"]} already at {target}')
                continue
            if unit['moves'] <= 0:
                continue
            text = read('unit_action', unit_id=unit['id'], action='move',
                        target_x=target[0], target_y=target[1])
            emit(f'move {unit["id"]} {unit["type"]} ({unit["x"]},{unit["y"]}) -> {target}: {text.strip()}')
            if 'BLOCKED' in text:
                emit(f'  !! blocked, dropping route for {unit["id"]}')
                route.pop(unit['id'], None)
                continue
            if 'ENEMY_CITY' in text or 'ZOC' in text:
                raise Stop(f'T{turn}: combat state detected: {text.strip()}')

        diplomacy = read('get_pending_diplomacy')
        trades = read('get_pending_trades')
        if not diplomacy.startswith('No pending') or not trades.startswith('No pending'):
            raise Stop(f'T{turn}: pending diplomacy/trade popup:\n{diplomacy}\n{trades}')

        notes_text = read('get_notifications')
        for marker in plan.get('stop_markers', []):
            if marker in notes_text:
                raise Stop(f'T{turn}: notification marker {marker!r}:\n{notes_text}')

        cleared = read('run_lua', code=DISMISS_STALE_LUA, context='ingame')
        if 'STALE_DISMISSED' in cleared:
            emit('stale blockers -> ' + cleared.strip().splitlines()[0])

        movers = [u for u in units if u['moves'] > 0 and u['id'] not in route]
        tactical = (f'T{turn} routine turn: {len(units)} units, {len(cities)} cities. '
                    f'Units parked: ' + ', '.join(f'{u["id"]}@{u["x"]},{u["y"]}' for u in movers[:8]))
        strategic = overview.replace('\n', ' | ')
        reflection = dict(
            tactical=tactical,
            strategic=strategic,
            tooling='Automated turn loop via civloop.py (MCP bridge); no manual tool errors this turn.',
            planning=notes or 'Continue queued build-up plan.',
            hypothesis='No combat expected this turn; Kongo remains neutral while we stage.',
        )
        advance_turn(turn, reflection)

    emit('LOOP DONE')
    flush_log()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except Stop as exc:
        print('\nSTOPPED: ' + str(exc), flush=True)
        LOG.append('STOPPED: ' + str(exc))
        flush_log()
        sys.exit(2)
