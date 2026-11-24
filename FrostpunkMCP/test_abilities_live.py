"""Reversible engine ability test, only in an existing save-blocked paused city."""
import json
from frostpunk_mcp import game_pid
from takeover import Controller
from city_state import entity_rows
from ability_control import abilities, set_enabled

c=Controller(game_pid())
try:
    c.pause()
    eid=next(i for e,i,n in entity_rows(c.reader) if n=='Generator_000')
    options=abilities(c,eid)
    choice=next(a for a in options['abilities'] if a['kind']=='toggle' and
                a['visible'] and a['unlocked'] and a['name'].startswith('Generator Overdrive'))
    initial=c.state()
    try:
        changed=set_enabled(c,eid,choice['name'],not choice['enabled'])
        print(json.dumps({'operation':changed}),flush=True)
        assert changed['accepted'] and changed['changed']
    finally:
        restored=set_enabled(c,eid,choice['name'],choice['enabled'])
        print(json.dumps({'restored':restored}),flush=True)
        assert restored['accepted'] and restored['enabled']==choice['enabled']
    final=c.state()
    assert final['paused'] and final['clock_raw']==initial['clock_raw']
    assert final['resources']==initial['resources']
    print(json.dumps({'passed':True,'ability':choice['name'],'clock_stopped':True,'resources_unchanged':True}))
finally:c.close()
