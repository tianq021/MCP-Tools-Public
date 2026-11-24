import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { getStatus, listInstallations } from './game.mjs';
import { mainMenu, menuInput, singlePlayerMenu, singlePlayerMenuInput, diseaseMenu, diseaseMenuInput, gameSetup, gameSetupInput,
  startCountry, startCountryInput, evolution, evolutionInput, gameStatus, gameStatusInput } from './main-menu.mjs';
import pkg from './package.json' with { type: 'json' };

const server = new McpServer({ name: pkg.name, version: pkg.version });

const installationId = z.enum(['steam', 'local']);
const copyStatus = {
  installed: z.boolean(), directory: z.string().nullable(), buildId: z.string().nullable(),
  source: z.enum(['steam', 'configured']).nullable(), gameVersion: z.string().nullable(),
  unityVersion: z.string().nullable(), running: z.boolean().nullable(),
  processes: z.array(z.object({ pid: z.number().int(), executable: z.string().nullable() }))
};
const readOnly = { readOnlyHint: true, openWorldHint: false };
function result(data) {
  return { structuredContent: data, content: [{ type: 'text', text: JSON.stringify(data) }] };
}

server.registerTool('list_installations', {
  title: '列出两份游戏安装',
  description: '分别返回 Steam 安装与本地副本的路径、游戏版本、Unity 版本和匹配进程。',
  inputSchema: {},
  outputSchema: { appId: z.string(), installations: z.array(z.object({ id: installationId, ...copyStatus })) },
  annotations: readOnly
}, async () => result(await listInstallations()));

server.registerTool('get_status', {
  title: '查看游戏安装和进程',
  description: '传 installation 为 steam 或 local 可仅查询对应安装。不传时沿用默认安装和所有同名进程。',
  inputSchema: { installation: installationId.optional() },
  outputSchema: { appId: z.string(), id: installationId.optional(), ...copyStatus },
  annotations: readOnly
}, async ({ installation }) => result(await getStatus(installation)));

server.registerTool('main_menu', {
  title: '查看和操作单人游戏主菜单',
  description: '仅用于 Steam 版。先不传参数读取；传可用 choice 和最新 state_id 激活按钮。支持进入单人模式选择、打开存档列表和返回主菜单。applied 为 true 表示已读回预期页面。',
  inputSchema: menuInput,
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
}, async args => result(await mainMenu(args)));

server.registerTool('single_player_menu', {
  title: '选择单人游戏模式',
  description: 'Steam 版。先用 main_menu 进入单人游戏，再空参读取可见模式。传模式 ID 和最新 state_id 选择模式。传 start 和最新 state_id 激活可用的开始按钮并确认目标页面。',
  inputSchema: singlePlayerMenuInput,
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
}, async args => result(await singlePlayerMenu(args)));

server.registerTool('disease_menu', {
  title: '读取和选择单人游戏病原体',
  description: 'Steam 版。空参读取当前单人设置页的游戏内病原体、锁定与选中状态。疫情模式列出可选择的疫情和各自病原体。传可用 choice 和最新 state_id 选择。',
  inputSchema: diseaseMenuInput,
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
}, async args => result(await diseaseMenu(args)));

server.registerTool('game_setup', {
  title: '完成单人开局设置',
  description: '读取当前单人开局步骤和可用动作。传 choice 和最新 state_id 选择。next 使用继续或开始按钮，set_name 还需 name。in_game 为真表示已读到游戏地图。',
  inputSchema: gameSetupInput,
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
}, async args => result(await gameSetup(args)));

server.registerTool('start_country', {
  title: '选择初始国家',
  description: '读取初始国家选择页的弹窗和可选国家（含人口与初始 DNA 加成）。先不传参数读取；用 dialog_close 关闭当前弹窗；再用最新 state_id 传国家 ID 确认初始国家。applied 为 true 表示已进入游戏内地图。',
  inputSchema: startCountryInput,
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
}, async args => result(await startCountry(args)));

server.registerTool('evolution', {
  title: '读取 DNA 点数并进化',
  description: '读取当前对局的 DNA 点数、已进化技术和当前可进化的技术（含花费）。传可进化技术的 ID 和最新 state_id 执行进化。',
  inputSchema: evolutionInput,
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
}, async args => result(await evolution(args)));

server.registerTool('game_status', {
  title: '读取对局状态并控制速度',
  description: '读取游戏状态、日期/回合、速度、DNA、人口与感染/死亡/解药进度、结束结果和当前弹窗。传 paused/normal/faster/fastest 设置速度；popup_close 关闭新闻弹窗；dialog_* 点击当前弹窗按钮。',
  inputSchema: gameStatusInput,
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
}, async args => result(await gameStatus(args)));

await server.connect(new StdioServerTransport());
