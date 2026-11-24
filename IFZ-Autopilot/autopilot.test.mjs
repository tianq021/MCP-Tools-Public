import assert from 'node:assert/strict';
import test from 'node:test';
import { planNext, SquadAutopilot } from './autopilot.mjs';

const assignment = { sessionId: 'test-session', playerSquadId: '52', squadId: '53', headquartersId: '100' };
const target = { id: '201', position: { x: 60, z: 0 } };

function setup() {
  const squad = { id: '53', position: { x: 0, z: 0 }, active_order: null,
    queued_orders: [], members: [{ hp: 100 }], cargo: {} };
  const state = { ready: true, focused: true, time: { paused: false }, sessionId: assignment.sessionId,
    headquartersId: assignment.headquartersId, squads: [{ id: '52' }, squad], groups: [], virtualGroups: [] };
  const orders = [];
  const notices = [];
  const tasks = new Map();
  const executor = {
    submit: (action) => { orders.push(action); tasks.set(action.requestId, { state: 'watching' }); },
    get: (id) => tasks.get(id),
  };
  const autopilot = new SquadAutopilot({ assignment, readState: () => state, readTargets: () => [target],
    executor, onException: (event) => notices.push(event), maxNotifications: 2 });
  return { state, squad, orders, notices, tasks, autopilot, executor };
}

test('routine scavenging and unloading submit once each and never ask the model', async () => {
  const { autopilot, squad, orders, notices, tasks } = setup();
  await Promise.all([autopilot.tick(), autopilot.tick(), autopilot.tick()]);
  assert.equal(orders.length, 1);
  await autopilot.tick();
  assert.equal(orders.length, 1);
  squad.cargo = { res_cans: 2 };
  tasks.set(orders[0].requestId, { state: 'succeeded' });
  await autopilot.tick();
  await autopilot.tick();
  assert.deepEqual(orders.map(({ command, params }) => [command, params]), [
    ['scavenge_building', { building_id: '201' }], ['return_to_stockroom', { stockroom_id: '100' }],
  ]);
  assert.ok(orders.every(({ squadId }) => squadId === '53'));
  squad.cargo = {};
  tasks.set(orders[1].requestId, { state: 'succeeded' });
  await autopilot.tick();
  assert.deepEqual(notices, []);
});

test('ambiguous submission retains the original request and alerts only once', async () => {
  const { autopilot, squad, orders, notices, executor } = setup();
  squad.cargo = { res_cans: 2 };
  executor.submit = (action) => { orders.push(action); throw new Error('connection lost after submission'); };
  await autopilot.tick();
  for (let i = 0; i < 6; i++) await autopilot.tick();
  assert.equal(orders.length, 1);
  assert.equal(notices.length, 1);
  assert.equal(notices[0].requestId, orders[0].requestId);
});

test('reports actual exceptions during an ongoing order and honors the notification cap', async () => {
  const { autopilot, state, notices } = setup();
  await autopilot.tick();
  state.dialogue = { choice_id: 'one' };
  await autopilot.tick();
  await autopilot.tick();
  state.dialogue = { choice_id: 'two' };
  await autopilot.tick();
  state.dialogue = { choice_id: 'three' };
  await autopilot.tick();
  assert.deepEqual(notices.map(({ reason }) => reason), ['radio_choice', 'radio_choice']);
});

test('declines assignments after a headquarters or session change', async () => {
  const { autopilot, state, orders } = setup();
  state.headquartersId = '101';
  assert.equal((await autopilot.tick()).reason, 'assignment_unavailable');
  state.headquartersId = '100';
  state.sessionId = 'other';
  assert.equal((await autopilot.tick()).reason, 'assignment_unavailable');
  assert.deepEqual(orders, []);
});

test('requires verified carriage and ignores unknown or hidden enemies', () => {
  const { state, squad } = setup();
  squad.vehicle_id = 'car';
  assert.equal(planNext(state, assignment).reason, 'incomplete_cargo_state');
  state.vehicles = [{ id: 'car', trunk: { cargo: { res_cans: 2 } } }];
  state.groups = [{ id: 'enemy', visible: false, hostile_to_player: true, position: { x: 1, z: 0 } }];
  assert.equal(planNext(state, assignment).command, 'return_to_stockroom');
  state.groups[0].visible = true;
  assert.equal(planNext(state, assignment).reason, 'hostile_near_squad');
});
