import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
const directory = path.dirname(fileURLToPath(import.meta.url));
const { values, positionals } = parseArgs({ allowPositionals: true, options: { 'request-file': { type: 'string' } } });
const tools = { inspect: 'inspect_controls', control: 'control_action', observe: 'observe_desktop', act: 'desktop_action', windows: 'list_windows', pause: 'pause_control', resume: 'resume_control', capture:'capture_target', sequence:'run_input_sequence', teach:'teach_visual_profile', watch:'run_visual_control' };
const tool = tools[positionals[0]];
if (!tool || ['act','control','capture','sequence','teach','watch'].includes(positionals[0]) && !values['request-file']) {
  console.log(JSON.stringify({ ok: false, message: '用法：client.mjs inspect|observe|windows|pause|resume，或 control|act|capture|sequence|teach|watch --request-file 请求.json' }));
  process.exit(1);
}
const require = createRequire(path.join(process.env.DESKTOP_CONTROL_RUNTIME_ROOT || 'F:\\deepseekA', 'package.json'));
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk/client/stdio.js');
const client = new Client({ name: 'codex-desktop-control', version: '1.0.0' });
try {
  const args = values['request-file'] ? JSON.parse((await readFile(values['request-file'], 'utf8')).replace(/^\uFEFF/, '')) : {};
  await client.connect(new StdioClientTransport({ command: process.execPath,
    args: [path.join(directory, 'server.mjs')], stderr: 'pipe' }));
  const result = await client.callTool({ name: tool, arguments: args }, undefined, { timeout: 40000 });
  console.log(JSON.stringify(result.structuredContent ?? { ok: false, message: '桌面工具未返回结构化结果，请重新观察。' }));
  process.exitCode = result.isError ? 1 : 0;
} catch {
  console.log(JSON.stringify({ ok: false, applied: null, message: '桌面工具连接或调用失败；请重新观察，不要自动重复输入。' }));
  process.exitCode = 1;
} finally { await client.close(); }
