import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { parseArgs } from 'node:util';
import { planNext } from './autopilot.mjs';

async function main() {
  const { positionals: [folder], values } = parseArgs({
    allowPositionals: true,
    options: { squad: { type: 'string' }, player: { type: 'string' },
      'assume-resumed': { type: 'boolean' }, targets: { type: 'string' } },
  });
  if (!folder || !values.squad || !values.player || values.squad === values.player)
    throw new Error('用法：node preview.mjs <快照文件夹> --squad <脚本队> --player <玩家队> [--assume-resumed] [--targets <可搜建筑JSON>]');

  const json = async (file) => {
    const bytes = await readFile(file);
    const bom = bytes.subarray(0, 2).toString('hex');
    const encoding = bom === 'fffe' ? 'utf-16le' : bom === 'feff' ? 'utf-16be' : 'utf-8';
    return JSON.parse(new TextDecoder(encoding).decode(bytes));
  };
  const read = (file) => json(join(folder, `${file}.json`));
  const [game, squads, vehicles, groups, virtual] = await Promise.all([
    read('get_state'), read('world_data_squads'), read('world_data_vehicles'),
    read('world_data_groups'), read('world_data_virtual_groups'),
  ]);
  if (!game.session_id || game.headquarters_id == null || game.ok === false ||
      [squads, vehicles, groups, virtual].some((page) => page.ok === false ||
        page.session_id !== game.session_id || !Array.isArray(page.items) || page.next_offset != null))
    throw new Error('需要同一会话下完整的总部、小队、载具和群组快照。');
  const resumed = Boolean(values['assume-resumed']);
  const state = {
    ready: game.ready, sessionId: game.session_id, headquartersId: String(game.headquarters_id),
    focused: resumed || game.focused, time: resumed ? { ...game.time, paused: false } : game.time,
    dialogue: game.dialogue, radioPaused: game.radio_paused, game: { playerSquads: game.squads },
    squads: squads.items, vehicles: vehicles.items, groups: groups.items, virtualGroups: virtual.items,
  };
  const assignment = { sessionId: state.sessionId, headquartersId: state.headquartersId,
    squadId: values.squad, playerSquadId: values.player };
  const page = values.targets && await json(values.targets);
  if (page && (page.session_id !== state.sessionId || !Array.isArray(page.items) || page.ok === false))
    throw new Error('建筑候选需要同一会话的 get_map_buildings 结果（scavenge_only=true）。');
  console.log(JSON.stringify({ mode: 'offline_preview', squadId: values.squad, assumeResumed: resumed,
    decision: planNext(state, assignment, { targets: page ? page.items : undefined }) }, null, 2));
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
