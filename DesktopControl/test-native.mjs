import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const directory = path.dirname(fileURLToPath(import.meta.url));
const helper = path.join(directory, 'DesktopControl.exe');
function call(request) {
  return new Promise((resolve, reject) => {
    const child = execFile(helper, [], { encoding: 'utf8', timeout: 22000, windowsHide: true }, (_error, out) => {
      try { resolve(JSON.parse(out.trim())); } catch { reject(new Error('Native helper did not return JSON')); }
    });
    child.stdin.end(JSON.stringify(request), 'utf8');
  });
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function fixtureState(predicate = () => true) {
  for (let n = 0; n < 60; n++) {
    try {
      const state = JSON.parse(await readFile(path.join(directory, 'test-window-state.json'), 'utf8'));
      if (state.pid === fixture.pid && predicate(state)) return state;
    } catch {}
    await wait(100);
  }
  throw new Error('Test window did not reach the expected state');
}
const before = await call({ action: 'list_windows' });
assert.equal(before.ok, true);
assert.equal(before.paused, false, 'Controller is already paused; do not override a prior stop.');
const original = before.windows.find(window => window.foreground);
const fixture = spawn(path.join(directory, 'ControlTestWindow.exe'), [], { stdio: 'ignore' });
const checks = [];
let observation;
let ownPause = false;
try {
  let state = await fixtureState();
  await wait(350);
  observation = await call({ action: 'observe' });
  assert.equal(observation.ok, true, observation.message);
  if (observation.foreground.hwnd !== state.hwnd) {
    observation = await call({ action:'focus_window', observation_id:observation.observation_id, hwnd:String(state.hwnd) });
    assert.equal(observation.ok, true, observation.message);
  }
  assert.equal(observation.foreground.hwnd, state.hwnd, 'The test window must be in foreground.');
  const invalid = await call({ action: 'type_text', observation_id: '0'.repeat(32), text: 'MUST_NOT_APPEAR' });
  assert.equal(invalid.ok, false); assert.equal(invalid.applied, false);
  checks.push('invalid observation rejected without input');

  const firstId = observation.observation_id;
  observation = await call({ action: 'type_text', observation_id: firstId, text: 'DSH/Codex 中文输入测试 123' });
  assert.equal(observation.ok, true, observation.message);
  state = await fixtureState(value => value.text === 'DSH/Codex 中文输入测试 123');
  checks.push('real Unicode keyboard input');
  const stale = await call({ action: 'type_text', observation_id: firstId, text: 'MUST_NOT_APPEAR' });
  assert.equal(stale.ok, false); assert.equal(stale.applied, false);
  checks.push('consumed observation cannot be replayed');

  observation = await call({ action: 'click', observation_id: observation.observation_id, ...state.button });
  assert.equal(observation.ok, true, observation.message);
  state = await fixtureState(value => value.clicks === 1);
  checks.push('real mouse click');
  observation = await call({ action: 'click', observation_id: observation.observation_id, ...state.textbox });
  assert.equal(observation.ok, true, observation.message);
  observation = await call({ action: 'hotkey', observation_id: observation.observation_id, keys: 'ctrl+a' });
  assert.equal(observation.ok, true, observation.message);
  observation = await call({ action: 'type_text', observation_id: observation.observation_id, text: '键鼠测试通过' });
  assert.equal(observation.ok, true, observation.message);
  state = await fixtureState(value => value.text === '键鼠测试通过');
  checks.push('real Ctrl+A shortcut and replacement');

  const paused = await call({ action: 'pause' });
  assert.equal(paused.paused, true); ownPause = true;
  const denied = await call({ action: 'type_text', observation_id: observation.observation_id, text: 'MUST_NOT_APPEAR' });
  assert.equal(denied.ok, false); assert.equal(denied.applied, false);
  observation = await call({ action: 'resume' }); ownPause = false;
  assert.equal(observation.ok, true, observation.message);
  checks.push('pause blocks input and explicit resume restores observation');
  console.log(JSON.stringify({ ok: true, checks, finalText: state.text, clickCount: state.clicks,
    image: observation.image.path, dimensions: [observation.image.width, observation.image.height] }));
} finally {
  if (ownPause) await call({ action: 'resume' });
  if (original && observation?.ok) {
    const current = await call({ action: 'list_windows' });
    const active = current.windows?.find(window => window.foreground);
    const state = await fixtureState().catch(() => null);
    if (state && active?.hwnd === state.hwnd) {
      const fresh = await call({ action: 'observe' });
      if (fresh.ok) await call({ action: 'focus_window', observation_id: fresh.observation_id, hwnd: String(original.hwnd) });
    }
  }
  fixture.kill();
}
