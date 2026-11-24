import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const directory = path.dirname(fileURLToPath(import.meta.url));
const { values } = parseArgs({ options: {
  python: { type: 'string', default: 'C:\\Program Files\\Python310\\python.exe' },
  'runtime-root': { type: 'string', default: path.resolve(directory, '..', '..') },
} });
const runtimeRequire = createRequire(path.join(values['runtime-root'], 'package.json'));
const { McpServer } = runtimeRequire('@modelcontextprotocol/sdk/server/mcp.js');
const { StdioServerTransport } = runtimeRequire('@modelcontextprotocol/sdk/server/stdio.js');

function run(action, signal) {
  return new Promise(resolve => {
    execFile(values.python, ['-B', '-X', 'utf8', path.join(directory, 'screen_tools.py'), action], {
      encoding: 'utf8', windowsHide: true, timeout: action === 'send' ? 85000 : 20000,
      maxBuffer: 65536, signal,
    }, (error, stdout) => {
      try {
        const result = JSON.parse(stdout.trim());
        if (result?.ok !== true && result?.ok !== false) throw new Error();
        if (error && result.ok) throw new Error();
        resolve(result);
      } catch {
        resolve({ ok: false, sent: false, message: '屏幕工具被中断或未返回有效结果；发送状态可能未知，未自动重试。' });
      }
    });
  });
}

async function resultFor(action, signal) {
  const result = await run(action, signal);
  const content = [{ type: 'text', text: JSON.stringify(result) }];
  if (result.ok && action !== 'check') {
    const capturePath = path.resolve(result.path);
    if (path.dirname(capturePath) !== path.join(directory, 'captures') || path.extname(capturePath) !== '.png') {
      return { isError: true, content: [{ type: 'text', text: '截图路径不属于本工具目录。' }] };
    }
    const image = await readFile(capturePath);
    if (image.byteLength > 10 * 1024 * 1024) {
      return { isError: true, content: [{ type: 'text', text: '截图超过 MCP 图片大小限制。' }] };
    }
    content.push({ type: 'image', mimeType: 'image/png', data: image.toString('base64') });
  }
  return { content, structuredContent: result, isError: !result.ok };
}

const server = new McpServer({ name: 'desktop-screen', version: '1.0.0' }, {
  instructions: '按用户要求查看电脑画面时调用 capture_screen，返回的是当前 Windows 主显示器截图。需要看懂内容时必须查看工具返回的图片，不要只根据文件名猜测。只有用户明确要求把截图发到飞书时，才调用 send_screen_to_feishu；它会截取新图片并发送到已配置群。check_feishu_config 不截图、不联网。每次调用只截图一次，不定时捕获，不控制鼠标键盘。发送超时或状态未知时不要自动重试。',
});
server.registerTool('capture_screen', {
  title: '查看电脑屏幕',
  description: '获取当前 Windows 主显示器的一张截图并返回图片，供查看和分析电脑画面。仅按用户要求使用，每次调用截图一次，不上传飞书。',
  inputSchema: {},
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: false, openWorldHint: false },
}, async (_args, extra) => resultFor('capture', extra.signal));
server.registerTool('send_screen_to_feishu', {
  title: '把电脑截图发到飞书',
  description: '按用户明确要求，截取当前主显示器画面，上传图片并发送到已配置的飞书群，同时返回截图。不要自动发送或重试。',
  inputSchema: {},
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
}, async (_args, extra) => resultFor('send', extra.signal));
server.registerTool('check_feishu_config', {
  title: '检查截图发送配置',
  description: '只检查本地截图发送配置与现有应用凭据是否存在；不截图，不联网，不输出密钥。',
  inputSchema: {},
  annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
}, async (_args, extra) => resultFor('check', extra.signal));
await server.connect(new StdioServerTransport());
