"""Explicit live move/stop smoke test; only current player's first ground unit."""
import asyncio, json, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor' / 'pyra2yr'))
from pyra2yr.network import DualClient
from ra2yrproto import commands_yr, commands_game, ra2yr
from google.protobuf.json_format import MessageToDict

report = {'commands': [], 'samples': [], 'passed': False}
async def main():
    c = DualClient('127.0.0.1', 14521, timeout=2)
    c.connect()
    async def run(cmd):
        return await asyncio.wait_for(c.exec_command(cmd, timeout=3), 5)
    async def state():
        q = commands_yr.GetGameState()
        r = await run(q)
        if not r.result.Unpack(q):
            raise RuntimeError('State unpack failed')
        if q.state.stage != 2:
            raise RuntimeError('Not in game')
        return q.state
    try:
        s = await state()
        house = next(h for h in s.houses if h.current_player and h.is_human_player)
        unit = next(o for o in s.objects if o.pointer_house == house.self and o.on_map and not o.in_limbo and ra2yr.AbstractType.Name(o.object_type) == 'ABSTRACT_TYPE_UNIT')
        addr = unit.pointer_self
        origin = (unit.coordinates.x, unit.coordinates.y)
        report['unit'] = MessageToDict(unit)
        async def sample(label):
            s = await state()
            u = next(o for o in s.objects if o.pointer_self == addr and o.pointer_house == house.self and o.health > 0)
            report['samples'].append({'label': label, 'frame': s.current_frame, 'unit': MessageToDict(u)})
            return u
        async def order(action, coords=None):
            await sample('before_' + action)
            q = commands_game.UnitOrder(object_addresses=[addr], action=getattr(ra2yr, action))
            if coords:
                q.coordinates.x, q.coordinates.y = coords
            t = time.monotonic()
            r = await run(q)
            report['commands'].append({'action': action, 'coordinates': coords, 'response_ms': round((time.monotonic()-t)*1000, 2), 'result': MessageToDict(r)})
        await order('UNIT_ACTION_MOVE', (origin[0]+2048, origin[1]))
        await asyncio.sleep(1)
        moving = await sample('moving')
        await order('UNIT_ACTION_STOP')
        await asyncio.sleep(1)
        stopped = await sample('stopped')
        await asyncio.sleep(1)
        stable = await sample('stable')
        xy = lambda u: (u.coordinates.x, u.coordinates.y)
        report['moved'] = xy(moving) != origin
        report['stopped_stable'] = xy(stopped) == xy(stable)
        report['passed'] = report['moved'] and report['stopped_stable']
    finally:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == '__main__':
    try: asyncio.run(main())
    except Exception as e: report['error'] = str(e) or type(e).__name__
    (ROOT / 'reports' / 'codex-control-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report['passed'] else 1)
