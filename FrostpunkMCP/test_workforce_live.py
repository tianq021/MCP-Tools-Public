"""Explicitly authorized, reversible staff assignment test; restores in finally."""
import json
from takeover import Controller
from frostpunk_mcp import game_pid
from city_state import city_state
from workforce_control import assigned, agent_rows, set_workplace
c=Controller(game_pid())
try:
    initial=c.pause()
    rows=agent_rows(c)
    print(json.dumps({'agents':len(rows),'sample':[n for p,i,n in rows[:3]]}),flush=True)
    building=next(x for x in city_state(c.reader) if x['name'].startswith('EngineersLab'))
    before=assigned(c,building['id'])
    agent=before['assigned'][0]
    assert any(i==agent['id'] for p,i,n in rows)
    try:
        result=set_workplace(c,agent['id'],None)
        assert result['accepted'] and result['changed']
        assert assigned(c,building['id'])['count']==before['count']-1
        print(json.dumps({'removed':result}),flush=True)
    finally:
        restored=set_workplace(c,agent['id'],building['id'])
        assert restored['accepted']
        assert assigned(c,building['id'])==before
        print(json.dumps({'restored':restored}),flush=True)
    final=c.state()
    assert final['paused'] and final['clock_raw']==initial['clock_raw']
    assert final['resources']==initial['resources']
    print(json.dumps({'passed':True,'clock_stopped':True,'resources_unchanged':True}))
finally:c.close()
