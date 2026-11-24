r"""Press end turn once, forcing it through the game UI if the MCP call stalls.

Usage: python 脚本\civadv.py [tactical note]
"""
import re
import sys

import civloop

note = sys.argv[1] if len(sys.argv) > 1 else 'routine turn'

overview = civloop.read('get_game_overview')
turn = int(re.search(r'Turn (\d+)', overview).group(1))
print(f'turn now {turn}', flush=True)

reflection = dict(
    tactical=note,
    strategic=' | '.join(overview.splitlines()[:6]),
    tooling='Forced-ENDTURN helper: end_turn -> WAITING -> run_lua UI.RequestAction(ActionTypes.ACTION_ENDTURN) -> poll Game.GetCurrentGameTurn(). Randomized tech/civics option regenerates an ENDTURN_BLOCKING_CIVIC notification each turn; dismissed via Lua.',
    planning='Continue the domination plan: 6 slingers -> archers once Archery lands, stage 2 tiles from Mbaba (31,14), declare war, then take Mbanza Kongo (30,18).',
    hypothesis='Kongo cannot stop 6 archers; Mbaba should fall ~3 turns after the first volley.',
)

civloop.advance_turn(turn, reflection)
after = civloop.current_turn()
print(f'turn after {after}', flush=True)
if after <= turn:
    print('STILL STUCK', flush=True)
    sys.exit(2)
