import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { findInstallation, inspectCopies } from '../game.mjs';

test('discovers a secondary Steam library and distinguishes another local copy', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'plague-mcp-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const library = path.join(root, 'secondary');
  const directory = path.join(library, 'steamapps', 'common', 'PlagueInc');
  await mkdir(path.join(root, 'steamapps'), { recursive: true });
  await mkdir(directory, { recursive: true });
  await writeFile(path.join(root, 'steamapps', 'libraryfolders.vdf'),
    ['"libraryfolders"', '{', '"1"', '{', '"path" ' + JSON.stringify(library), '}', '}'].join('\n'));
  await writeFile(path.join(library, 'steamapps', 'appmanifest_246620.acf'),
    ['"AppState"', '{', '"appid" "246620"', '"installdir" "PlagueInc"', '"buildid" "1234"', '}'].join('\n'));
  assert.equal((await findInstallation(root)).installed, false);
  await writeFile(path.join(directory, 'PlagueIncEvolved.exe'), '');
  assert.deepEqual(await findInstallation(root), { installed: true, directory, buildId: '1234', source: 'steam' });
  assert.equal((await findInstallation(root, directory)).buildId, '1234');
  await writeFile(path.join(root, 'PlagueIncEvolved.exe'), '');
  assert.deepEqual(await findInstallation(root, root), { installed: true, directory: root, buildId: null, source: 'configured' });
  const running = [
    { pid: 25, executable: path.join(directory, 'PlagueIncEvolved.exe') },
    { pid: 49, executable: path.join(root, 'PlagueIncEvolved.exe') },
    { pid: 50, executable: null }
  ];
  const { installations: [steam, local] } = await inspectCopies(root, root, running);
  assert.deepEqual(steam.processes, [running[0]]);
  assert.deepEqual(local.processes, [running[1]]);
  assert.equal(local.gameVersion, null);
  const onlySteam = await inspectCopies(root, root, [running[0]]);
  assert.equal(onlySteam.installations[1].running, false);
  const restricted = await inspectCopies(root, root, [running[2]]);
  assert.equal(restricted.installations[0].running, null);
  const missing = await inspectCopies(root, path.join(root, 'missing'), []);
  assert.equal(missing.installations[1].installed, false);
});

test('a stdio client can discover and call get_status on this computer', { skip: process.platform !== 'win32' }, async t => {
  const client = new Client({ name: 'plague-mcp-smoke', version: '0.1.0' });
  const entry = fileURLToPath(new URL('../server.mjs', import.meta.url));
  const transport = new StdioClientTransport({ command: process.execPath, args: [entry] });
  t.after(() => client.close());
  await client.connect(transport);
  const { tools } = await client.listTools();
  assert.ok(tools.some(tool => tool.name === 'get_status' && tool.annotations?.readOnlyHint));
  const result = await client.callTool({ name: 'get_status', arguments: {} });
  assert.ok(!result.isError, result.content[0]?.text);
  const data = result.structuredContent;
  assert.equal(data.appId, '246620');
  assert.equal(typeof data.installed, 'boolean');
  assert.equal(data.running, data.processes.length > 0);
  assert.equal(data.installed, typeof data.directory === 'string');
  const listed = await client.callTool({ name: 'list_installations', arguments: {} });
  assert.ok(!listed.isError, listed.content[0]?.text);
  assert.deepEqual(listed.structuredContent.installations.map(copy => copy.id), ['steam', 'local']);
  for (const copy of listed.structuredContent.installations) {
    const scoped = await client.callTool({ name: 'get_status', arguments: { installation: copy.id } });
    assert.ok(!scoped.isError, scoped.content[0]?.text);
    const status = scoped.structuredContent;
    for (const field of ['id', 'directory', 'gameVersion', 'unityVersion', 'buildId', 'installed'])
      assert.equal(status[field], copy[field]);
  }
});
