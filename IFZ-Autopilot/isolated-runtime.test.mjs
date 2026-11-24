import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { setTimeout as wait } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import { SquadAutopilot } from './autopilot.mjs';

const project = process.env.IFZ_AUTOPILOT_TEST_REPO ?? 'E:\\InfectionFreeZoneMcp';
const fromProject = (file) => import(pathToFileURL(join(project, file)).href);
const [{ FakeBridge }, { StateService }, { ExecService }, { nearbyScavengeQuery, readMapPage }] = await Promise.all([
  fromProject('runtime/test/fake-bridge.mjs'), fromProject('runtime/state-service.mjs'),
  fromProject('runtime/exec-service.mjs'), fromProject('map-query.mjs'),
]);

const player = '834';
const scripted = '835';
const targets = ['417999991', '417999992'];

class IsolatedBridge extends FakeBridge {
  constructor(sessionId) {
    super({ sessionId, buildingCount: 0 });
    this.squad = this.world.squads.find(({ id }) => id === scripted);
    this.squad.active_order = null;
    this.squad.vehicle_id = null;
    this.world.buildings.push(...targets.map((id, index) => ({ id, can_scavenge: true, scavenged: false,
      position: { ...this.squad.position, x: this.squad.position.x + 25 + index * 10 } })));
    this.orders = [];
  }

  async call(method, args = {}) {
    if (['status', 'get_state', 'get_world_data', 'get_map_buildings'].includes(method)) return super.call(method, args);
    if (method === 'get_building_info') {
      const row = this.world.buildings.find(({ id }) => id === args.building_id);
      return { ok: Boolean(row), ...row };
    }
    if (method === 'hud_message') return { ok: true };
    if (method === 'acquire_control') {
      assert.equal(this.lease, undefined);
      this.lease = randomUUID();
      return { ok: true, lease: this.lease };
    }
    assert.ok(this.lease);
    assert.equal(args.lease, this.lease);
    if (method === 'release_control') {
      this.lease = undefined;
      return { ok: true };
    }
    assert.equal(args.session_id, this.sessionId);
    if (method === 'pause') {
      this.baseState.pause_request_id = args.pause_request_id;
      Object.assign(this.baseState.time, { paused: true, gameplay_pause: true });
      return this.getState();
    }
    assert.equal(this.baseState.time.paused, true);
    if (method === 'advance') {
      assert.equal(args.pause_request_id, this.baseState.pause_request_id);
      Object.assign(this.baseState.time, { paused: false, gameplay_pause: false });
      this.baseState.pause_request_id = null;
      return this.getState();
    }
    assert.ok(['scavenge_building', 'return_to_stockroom'].includes(method));
    assert.equal(args.squad_id, scripted);
    this.orders.push({ method, args });
    this.squad.active_order = method === 'scavenge_building' ? 'MoveToBuildingOrder' : 'MoveToStockroomOrder';
    return { ok: true, state: 'accepted', order: this.squad.active_order,
      ...(args.stockroom_id && { stockroom_id: args.stockroom_id, mounted: false }) };
  }

  complete() {
    const { method, args } = this.orders.at(-1);
    this.squad.active_order = null;
    if (method === 'scavenge_building') {
      const building = this.world.buildings.find(({ id }) => id === args.building_id);
      building.scavenged = true;
      this.squad.position = { ...building.position };
      this.squad.cargo.res_cans = 2;
      return;
    }
    const room = this.world.stockrooms.find(({ id }) => id === args.stockroom_id);
    this.squad.position = { ...room.position };
    this.world.stockpile.find(({ resource_id }) => resource_id === 'res_cans').quantity += this.squad.cargo.res_cans;
    this.squad.cargo = {};
  }
}

async function waitForTask(executor, requestId, state) {
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    const task = executor.get(requestId);
    if (task.state === state) return task;
    assert.notEqual(task.state, 'failed', JSON.stringify(task));
    await wait(10);
  }
  assert.fail(`订单未进入 ${state}：${JSON.stringify(executor.get(requestId))}`);
}

test('本地循环通过实际 Runtime 执行器和模拟桥接，日常订单不进入模型队列', async (t) => {
  const sessionId = randomUUID();
  const bridge = new IsolatedBridge(sessionId);
  const headquartersId = bridge.baseState.headquarters_id;
  const service = new StateService({ pipe: bridge });
  await service.pollOnce();
  const executor = new ExecService({ pipe: bridge, stateService: service,
    sessionId, playerSquadId: player, squadIds: [scripted] });
  t.after(async () => { service.stop(); await executor.stop(); });

  const notifications = [];
  const autopilot = new SquadAutopilot({
    assignment: { sessionId, headquartersId, playerSquadId: player, squadId: scripted }, executor,
    readState: () => ({ ...service.state, ready: service.ready && service.online,
      headquartersId: service.state.stockrooms.find(({ headquarters }) => headquarters)?.id }),
    readTargets: async (position, session) => (await readMapPage(bridge.call.bind(bridge), session,
      nearbyScavengeQuery(position))).items,
    onException: (message) => notifications.push(message),
  });
  const originalFood = bridge.world.stockpile.find(({ resource_id }) => resource_id === 'res_cans').quantity;

  for (const target of targets) {
    for (const [command, params] of [['scavenge_building', { building_id: target }],
      ['return_to_stockroom', { stockroom_id: headquartersId }]]) {
      const order = await autopilot.tick();
      assert.equal(order.command, command);
      assert.deepEqual(order.params, params);
      await waitForTask(executor, order.requestId, 'watching');
      const written = bridge.orders.length;
      await Promise.all([autopilot.tick(), autopilot.tick()]);
      assert.equal(bridge.orders.length, written);
      bridge.complete();
      await service.pollOnce({ forceMedium: true });
      const task = await waitForTask(executor, order.requestId, 'succeeded');
      assert.ok(task.evidence, '执行器需要从模拟世界状态确认结果');
      await autopilot.tick();
    }
  }

  assert.equal(bridge.orders.length, 4);
  assert.equal(bridge.world.stockpile.find(({ resource_id }) => resource_id === 'res_cans').quantity, originalFood + 4);
  assert.deepEqual(notifications, []);

  bridge.baseState.dialogue = { choice_id: 'simulated-radio', transmission_id: 'simulated-transmission', options: [] };
  await service.pollOnce();
  await autopilot.tick();
  await autopilot.tick();
  assert.deepEqual(notifications.map(({ reason }) => reason), ['radio_choice']);
  assert.equal(bridge.orders.length, 4);

  bridge.setSession(randomUUID());
  await service.pollOnce();
  assert.equal((await autopilot.tick()).reason, 'assignment_unavailable');
  assert.equal(bridge.orders.length, 4);
  t.diagnostic('2 轮搜刮与回仓，4 次模拟订单，4 罐入模拟仓，1 条模拟异常通知，0 次实际模型调用');
});
