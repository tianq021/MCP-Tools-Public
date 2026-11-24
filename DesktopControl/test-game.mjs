import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { readFile, writeFile, unlink } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire('F:\\deepseekA\\package.json');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk/client/stdio.js');
const client = new Client({ name: 'desktop-game-validation', version: '2.0.0' });
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const checks = [];
const target = path.join(root, 'GameControlTestWindow.exe');
let fixture, observation, original, profile, activeWorker;

async function state(predicate = () => true, timeout = 5000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    try {
      const row = JSON.parse(await readFile(path.join(root, 'game-test-state.json'), 'utf8'));
      if (row.pid === fixture.pid && predicate(row)) return row;
    } catch {}
    await wait(30);
  }
  throw Error('Fixture state did not reach the required result.');
}
async function call(name, args = {}) {
  const result = await client.callTool({ name, arguments: args }, undefined, { timeout: 40000 });
  const row = result.structuredContent;
  assert(row && typeof row.ok === 'boolean', 'MCP structured result required');
  return { ...row, returned_image: result.content.some(item => item.type === 'image') };
}
async function observe() {
  const row = await call('observe_desktop', { include_screenshot: false });
  assert(row.ok, row.message);
  observation = row;
  return row;
}
async function sequence(steps, extra = {}) {
  const row = await call('run_input_sequence', {
    observation_id: observation.observation_id, target_executable: target,
    steps, dry_run: false, ...extra,
  });
  if (row.ok) observation = row;
  return row;
}

try {
  await client.connect(new StdioClientTransport({
    command: process.execPath, args: [path.join(root, 'server.mjs')], stderr: 'pipe',
  }));
  const before = await call('list_windows');
  assert(before.ok && !before.paused, 'Do not override an existing desktop stop.');
  original = before.windows.find(row => row.foreground);
  fixture = spawn(target, [], { stdio: 'ignore' });
  let current = await state();
  await wait(250);
  await observe();
  if (observation.foreground.hwnd !== current.hwnd) {
    observation = await call('desktop_action', {
      action: 'focus_window', observation_id: observation.observation_id, hwnd: String(current.hwnd),
    });
  }
  assert(observation.ok && observation.foreground.hwnd === current.hwnd, 'Fixture must be foreground.');
  const snapshot = await call('capture_target', { observation_id: observation.observation_id, target_executable: target });
  assert(snapshot.ok, snapshot.message);
  assert(snapshot.returned_image);
  assert.deepEqual(snapshot.client_size, [800, 480]);
  assert.equal(snapshot.target_executable.toLowerCase(), target.toLowerCase());
  observation = snapshot;
  checks.push('MCP returns real target pixels and exact executable binding');

  let row = await sequence([{ type: 'key', keys: ['w'] }], { target_executable: path.join(root, 'DesktopControl.exe') });
  assert.equal(row.ok, false); assert.equal(row.applied, false);
  row = await sequence([{ type: 'key', keys: ['w'] }, { type: 'click', x: 801, y: 0 }]);
  assert.equal(row.ok, false); assert.equal(row.applied, false);
  row = await sequence([{ type: 'key', keys: ['w'] }], { observation_id: '0'.repeat(32) });
  assert.equal(row.ok, false); assert.equal(row.applied, false);
  checks.push('wrong target, out-of-bounds later step, and stale observations refused before input');

  row = await sequence([{ type: 'click', x: 430, y: 190 }], { dry_run: true });
  assert(row.ok, row.message); assert.equal(row.applied, false);
  current = await state();
  assert.equal(current.clicks, 0);
  checks.push('dry run leaves application untouched');

  const consumed = observation.observation_id;
  row = await sequence([
    { type: 'key_down', keys: ['shift', 'w'] },
    { type: 'wait', duration_s: 0.35 },
    { type: 'key_up', keys: ['w', 'shift'] },
    { type: 'click', x: 430, y: 190 },
    { type: 'drag', x: 180, y: 330, to_x: 250, to_y: 330, duration_s: 0.2 },
    { type: 'relative_move', dx: 20, dy: 5, duration_s: 0.1 },
    { type: 'scroll', amount: 1 },
  ]);
  assert(row.ok, row.message); assert(row.applied && row.keys_released && row.returned_image);
  current = await state(v => v.holds > 5 && v.clicks === 1 && v.wheel === 120);
  assert(!current.w_pressed && !current.shift_pressed && !current.left_pressed);
  assert(current.drags >= 2 && current.moves > 3);
  checks.push('real scan-code chord, continuous W movement, mouse click, drag, relative motion, and wheel');
  row = await sequence([{ type: 'key', keys: ['w'] }], { observation_id: consumed });
  assert.equal(row.ok, false); assert.equal(row.applied, false);
  checks.push('successful input consumes observation and returns a new one');

  profile = 'fixture-visual-' + Date.now();
  row = await call('teach_visual_profile', { profile, snapshot: snapshot.session, rules: [{
    name: 'cue', crop: [362, 152, 498, 228], region: [350, 140, 510, 240],
    action: { type: 'click_match' }, threshold: 0.93, stable_frames: 2, cooldown_s: 1,
  }] });
  assert(row.ok, row.message);
  row = await call('run_visual_control', { observation_id: observation.observation_id,
    profile, duration_s: 3, fps: 30, max_actions: 1, dry_run: false });
  assert(row.ok, row.message);
  observation = row;
  assert(row.frames >= 2 && row.events.length === 1 && row.keys_released);
  await state(v => v.clicks === 2);
  checks.push('real DXGI frames trigger a visual rule and the application confirms the click');

  const previousDowns = current.keyDowns;
  const controller = new AbortController();
  const pending = client.callTool({ name: 'run_input_sequence', arguments: {
    observation_id: observation.observation_id, target_executable: target,
    steps: [{ type: 'key_down', keys: ['w'] }, { type: 'wait', duration_s: 10 }], dry_run: false,
  } }, undefined, { timeout: 40000, signal: controller.signal }).catch(() => null);
  await state(v => v.w_pressed && v.keyDowns > previousDowns);
  controller.abort();
  await pending;
  await state(v => !v.w_pressed, 3000);
  await wait(100);
  checks.push('MCP cancellation releases a held key without waiting for the plan to end');

  await observe();
  const python = process.env.DESKTOP_CONTROL_PYTHON || 'C:\\Program Files\\Python310\\python.exe';
  activeWorker = spawn(python, ['-B', path.join(root, 'realtime', 'runner.py')], {
    windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONPATH: path.join(root, 'realtime', '.venv', 'Lib', 'site-packages') },
  });
  activeWorker.stdin.on('error', () => {});
  activeWorker.stdout.on('data', () => {}); activeWorker.stderr.on('data', () => {});
  activeWorker.stdin.write(JSON.stringify({
    operation: 'sequence', observation_id: observation.observation_id, target_executable: target,
    steps: [{ type: 'key_down', keys: ['w'] }, { type: 'wait', duration_s: 15 }], dry_run: false,
  }) + '\n');
  await state(v => v.w_pressed);
  const journal = JSON.parse(await readFile('G:\\Codex_mmx\\desktop-control-data\\active-input.json', 'utf8'));
  activeWorker.kill();
  await state(v => !v.w_pressed, 3000);
  const recovered = JSON.parse(await readFile('G:\\Codex_mmx\\desktop-control-data\\last-recovery.json', 'utf8'));
  assert.equal(recovered.session, journal.session);
  assert.equal(recovered.recovered, true);
  checks.push('independent watchdog releases W after the worker is forcibly terminated');
  activeWorker = null;

  await observe();
  const losingFocus = sequence([
    { type: 'key_down', keys: ['w'] }, { type: 'wait', duration_s: 10 },
  ]);
  await state(v => v.w_pressed);
  await writeFile(path.join(root, 'game-test-minimize.flag'), 'test');
  row = await losingFocus;
  assert.equal(row.ok, false);
  assert.equal(row.keys_released, true);
  assert.match(row.message, /失焦|窗口|最小化/);
  await state(v => !v.w_pressed, 3000);
  checks.push('focus loss stops the controller and releases owned inputs');

  const report = { ok: true, checks, profile, image: snapshot.image.path, verified_at: new Date().toISOString() };
  await writeFile(path.join(root, 'test-game-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  activeWorker?.kill();
  await unlink(path.join(root, 'game-test-minimize.flag')).catch(() => {});
  if (fixture) {
    const windows = await call('list_windows').catch(() => null);
    if (windows?.windows?.some(v => v.foreground && v.hwnd === observation?.foreground?.hwnd) && original) {
      const fresh = await observe().catch(() => null);
      if (fresh) await call('desktop_action', { action: 'focus_window', observation_id: fresh.observation_id, hwnd: String(original.hwnd) }).catch(() => {});
    }
    fixture.kill();
  }
  await client.close();
}
