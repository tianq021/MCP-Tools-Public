"""Execute a bounded peacetime plan through MCP, stopping at any decision."""
import json
import re
import sys
import urllib.request

def call(name, **arguments):
    request = urllib.request.Request('http://127.0.0.1:8766/call',
        data=json.dumps(dict(name=name, arguments=arguments)).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    text = '\n'.join(c.get('text', '') for c in result.get('content', []))
    if result.get('isError'):
        raise RuntimeError(text)
    return text

for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 3):
    overview = call('get_game_overview')
    print(overview, flush=True)
    diplomacy = call('get_pending_diplomacy')
    trades = call('get_pending_trades')
    if not diplomacy.startswith('No pending') or not trades.startswith('No pending'):
        print('STOP: diplomacy\n' + diplomacy + '\n' + trades, flush=True)
        break
    units = call('get_units')
    cities = call('get_cities')
    print(units + '\n' + cities, flush=True)
    if ('Building: nothing' in cities or 'Research: None' in overview
            or re.search(r'HP:\s*(?:[1-8]?\d)/100|CAN ATTACK|attack targets|Targets:|can hit|can melee|can ranged|NEEDS PROMOTION', units, re.I)):
        print('STOP: city, research, or tactical decision', flush=True)
        break
    for unit_id in (131073, 196610, 327684, 393221, 458758):
        unit_line = next((line for line in units.splitlines() if f'[id:{unit_id},' in line), '')
        if unit_line and '(no moves)' not in unit_line:
            print(call('unit_action', unit_id=unit_id, action='automate'), flush=True)
    result = call('end_turn', tactical='Explorers follow their issued auto-explore orders; warrior 262147 guards Beijing (29,6). ' + units,
        strategic=overview + '\n' + cities,
        tooling='All reads and actions use the existing Civ6 MCP server through its persistent ClientSession.',
        planning='Finish monument, buy builder near 300 gold, expand after population 2; mining then pottery. Stop this batch at any blocker or combat decision.',
        hypothesis='Capital growth and Xueer establishment should improve early output; exploration will clarify the Kongo border.')
    print(result, flush=True)
    if not re.search(r'Turn (\d+) -> (\d+)', result) or any(s in result for s in ('BLOCKED:', 'WAITING:', 'HANG:', 'GAME OVER', 'CRITICAL')):
        print('STOP: turn requires a decision', flush=True)
        break
