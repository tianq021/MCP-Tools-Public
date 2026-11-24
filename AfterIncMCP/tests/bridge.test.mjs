import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { BridgeClient } from '../lib/bridge-client.mjs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createServer } from '../server.mjs';

test('only the loopback game bridge can be selected', () => {
  for (const endpoint of ['http://example.com/v1/call', 'http://127.0.0.1/other', 'https://127.0.0.1/v1/call', 'http://user@127.0.0.1/v1/call']) {
    assert.throws(() => new BridgeClient({ endpoint }));
  }
});

test('a lost mutation response is never silently retried and keeps its request ID', async t => {
  let count = 0;
  const server = http.createServer((req, res) => { count++; req.socket.destroy(); });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => server.close());
  const client = new BridgeClient({ endpoint: `http://127.0.0.1:${server.address().port}/v1/call` });
  const result = await client.call('execute_action', {}, 'operation-001');
  assert.equal(count, 1); assert.equal(result.applied, null); assert.equal(result.request_id, 'operation-001');
});

test('MCP exposes the same contract, validates inputs, and propagates game errors', async t => {
  const calls = [];
  const server = createServer({ call: async (...args) => {
    calls.push(args); return { ok: false, applied: false, error: { code: 'NOT_IN_GAME', message: 'Open a settlement' } };
  } });
  const client = new Client({ name: 'contract-test', version: '1' });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(a), client.connect(b)]);
  t.after(async () => { await client.close(); await server.close(); });
  const list = await client.listTools();
  assert.equal(list.tools.length, 34);
  const names = list.tools.map(tool => tool.name).sort();
  assert.deepEqual(names, ['acquire_control', 'activate_game_control', 'activate_map_marker', 'advance', 'build_outpost', 'choose_campaign_option', 'debug_pause', 'deploy_soldier', 'enter_campaign_level', 'execute_action', 'get_campaign_map', 'get_game_controls', 'get_map', 'get_map_markers', 'get_military', 'get_objectives', 'get_outposts', 'get_settlement_purchases', 'get_settlement_sites', 'get_soldier_orders', 'get_state', 'get_technology', 'get_zone_details', 'list_actions', 'move_soldier', 'open_campaign_map', 'purchase_settlement', 'release_control', 'select_campaign_region', 'set_speed', 'settle_zone', 'status', 'step_turn', 'unpause']);
  for (const name of ['purchase_settlement', 'deploy_soldier', 'move_soldier', 'build_outpost', 'settle_zone', 'activate_map_marker', 'activate_game_control', 'open_campaign_map', 'select_campaign_region', 'enter_campaign_level', 'choose_campaign_option']) {
    assert.equal((await client.callTool({ name, arguments: {} })).isError, true);
    assert.equal(list.tools.find(tool => tool.name === name).annotations.readOnlyHint, false);
  }
  assert.equal(calls.length, 0);
  const result = await client.callTool({ name: 'get_state', arguments: {} });
  assert.equal(result.isError, true); assert.equal(result.structuredContent.error.code, 'NOT_IN_GAME');
  const bad = await client.callTool({ name: 'advance', arguments: { turns: 999 } });
  assert.equal(bad.isError, true); assert.equal(calls.length, 1);
  await client.callTool({ name: 'set_speed', arguments: { speed: 'Paused', request_id: 'operation-002' } });
  assert.deepEqual(calls.at(-1), ['set_speed', { speed: 'Paused' }, 'operation-002']);
  const beforeInvalid = calls.length;
  for (const [name, args] of [['get_zone_details', { zone_id: -1 }], ['list_actions', { zone_id: 0, kind: 'typo' }], ['purchase_settlement', { tech_id: '', expected_revision: '0'.repeat(24) }], ['purchase_settlement', { tech_id: 'water_1', expected_revision: 'invalid' }]]) {
    assert.equal((await client.callTool({ name, arguments: args })).isError, true);
  }
  assert.equal(calls.length, beforeInvalid);
  for (const [name, args] of [['get_zone_details', {zone_id: 0}], ['list_actions', {zone_id: 0, kind: 'building'}], ['get_settlement_purchases', {}], ['get_map_markers', {}], ['get_soldier_orders', {}], ['get_outposts', {}], ['get_military', {}], ['get_technology', {}]]) {
    await client.callTool({name, arguments: args});
    assert.deepEqual(calls.at(-1), [name, args, undefined]);
    assert.equal(list.tools.find(t => t.name === name).annotations.readOnlyHint, true);
  }
  await client.callTool({ name: 'release_control', arguments: { pause: false, request_id: 'operation-release-001' } });
  assert.deepEqual(calls.at(-1), ['release_control', { pause: false }, 'operation-release-001']);
  const purchase = { tech_id: 'water_1', expected_revision: 'b'.repeat(24) };
  await client.callTool({ name: 'purchase_settlement', arguments: { ...purchase, request_id: 'operation-shop-001' } });
  assert.deepEqual(calls.at(-1), ['purchase_settlement', purchase, 'operation-shop-001']);
  const order = { zone_id: 1, expected_revision: 'a'.repeat(24) };
  for (const [name, args] of [['deploy_soldier', order], ['build_outpost', order], ['move_soldier', { ...order, unit_id: 'unit-test' }]]) {
    await client.callTool({ name, arguments: { ...args, request_id: 'operation-deploy-001' } });
    assert.deepEqual(calls.at(-1), [name, args, 'operation-deploy-001']);
  }
  const level = { campaign_id: 'campaign-A', level_id: 'level-B', expected_revision: '0'.repeat(24) };
  await client.callTool({ name: 'enter_campaign_level', arguments: { ...level, request_id: 'operation-map-001' } });
  assert.deepEqual(calls.at(-1), ['enter_campaign_level', level, 'operation-map-001']);
});
