"""Opt-in reversible building on/off test in the save-blocked city."""
import json
from frostpunk_mcp import game_pid
from takeover import Controller
from city_state import city_state
from building_control import set_enabled

c=Controller(game_pid())
try:
    initial=c.pause()
    item=next(x for x in city_state(c.reader) if x['name'].startswith('EngineersLab') and x['can_toggle'])
    try:
        result=set_enabled(c,item['id'],not item['enabled'])
        assert result['changed']
        print(json.dumps({'operation':result}),flush=True)
    finally:
        result=set_enabled(c,item['id'],item['enabled'])
        assert result['enabled']==item['enabled']
        print(json.dumps({'restored':result}),flush=True)
    final=c.state()
    assert final['paused'] and final['clock_raw']==initial['clock_raw']
    assert final['resources']==initial['resources']
    print(json.dumps({'passed':True,'clock_stopped':True,'resources_unchanged':True}))
finally:c.close()
