import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { endpointDirectories, readEndpoint } from '../main-menu.mjs';

test('locates the bridge endpoint outside the client temp directory', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'plague-endpoint-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const token = 'a'.repeat(32);
  await assert.rejects(readEndpoint(7, [root]), /MENU_BRIDGE_NOT_LOADED/);
  await writeFile(path.join(root, '7.json'), JSON.stringify({ protocol: 1, pid: 7, port: 1234, token }));
  assert.deepEqual(await readEndpoint(7, [root]), { protocol: 1, pid: 7, port: 1234, token });
  await writeFile(path.join(root, '7.json'), JSON.stringify({ protocol: 1, pid: 8, port: 1234, token }));
  await assert.rejects(readEndpoint(7, [root]), /INVALID_BRIDGE_ENDPOINT/);
  await writeFile(path.join(root, '7.json'), 'not json');
  await assert.rejects(readEndpoint(7, [root]), /INVALID_BRIDGE_ENDPOINT/);

  const previous = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = path.join(root, 'local');
  try {
    assert.ok(endpointDirectories().includes(path.join(root, 'local', 'Temp', 'plague-inc-evolved-mcp')));
  } finally {
    if (previous === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = previous;
  }
});

test('main_menu exposes only supported navigation and requires a current state for selection', async t => {
  const client = new Client({ name: 'plague-menu-contract', version: '0.1.0' });
  const entry = fileURLToPath(new URL('../server.mjs', import.meta.url));
  t.after(() => client.close());
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [entry] }));

  const { tools } = await client.listTools();
  const menu = tools.find(tool => tool.name === 'main_menu');
  assert.ok(menu);
  assert.deepEqual(menu.inputSchema.properties.choice.enum, ['single_player', 'load_game', 'back']);

  const missingState = await client.callTool({ name: 'main_menu', arguments: { choice: 'single_player' } });
  assert.equal(missingState.structuredContent?.error, 'INVALID_MENU_ARGUMENTS');
  assert.equal(missingState.structuredContent.applied, false);

  const unsupported = await client.callTool({ name: 'main_menu', arguments: { choice: 'multiplayer' } });
  assert.equal(unsupported.isError, true);

  const singlePlayer = tools.find(tool => tool.name === 'single_player_menu');
  assert.ok(singlePlayer);
  assert.deepEqual(singlePlayer.inputSchema.properties.choice.enum,
    ['classic', 'outbreak', 'cure', 'speed_run', 'official_scenarios', 'custom_scenarios', 'start']);

  for (const choice of ['classic', 'start']) {
    const result = await client.callTool({ name: 'single_player_menu', arguments: { choice } });
    assert.equal(result.structuredContent?.error, 'INVALID_MENU_ARGUMENTS');
    assert.equal(result.structuredContent.applied, false);
  }

  assert.ok(tools.some(tool => tool.name === 'disease_menu'));
  const disease = await client.callTool({ name: 'disease_menu', arguments: { choice: 'bacteria' } });
  assert.equal(disease.structuredContent?.error, 'INVALID_MENU_ARGUMENTS');
  assert.equal(disease.structuredContent.applied, false);
  const invalid = await client.callTool({ name: 'disease_menu', arguments: { choice: '../bacteria' } });
  assert.equal(invalid.isError, true);

  assert.ok(tools.some(tool => tool.name === 'game_setup'));
  const stateId = `${'a'.repeat(32)}-1`;
  for (const args of [
    { choice: 'next' },
    { choice: 'set_name', state_id: stateId },
    { choice: 'next', state_id: stateId, name: 'MCP' }
  ]) {
    const result = await client.callTool({ name: 'game_setup', arguments: args });
    assert.equal(result.structuredContent?.error, 'INVALID_MENU_ARGUMENTS');
    assert.equal(result.structuredContent.applied, false);
  }

  assert.ok(tools.some(tool => tool.name === 'start_country'));
  for (const args of [{ choice: 'china' }, { choice: 'dialog_close' }]) {
    const result = await client.callTool({ name: 'start_country', arguments: args });
    assert.equal(result.structuredContent?.error, 'INVALID_MENU_ARGUMENTS');
    assert.equal(result.structuredContent.applied, false);
  }
  const badCountry = await client.callTool({ name: 'start_country', arguments: { choice: '../china' } });
  assert.equal(badCountry.isError, true);

  assert.ok(tools.some(tool => tool.name === 'evolution'));
  const evolve = await client.callTool({ name: 'evolution', arguments: { choice: 'air1' } });
  assert.equal(evolve.structuredContent?.error, 'INVALID_MENU_ARGUMENTS');
  assert.equal(evolve.structuredContent.applied, false);
  const badTech = await client.callTool({ name: 'evolution', arguments: { choice: 'bad id!' } });
  assert.equal(badTech.isError, true);

  assert.ok(tools.some(tool => tool.name === 'game_status'));
  for (const args of [{ choice: 'fastest' }, { choice: 'popup_close' }, { choice: 'dialog_a' }]) {
    const result = await client.callTool({ name: 'game_status', arguments: args });
    assert.equal(result.structuredContent?.error, 'INVALID_MENU_ARGUMENTS');
    assert.equal(result.structuredContent.applied, false);
  }
  const badSpeed = await client.callTool({ name: 'game_status', arguments: { choice: 'turbo' } });
  assert.equal(badSpeed.isError, true);
});
