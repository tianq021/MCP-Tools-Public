import { execFile } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const directory = path.dirname(fileURLToPath(import.meta.url));
const { values } = parseArgs({ options: {
  config: { type: 'string', default: path.join(directory, 'config.local.json') },
  python: { type: 'string', default: 'python.exe' },
  'runtime-root': { type: 'string', default: path.resolve(directory, '..', '..') },
  'dry-run': { type: 'boolean', default: false },
} });
const runtimeRequire = createRequire(path.join(values['runtime-root'], 'package.json'));
const { McpServer } = runtimeRequire('@modelcontextprotocol/sdk/server/mcp.js');
const { StdioServerTransport } = runtimeRequire('@modelcontextprotocol/sdk/server/stdio.js');
const { z } = runtimeRequire('zod');

function runSender(check, text, signal) {
  return new Promise((resolve) => {
    const args = ['-B', '-X', 'utf8', path.join(directory, 'send_message.py'), '--config', values.config];
    if (check) args.push('--check');
    else {
      args.push('--stdin');
      if (values['dry-run']) args.push('--dry-run');
    }
    const child = execFile(values.python, args, {
      encoding: 'utf8', windowsHide: true, timeout: 22000,
      maxBuffer: 65536, signal,
    }, (error, stdout) => {
      try {
        const result = JSON.parse(stdout.trim());
        if (typeof result !== 'object' || result === null || typeof result.ok !== 'boolean') throw new Error();
        if (error && result.ok) throw new Error();
        resolve({ ...result, sent: !check && !values['dry-run'] && result.ok });
      } catch {
        resolve({ ok: false, sent: false, message: check
          ? '飞书配置检查未完成，请检查 Python 运行环境。'
          : '发送过程被中断或未返回有效结果，送达状态未知；未自动重试。' });
      }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(check ? '' : `[DSH]\n${text}`, 'utf8');
  });
}

function toolResult(result) {
  return {
    content: [{ type: 'text', text: JSON.stringify(result) }],
    structuredContent: result,
    isError: !result.ok,
  };
}

const server = new McpServer({ name: 'dsh-feishu-notify', version: '1.0.0' }, {
  instructions: '向配置好的飞书群发送用户要求的通知或任务结果。仅当用户要求发送，或已授权任务完成通知时调用 send_message。check_config 只检查本地配置。不要自动转发对话；超时或送达未知时不要自动重试。Webhook 和签名密钥由本地脚本读取，不需要在工具参数中提供。',
});

server.registerTool('send_message', {
  title: '发送飞书消息',
  description: '向预先配置的飞书群发送一条文本消息。用于用户要求的飞书通知、测试或已授权的任务完成通知。发送一次，不自动重试；失败时如实报告。',
  inputSchema: { text: z.string().trim().min(1).max(12000).describe('要发送的消息正文；不得包含 Webhook、签名密钥等凭据。') },
  annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
}, async ({ text }, extra) => toolResult(await runSender(false, text, extra.signal)));

server.registerTool('check_config', {
  title: '检查飞书通知配置',
  description: '检查本地飞书 Webhook 配置格式，不发送消息，不输出地址或密钥；检查通过不代表已送达。',
  inputSchema: {},
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
}, async (_args, extra) => toolResult(await runSender(true, '', extra.signal)));

await server.connect(new StdioServerTransport());
