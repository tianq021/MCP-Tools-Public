// Autoplay driver: plays a Plague Inc match through the MCP bridge.
// Usage: node test/autoplay.mjs   (env: AUTOPLAY_MINUTES=60, AUTOPLAY_COUNTRY=china)
// Not part of `npm test` (filename does not match *.test.mjs).
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';

const minutes = Number(process.env.AUTOPLAY_MINUTES ?? 60);
const countryPreference = process.env.AUTOPLAY_COUNTRY ?? 'china';
const deadline = Date.now() + minutes * 60_000;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const stamp = () => new Date().toISOString().slice(11, 19);
const log = (...parts) => console.log(stamp(), ...parts);
const retryable = new Set(['GAME_THREAD_TIMEOUT', 'MENU_BRIDGE_UNREACHABLE', 'MENU_BRIDGE_NOT_LOADED']);

const client = new Client({ name: 'plague-autoplay', version: '0.1.0' });
const entry = fileURLToPath(new URL('../server.mjs', import.meta.url));
await client.connect(new StdioClientTransport({ command: process.execPath, args: [entry] }));

async function call(name, args = {}) {
  const result = await client.callTool({ name, arguments: args });
  return result.structuredContent ?? { error: 'NO_STRUCTURED_CONTENT' };
}

// Retries transient bridge/focus failures until the deadline.
async function safeCall(name, args = {}) {
  for (let attempt = 0; ; attempt++) {
    if (Date.now() > deadline) return { error: 'AUTOPLAY_DEADLINE' };
    try {
      const data = await call(name, args);
      if (data && retryable.has(data.error)) {
        await sleep(1500);
        continue;
      }
      return data;
    } catch (error) {
      if (attempt >= 40) throw error;
      await sleep(1500);
    }
  }
}

function summary(status) {
  const percent = value => `${(value * 100).toFixed(1)}%`;
  return `turn=${status.turn} ${status.date ?? ''} dna=${status.dna_points} infected=${percent(status.infected_percent ?? 0)} ` +
    `dead=${percent((status.dead ?? 0) / (status.population || 1))} healthy=${percent((status.healthy ?? 0) / (status.population || 1))} ` +
    `cure=${percent(status.cure_percent ?? 0)} countries=${status.infected_countries} speed=${status.speed}`;
}

function pickPhase(status) {
  const population = status.population || 1;
  const healthy = (status.healthy ?? population) / population;
  const infected = status.infected_percent ?? ((status.infected ?? 0) / population);
  const cure = status.cure_percent ?? 0;
  if (healthy <= 0.02 || infected >= 0.985) return 'kill';
  if (healthy <= 0.05 && cure >= 0.35) return 'kill';
  return 'spread';
}

// Spread first without symptoms (keeps severity low, slows the cure); then buy lethality.
function rankTech(tech, phase, cure) {
  const id = tech.id;
  const slowsCure = id.includes('drug') || id.includes('genetic') || id.includes('hardening');
  if (phase === 'spread') {
    if (tech.type === 'transmission') {
      const order = ['air', 'water', 'insect', 'rodent', 'livestock', 'bird', 'blood'];
      const index = order.findIndex(key => id.startsWith(key));
      return index >= 0 ? index : 20;
    }
    if (tech.type === 'ability') {
      if (cure > 0.02 && slowsCure) return 21;
      if (id.includes('cold')) return 25;
      if (id.includes('heat')) return 26;
      return 30;
    }
    return -1;
  }
  if (tech.type === 'symptom') return 0;
  if (tech.type === 'ability' && cure > 0.15 && slowsCure) return 1;
  if (tech.type === 'ability') return 10;
  return -1;
}

async function dismissBlockers(status) {
  if (status.popup) {
    const result = await safeCall('game_status', { choice: 'popup_close', state_id: status.state_id });
    log('closed popup:', status.popup.title ?? '', result.error ?? '');
    return true;
  }
  if (status.dialog || (status.options && status.options.length > 0)) {
    const option = (status.options ?? []).find(candidate => candidate.available);
    if (option) {
      const result = await safeCall('game_status', { choice: option.id, state_id: status.state_id });
      log('closed dialog:', status.dialog?.title ?? option.label, result.error ?? '');
      return true;
    }
  }
  return false;
}

async function ensurePlaying() {
  for (let attempt = 0; attempt < 600; attempt++) {
    if (Date.now() > deadline) return false;
    const status = await safeCall('game_status');
    if (status.error === 'AUTOPLAY_DEADLINE') return false;
    if (status.error || status.game_state === 'none') {
      log('waiting for a game:', status.error ?? status.game_state);
      await sleep(1500);
      continue;
    }
    if (status.game_ended) return true;
    if (await dismissBlockers(status)) continue;
    if (status.game_state === 'in_progress') {
      const auto = await safeCall('game_status', { choice: 'autocollect', state_id: status.state_id });
      log('auto-collect bubbles:', auto.error ?? 'on');
      return true;
    }
    if (status.game_state === 'choose_country') {
      const select = await safeCall('start_country');
      if (select.error) continue;
      if (select.popup) {
        await safeCall('start_country', { choice: 'dialog_close', state_id: select.state_id });
        continue;
      }
      if (select.allows_choice && select.countries?.length) {
        const choice = select.countries.find(country => country.id === countryPreference) ?? select.countries[0];
        const result = await safeCall('start_country', { choice: choice.id, state_id: select.state_id });
        log('start country:', choice.label, result.applied, result.error ?? '');
        await sleep(800);
        continue;
      }
    }
    await sleep(1000);
  }
  return false;
}

async function main() {
  log('autoplay start, limit', minutes, 'minutes');
  if (!await ensurePlaying()) {
    log('could not reach an in-progress game');
    return 1;
  }

  let evolved = 0;
  let lastSummary = 0;
  for (;;) {
    if (Date.now() > deadline) {
      log('deadline reached');
      break;
    }
    const status = await safeCall('game_status');
    if (status.error === 'AUTOPLAY_DEADLINE') break;
    if (status.error) {
      await sleep(1000);
      continue;
    }
    if (status.game_ended) {
      log('GAME ENDED', 'won=' + status.game_won, 'result=' + status.game_result);
      log(summary(status));
      return status.game_won ? 0 : 2;
    }
    if (await dismissBlockers(status)) continue;
    if (status.speed !== 3) {
      const result = await safeCall('game_status', { choice: 'fastest', state_id: status.state_id });
      log('speed -> fastest', result.error ?? '');
      continue;
    }

    const evolution = await safeCall('evolution');
    if (evolution.error) {
      await sleep(600);
      continue;
    }
    const phase = pickPhase(status);
    const dna = evolution.dna_points ?? 0;
    const candidates = (evolution.available ?? [])
      .filter(tech => tech.cost <= dna)
      .map(tech => ({ ...tech, rank: rankTech(tech, phase, status.cure_percent ?? 0) }))
      .filter(tech => tech.rank >= 0)
      .sort((a, b) => a.rank - b.rank || a.cost - b.cost);

    if (candidates.length === 0) {
      await sleep(1200);
    } else {
      const chosen = candidates[0];
      const result = await safeCall('evolution', { choice: chosen.id, state_id: evolution.state_id });
      if (result.error === 'STALE_MENU') continue;
      if (result.applied) {
        evolved++;
        log(`evolve ${chosen.label} [${chosen.type} L${chosen.level} cost ${chosen.cost}] dna=${result.dna_points} phase=${phase}`);
      } else if (result.error) {
        log('evolve failed', chosen.id, result.error);
      }
      await sleep(200);
    }

    if (Date.now() - lastSummary > 20_000) {
      lastSummary = Date.now();
      log(summary(status), 'evolved=' + evolved, 'phase=' + phase);
    }
  }

  const status = await safeCall('game_status');
  log('final:', status.game_state, 'ended=' + status.game_ended, 'won=' + status.game_won, 'result=' + status.game_result);
  log(summary(status));
  return 0;
}

try {
  process.exitCode = await main();
} finally {
  await client.close();
}
