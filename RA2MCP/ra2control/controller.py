import asyncio
import math
import secrets
import time
from collections import deque

from .native import ra2yr


class Controller:
    """One native owner, scoped leases, bounded local waypoint execution.

    Enemy data is withheld until native visibility has been independently
    validated. Human input hooks are NOT implemented; release() is explicit.
    """
    def __init__(self, native, journal, interval=0.15):
        self.native, self.journal, self.interval = native, journal, interval
        self.lock = asyncio.Lock()
        self.state = None
        self.epoch = secrets.token_hex(8)
        self.lease = self.task = None
        self.updated = self.frame_changed = 0.0
        self.types = {}
        self.events = deque(maxlen=100)
        self.error = None
        self.seq = 0

    def event(self, kind, **data):
        self.seq += 1
        self.events.append(dict(seq=self.seq, kind=kind, time=time.time(), **data))

    def player(self):
        if self.state is None or self.state.stage != 2:
            raise ValueError('No live match')
        house = next((h for h in self.state.houses if h.current_player and h.is_human_player), None)
        if house is None or house.defeated or house.is_loser:
            raise ValueError('Player absent or defeated')
        return house

    def own(self):
        house = self.player()
        return {o.pointer_self: o for o in self.state.objects if o.pointer_house == house.self and o.health > 0 and o.on_map and not o.in_limbo}

    def ready(self):
        self.player()
        if time.monotonic() - self.updated > 1 or time.monotonic() - self.frame_changed > 2:
            raise ValueError('Snapshot stale or simulation not advancing')

    async def refresh(self):
        new = await self.native.state()
        old = self.state
        now = time.monotonic()
        old_house = next((h.self for h in old.houses if h.current_player), None) if old else None
        new_house = next((h.self for h in new.houses if h.current_player), None)
        reset = old is not None and (new.current_frame < old.current_frame or old_house != new_house or old.stage != new.stage)
        if reset:
            self.end_task('session_changed')
            self.lease = None
            self.epoch = secrets.token_hex(8)
            self.types = {}
            self.event('session_changed')
        if old is None or new.current_frame != old.current_frame:
            self.frame_changed = now
        self.state, self.updated = new, now
        self.error = None
        if not self.types and new.stage == 2:
            initial = await self.native.initial()
            self.types = {t.pointer_self: t for t in initial.object_types}

    def end_task(self, outcome):
        if self.task:
            self.journal.record(self.task['skill'], outcome, {k:v for k,v in self.task.items() if k != 'token'})
            self.event('task_ended', task_id=self.task['id'], outcome=outcome)
        self.task = None

    def check_lease(self, token):
        self.ready()
        if not self.lease or not secrets.compare_digest(self.lease['token'], token) or time.monotonic() >= self.lease['until']:
            raise ValueError('Missing, expired or superseded control token')
        if self.lease['epoch'] != self.epoch:
            raise ValueError('Match changed; acquire control again')
        return self.lease

    def validate_ids(self, ids):
        if not ids or len(ids) > 64 or len(ids) != len(set(ids)):
            raise ValueError('Specify 1..64 unique unit IDs')
        own = self.own()
        if any(i not in own for i in ids):
            raise ValueError('Unit missing, dead, off map or not owned by player')
        if any(i not in self.lease['units'] for i in ids):
            raise ValueError('Unit outside granted scope')
        return [own[i] for i in ids]

    async def acquire(self, owner, units, ttl=30):
        async with self.lock:
            self.ready()
            if self.lease and time.monotonic() < self.lease['until']:
                raise ValueError('Control is held; renew with token or explicitly release')
            if self.lease:
                await self.revoke('lease_expired')
            if not 5 <= ttl <= 120 or not owner.strip():
                raise ValueError('owner required; ttl must be 5..120 seconds')
            own = self.own()
            if not units or len(units) > 64 or any(i not in own for i in units):
                raise ValueError('Explicit owned unit scope required (1..64)')
            self.lease = dict(owner=owner, token=secrets.token_urlsafe(24), units=list(set(units)), until=time.monotonic()+ttl, epoch=self.epoch)
            self.event('control_acquired', owner=owner, units=units)
            return dict(token=self.lease['token'], epoch=self.epoch, ttl=ttl)

    async def renew(self, token, ttl=30):
        async with self.lock:
            lease = self.check_lease(token)
            if not 5 <= ttl <= 120:
                raise ValueError('ttl must be 5..120 seconds')
            lease['until'] = time.monotonic()+ttl
            return {'renewed': True, 'ttl': ttl}

    async def revoke(self, reason, stop=True):
        ids = self.lease['units'] if self.lease else []
        self.end_task(reason)
        self.lease = None
        self.event('control_released', reason=reason)
        if stop and ids:
            try:
                self.ready()
                own = self.own()
                ids = [i for i in ids if i in own and ra2yr.AbstractType.Name(own[i].object_type) != 'ABSTRACT_TYPE_BUILDING']
                if ids:
                    await self.native.order(ids, 'STOP')
            except Exception as exc:
                self.event('stop_unconfirmed', error=str(exc))

    async def release(self, token):
        async with self.lock:
            if not self.lease or not secrets.compare_digest(self.lease['token'], token):
                raise ValueError('Invalid control token')
            await self.revoke('released_by_client')
            return {'released': True}

    async def emergency_stop(self):
        async with self.lock:
            await self.revoke('manual_emergency_stop')
            return {'released': True, 'note': 'Stops scoped units when reachable; does not pause the game'}

    @staticmethod
    def coordinates(xy):
        if xy is None or len(xy) != 2 or any(type(v) is not int or v < 0 or v > 131071 for v in xy):
            raise ValueError('Expected two nonnegative integer world coordinates')
        return xy

    async def order(self, token, request_id, epoch, ids, action, xy=None):
        async with self.lock:
            if not request_id or len(request_id) > 128:
                raise ValueError('request_id required (max 128 chars)')
            self.check_lease(token)
            if epoch != self.epoch:
                raise ValueError('Stale match epoch')
            objects = self.validate_ids(ids)
            action = action.upper()
            if action not in {'MOVE', 'STOP', 'DEPLOY'}:
                raise ValueError('First version supports MOVE, STOP, DEPLOY; attacks require verified visibility')
            if action == 'MOVE':
                self.coordinates(xy)
            if any(ra2yr.AbstractType.Name(o.object_type) == 'ABSTRACT_TYPE_BUILDING' for o in objects):
                raise ValueError('Only mobile units accepted')
            params = dict(epoch=epoch, ids=ids, action=action, xy=xy)
            cached = self.journal.begin(request_id, params)
            if cached is not None:
                return cached
            self.end_task('replaced_by_direct_order')
            started = time.perf_counter()
            try:
                await self.native.order(ids, action, xy)
                result = {'status': 'acknowledged', 'effect_verified': False, 'ack_ms': round((time.perf_counter()-started)*1000, 3)}
            except ValueError as exc:
                result = {'status': 'rejected', 'error': str(exc)}
            except Exception as exc:
                result = {'status': 'outcome_unknown', 'error': str(exc)}
                await self.revoke('transport_error', stop=False)
            return self.journal.finish(request_id, result)

    async def waypoints(self, token, epoch, ids, points, duration=30, retreat_hp=0.6):
        async with self.lock:
            self.check_lease(token)
            if epoch != self.epoch:
                raise ValueError('Stale match epoch')
            objects = self.validate_ids(ids)
            if any(ra2yr.AbstractType.Name(o.object_type) not in {'ABSTRACT_TYPE_UNIT','ABSTRACT_TYPE_INFANTRY'} for o in objects):
                raise ValueError('Waypoint skill requires ground mobile units')
            if not 1 <= len(points) <= 32 or not 1 <= duration <= 120 or not 0 < retreat_hp <= 1:
                raise ValueError('1..32 waypoints, duration 1..120 seconds, retreat_hp (0,1]')
            for p in points:
                self.coordinates(p)
            self.end_task('replaced')
            now = time.monotonic()
            task_id = secrets.token_hex(8)
            self.task = dict(id=task_id, skill='waypoints', units=ids, points=points, index=0,
                             deadline=now+duration, started=now, sent=False, last_progress=now,
                             best_distance=None, health={o.pointer_self:o.health for o in objects},
                             home=[objects[0].coordinates.x, objects[0].coordinates.y], retreat_hp=retreat_hp,
                             frame_start=self.state.current_frame, reached=0)
            self.event('task_started', task_id=task_id)
            return {'task_id': task_id, 'status': 'running'}

    async def step(self):
        if self.lease and time.monotonic() >= self.lease['until']:
            await self.revoke('lease_expired')
            return
        if not self.task:
            return
        self.ready()
        task = self.task
        now = time.monotonic()
        own = self.own()
        if any(i not in own for i in task['units']):
            await self.revoke('unit_lost')
            return
        units = [own[i] for i in task['units']]
        if now >= task['deadline']:
            await self.native.order(task['units'], 'STOP')
            self.end_task('timeout')
            return
        if any(o.health < task['health'][o.pointer_self]*task['retreat_hp'] for o in units):
            await self.native.order(task['units'], 'MOVE', task['home'])
            self.event('retreat_issued', frame=self.state.current_frame, home=task['home'])
            self.end_task('retreat_issued_on_damage')
            return
        point = task['points'][task['index']]
        distance = max(math.hypot(o.coordinates.x-point[0], o.coordinates.y-point[1]) for o in units)
        if distance <= 256:
            task['reached'] += 1
            task['index'] += 1
            task['sent'], task['best_distance'], task['last_progress'] = False, None, now
            if task['index'] == len(task['points']):
                task['frame_end'] = self.state.current_frame
                self.end_task('arrived')
                return
            point = task['points'][task['index']]
            distance = max(math.hypot(o.coordinates.x-point[0], o.coordinates.y-point[1]) for o in units)
        if not task['sent']:
            t = time.perf_counter()
            await self.native.order(task['units'], 'MOVE', point)
            task['sent'] = True
            self.event('waypoint_sent', task_id=task['id'], point=point, ack_ms=round((time.perf_counter()-t)*1000,3))
        if task['best_distance'] is None or distance < task['best_distance']-32:
            task['best_distance'], task['last_progress'] = distance, now
        elif now-task['last_progress'] > 5:
            await self.native.order(task['units'], 'STOP')
            self.end_task('stuck')

    def status(self, after=0):
        try:
            house = self.player()
            units = []
            for o in self.own().values():
                tc = self.types.get(o.pointer_technotypeclass)
                units.append(dict(id=o.pointer_self, type=tc.name if tc else ra2yr.AbstractType.Name(o.object_type), hp=o.health,
                                  xy=[o.coordinates.x,o.coordinates.y], mission=ra2yr.Mission.Name(o.current_mission)))
            player = dict(name=house.name, money=house.money, power_output=house.power_output, power_drain=house.power_drain)
        except ValueError:
            player, units = None, []
        return dict(epoch=self.epoch, frame=self.state.current_frame if self.state else None,
                    ready=player is not None and time.monotonic()-self.updated < 1 and time.monotonic()-self.frame_changed < 2,
                    player=player, units=units, enemies=[], visibility='enemy data withheld; native visibility pending validation',
                    lease=None if not self.lease else {k:v for k,v in self.lease.items() if k != 'token'},
                    task=None if not self.task else {k:v for k,v in self.task.items() if k != 'health'},
                    events=[e for e in self.events if e['seq'] > after], error=self.error,
                    skills=self.journal.skills(), human_priority='explicit emergency_stop only; native input hooks not implemented')

    async def run(self):
        try:
            while True:
                start = time.monotonic()
                async with self.lock:
                    try:
                        await self.refresh()
                        self.player()
                        await self.step()
                    except Exception as exc:
                        self.error = str(exc) or type(exc).__name__
                        if self.lease:
                            await self.revoke('observation_unavailable', stop=False)
                await asyncio.sleep(max(0.01, self.interval-(time.monotonic()-start)))
        finally:
            await self.native.close()
