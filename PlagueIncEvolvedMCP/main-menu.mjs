import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { z } from 'zod';
import { getStatus } from './game.mjs';

const choice = z.enum(['single_player', 'load_game', 'back']);
const mode = z.enum(['classic', 'outbreak', 'cure', 'speed_run', 'official_scenarios', 'custom_scenarios']);
const modeChoice = z.enum([...mode.options, 'start']);
const diseaseId = z.string().regex(/^[a-z][a-z0-9_]{0,63}$/);
const requestIdFormat = z.uuid();
const stateIdFormat = z.string().regex(/^[a-f0-9]{32}-\d+$/);

export const menuInput = {
  choice: choice.optional().describe('省略则读取菜单。选择时使用当前 options 中可用的 ID。'),
  state_id: stateIdFormat.optional().describe('上一次读取返回的 state_id。选择时必填。'),
  request_id: requestIdFormat.optional().describe('可为选择提供 UUID。重试时沿用原 ID 和参数。')
};

export const singlePlayerMenuInput = {
  ...menuInput,
  choice: modeChoice.optional().describe('省略则读取模式页。传可用模式 ID 选择模式，传 start 激活当前模式的开始按钮。')
};

export const diseaseMenuInput = {
  ...menuInput,
  choice: diseaseId.optional().describe('省略则读取。选择时传当前 diseases 或 outbreaks 中可用的 ID。')
};

export const gameSetupInput = {
  ...menuInput,
  choice: diseaseId.optional().describe('省略则读取开局步骤。传当前 options 中可用的 ID。'),
  name: z.string().min(1).max(64).regex(/^[^\p{Cc}]+$/u).optional().describe('choice 为 set_name 时填写游戏内名称。')
};

const countryChoice = z.union([
  z.literal('dialog_close'),
  z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/)
]);
const techChoice = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/);

export const startCountryInput = {
  ...menuInput,
  choice: countryChoice.optional().describe('省略则读取初始国家页。dialog_close 关闭当前弹窗；传读取到的国家 ID 确认初始国家。')
};

export const evolutionInput = {
  ...menuInput,
  choice: techChoice.optional().describe('省略则读取 DNA 点数与进化选项。传可进化技术的 ID 执行进化。')
};

export const gameStatusInput = {
  ...menuInput,
  choice: z.enum(['paused', 'normal', 'faster', 'fastest', 'dialog_a', 'dialog_b', 'dialog_close', 'popup_close',
    'collect', 'autocollect', 'autocollect_off'])
    .optional().describe('省略则读取对局状态。paused/normal/faster/fastest 设置速度；popup_close 关闭新闻弹窗；dialog_* 点击当前弹窗按钮；autocollect 自动收集 DNA/解药气泡，collect 立即收集一次。')
};

const endpointFormat = z.object({
  protocol: z.literal(1), pid: z.number().int().positive(),
  port: z.number().int().min(1).max(65535), token: z.string().regex(/^[a-f0-9]{32}$/)
});

const endpointDirectoryName = 'plague-inc-evolved-mcp';

// The game writes its endpoint into the temp directory of its own process (normally
// %LOCALAPPDATA%\Temp), which is not always the temp directory this client sees.
export function endpointDirectories() {
  const local = process.env.LOCALAPPDATA ?? (process.env.USERPROFILE ? path.join(process.env.USERPROFILE, 'AppData', 'Local') : null);
  const roots = [tmpdir(), local ? path.join(local, 'Temp') : null];
  return [...new Set(roots.filter(Boolean).map(root => path.join(root, endpointDirectoryName)))];
}

export async function readEndpoint(pid, directories = endpointDirectories()) {
  let invalid = false;
  for (const directory of directories) {
    let text;
    try {
      text = await readFile(path.join(directory, `${pid}.json`), 'utf8');
    } catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'ENOTDIR') invalid = true;
      continue;
    }
    try {
      const endpoint = endpointFormat.parse(JSON.parse(text));
      if (endpoint.pid === pid) return endpoint;
    } catch { /* malformed or stale endpoint file */ }
    invalid = true;
  }
  throw new Error(invalid ? 'INVALID_BRIDGE_ENDPOINT' : 'MENU_BRIDGE_NOT_LOADED');
}

const responseFormat = z.object({
  ok: z.boolean(),
  screen: z.enum(['loading', 'main', 'single_player', 'load_game', 'unavailable_submenu', 'outside_main_menu',
    'game_setup', 'speed_run_setup', 'official_scenarios', 'custom_scenarios', 'in_game', 'choose_country', 'end_game']).optional(),
  blocked_by_overlay: z.boolean().optional(), state_id: stateIdFormat.optional(),
  options: z.array(z.object({ id: diseaseId, label: z.string(), available: z.boolean(), selected: z.boolean().optional() })).nullish(),
  single_player: z.object({
    selected_mode: mode.nullable(), title: z.string().nullable(), description: z.string().nullable(),
    modes: z.array(z.object({ id: mode, label: z.string(), available: z.boolean() })),
    start: z.object({ label: z.string(), available: z.boolean() }).nullable()
  }).nullish(),
  mode: mode.nullish(),
  selection_status: z.enum(['selectable', 'choose_outbreak', 'choose_scenario_first', 'other_setup_step', 'wrong_menu']).optional(),
  selected_disease: diseaseId.nullish(),
  diseases: z.array(z.object({ id: diseaseId, label: z.string(), locked: z.boolean(), available: z.boolean() })).optional(),
  selected_outbreak: diseaseId.nullish(),
  outbreaks: z.array(z.object({ id: diseaseId, label: z.string(), disease_id: diseaseId, disease_label: z.string(),
    locked: z.boolean(), available: z.boolean() })).optional(),
  stage: z.string().nullish(), title: z.string().nullish(), in_game: z.boolean().optional(),
  dialog: z.object({ title: z.string().nullable(), text: z.string().nullable() }).nullish(),
  name: z.string().nullish(), name_limit: z.number().int().nullish(), difficulty: z.number().int().nonnegative().nullish(),
  selected_genes: z.array(z.string()).optional(),
  popup: z.object({ title: z.string().nullable(), text: z.string().nullable() }).nullish(),
  allows_choice: z.boolean().nullish(),
  selected_country: z.string().nullish(),
  country_total: z.number().int().optional(),
  countries: z.array(z.object({
    id: z.string(), label: z.string(), population: z.number().int(), bonus: z.number().int(),
    island: z.boolean(), continent: z.string()
  })).optional(),
  dna_points: z.number().int().optional(),
  dna_spent: z.number().int().optional(),
  dna_earned: z.number().int().optional(),
  turn: z.number().int().optional(),
  available: z.array(z.object({
    id: z.string(), label: z.string(), type: z.string(), level: z.number().int(), cost: z.number().int()
  })).optional(),
  evolved: z.array(z.object({
    id: z.string(), label: z.string(), type: z.string(), level: z.number().int()
  })).optional(),
  game_state: z.string().optional(),
  speed: z.number().int().optional(),
  paused: z.boolean().optional(),
  date: z.string().nullish(),
  population: z.number().int().optional(),
  infected: z.number().int().optional(),
  healthy: z.number().int().optional(),
  dead: z.number().int().optional(),
  killed: z.number().int().optional(),
  zombie: z.number().int().optional(),
  infected_percent: z.number().optional(),
  severity: z.number().optional(),
  lethality: z.number().optional(),
  infectiousness: z.number().optional(),
  cure_percent: z.number().optional(),
  disease: z.string().nullish(),
  disease_name: z.string().nullish(),
  difficulty: z.number().int().nullish(),
  infected_countries: z.number().int().optional(),
  game_ended: z.boolean().optional(),
  game_won: z.boolean().nullish(),
  game_result: z.string().nullish(),
  collected: z.number().int().optional(),
  applied: z.boolean().nullable(), error: z.string().nullable()
});

export const mainMenu = args => callMenu('main-menu', args);
export const singlePlayerMenu = args => callMenu('single-player-menu', args);
export const diseaseMenu = args => callMenu('disease-menu', args);
export const gameSetup = args => callMenu('game-setup', args);
export const startCountry = args => callMenu('start-country', args);
export const evolution = args => callMenu('evolution', args);
export const gameStatus = args => callMenu('game-status', args);

async function callMenu(menu, { choice, state_id, request_id, name }) {
  const requestId = choice ? (request_id ?? randomUUID()).toLowerCase() : null;
  const fail = (error, applied = false) => ({ ok: false, error, applied, request_id: requestId });
  if ((choice && !state_id) || (!choice && (state_id || request_id))) return fail('INVALID_MENU_ARGUMENTS');
  if ((name !== undefined) !== (menu === 'game-setup' && choice === 'set_name')) return fail('INVALID_MENU_ARGUMENTS');

  const installation = await getStatus('steam');
  if (installation.processes.length !== 1) {
    return fail(installation.running === false ? 'STEAM_GAME_NOT_RUNNING' : 'STEAM_GAME_PROCESS_UNRESOLVED');
  }

  const [{ pid }] = installation.processes;
  let endpoint;
  try {
    endpoint = await readEndpoint(pid);
  } catch (error) {
    return fail(error.message);
  }

  const headers = { 'X-Plague-Token': endpoint.token };
  if (choice) {
    headers['X-Plague-State'] = state_id;
    headers['X-Plague-Request'] = requestId;
  }
  if (name !== undefined) headers['X-Plague-Name'] = Buffer.from(name, 'utf8').toString('base64');

  try {
    const route = `/v1/${menu}${choice ? `/${choice}` : ''}`;
    const response = await fetch(`http://127.0.0.1:${endpoint.port}${route}`, {
      method: choice ? 'POST' : 'GET', headers, redirect: 'error',
      signal: AbortSignal.timeout((menu === 'game-setup' || menu === 'start-country') && choice ? 11000 : 5500)
    });
    if (!response.ok) throw new Error('Unexpected bridge response');
    return { ...responseFormat.parse(await response.json()), request_id: requestId };
  } catch {
    return fail('MENU_BRIDGE_UNREACHABLE', choice ? null : false);
  }
}
