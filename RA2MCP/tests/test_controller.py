import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from ra2control.controller import Controller
from ra2control.journal import Journal
from ra2control.native import ra2yr


def state(frame=10):
    s = ra2yr.GameState(stage=2, current_frame=frame)
    s.houses.add(self=1, current_player=True, is_human_player=True, money=1000)
    s.houses.add(self=2)
    for addr, owner in [(100,1),(200,2)]:
        o = s.objects.add(pointer_self=addr, pointer_house=owner, health=100, on_map=True,
                          object_type=ra2yr.ABSTRACT_TYPE_UNIT, current_mission=ra2yr.Mission_Guard)
        o.coordinates.x = o.coordinates.y = 1024
    return s


class FakeNative:
    def __init__(self):
        self.s = state()
        self.calls = []
        self.fail = False
    async def state(self):
        copy = ra2yr.GameState()
        copy.CopyFrom(self.s)
        return copy
    async def initial(self): return self.s
    async def order(self, ids, action, xy=None):
        self.calls.append((ids, action, xy))
        if self.fail: raise TimeoutError('write outcome unknown')


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.tmp')
        self.j = Journal(Path(self.tmp.name)/'journal.db')
        self.n = FakeNative()
        self.c = Controller(self.n, self.j)
        await self.c.refresh()
        self.lease = await self.c.acquire('test', [100])
        self.token, self.epoch = self.lease['token'], self.lease['epoch']

    async def asyncTearDown(self):
        self.j.db.close()
        self.tmp.cleanup()

    async def test_enemy_not_exposed_or_controllable(self):
        self.assertEqual([u['id'] for u in self.c.status()['units']], [100])
        with self.assertRaises(ValueError):
            await self.c.order(self.token, 'enemy', self.epoch, [200], 'MOVE', [2000,2000])
        self.assertEqual(self.n.calls, [])

    async def test_duplicate_and_mismatched_request(self):
        a = await self.c.order(self.token, 'same', self.epoch, [100], 'STOP')
        b = await self.c.order(self.token, 'same', self.epoch, [100], 'STOP')
        self.assertEqual(a,b)
        self.assertEqual(len(self.n.calls),1)
        with self.assertRaises(ValueError):
            await self.c.order(self.token, 'same', self.epoch, [100], 'MOVE', [2000,2000])

    async def test_timeout_revokes_without_replay(self):
        self.n.fail = True
        r = await self.c.order(self.token,'lost',self.epoch,[100],'STOP')
        self.assertEqual(r['status'],'outcome_unknown')
        self.assertIsNone(self.c.lease)
        self.assertEqual(len(self.n.calls),1)

    async def test_exclusive_lease_and_expiry_cancels_task(self):
        with self.assertRaises(ValueError): await self.c.acquire('other',[100])
        await self.c.waypoints(self.token,self.epoch,[100],[[2048,1024]])
        self.c.lease['until'] = 0
        await self.c.step()
        self.assertIsNone(self.c.task)
        self.assertIsNone(self.c.lease)
        self.assertEqual(self.n.calls[-1][1],'STOP')

    async def test_local_skill_only_sends_once_then_observes_arrival(self):
        await self.c.waypoints(self.token,self.epoch,[100],[[2048,1024]])
        await self.c.step()
        await self.c.step()
        self.assertEqual(len(self.n.calls),1)
        self.c.state.objects[0].coordinates.x=2048
        await self.c.step()
        self.assertIsNone(self.c.task)
        self.assertEqual(self.j.skills()[0]['outcome'],'arrived')

    async def test_damage_triggers_retreat_without_llm(self):
        await self.c.waypoints(self.token,self.epoch,[100],[[2048,1024]])
        self.c.state.objects[0].health=30
        await self.c.step()
        self.assertEqual(self.n.calls[-1],([100],'MOVE',[1024,1024]))
        self.assertIsNone(self.c.task)

    async def test_epoch_change_fences_previous_control(self):
        self.n.s.current_frame=1
        await self.c.refresh()
        self.assertNotEqual(self.c.epoch,self.epoch)
        self.assertIsNone(self.c.lease)

    async def test_stale_simulation_rejected(self):
        self.c.frame_changed=time.monotonic()-5
        with self.assertRaises(ValueError):
            await self.c.order(self.token,'paused',self.epoch,[100],'STOP')

    async def test_crashed_journal_never_replays(self):
        self.j.begin('crash', {'x':1})
        self.j.db.close()
        self.j=Journal(Path(self.tmp.name)/'journal.db')
        self.assertEqual(self.j.begin('crash',{'x':1})['status'],'outcome_unknown')


if __name__ == '__main__': unittest.main()
