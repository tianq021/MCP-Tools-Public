import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtemp, writeFile, unlink, rmdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const directory = path.dirname(fileURLToPath(import.meta.url));
const { values } = parseArgs({ options: {
  server: { type: 'string', default: path.join(directory, 'server.mjs') },
  'runtime-root': { type: 'string', default: 'F:\\deepseekA' },
  config: { type: 'string', default: path.join(directory, 'config.local.json') },
  python: { type: 'string', default: 'C:\\Program Files\\Python310\\python.exe' },
  'send-test': { type: 'boolean', default: false },
} });
// Use the same v2 client installed with the active DSH runtime.
const runtimeRequire = createRequire('F:\\deepseekA-020-test\\package.json');
const { Client } = runtimeRequire('@modelcontextprotocol/client');
const { StdioClientTransport } = runtimeRequire('@modelcontextprotocol/client/stdio');
const temp = values['send-test'] ? null : await mkdtemp(path.join(os.tmpdir(), 'dsh-feishu-'));
const config = temp ? path.join(temp, 'config.json') : values.config;
if (temp) await writeFile(config, JSON.stringify({
  webhook_url: 'https://open.feishu.cn/open-apis/bot/v2/hook/test-token',
  sign_secret: 'test-secret', keyword: 'Codex',
}));
const client = new Client({ name: 'dsh-feishu-verification', version: '1.0.0' });
try {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [values.server, '--runtime-root', values['runtime-root'], '--python', values.python, '--config', config,
      ...(values['send-test'] ? [] : ['--dry-run'])],
    stderr: 'pipe',
  });
  await client.connect(transport);
  const { tools } = await client.listTools();
  assert.deepEqual(tools.map(tool => tool.name).sort(), ['check_config', 'send_message']);
  const checked = await client.callTool({ name: 'check_config', arguments: {} });
  assert.equal(checked.isError, false);
  assert.equal(checked.structuredContent.ok, true);
  assert.equal(checked.structuredContent.sent, false);
  if (!values['send-test']) {
    const invalid = await client.callTool({ name: 'send_message', arguments: { text: '   ' } });
    assert.equal(invalid.isError, true);
  }
  const sent = await client.callTool({ name: 'send_message', arguments: {
    text: '飞书通知测试：DSH 的 MCP 发送工具已连接，可以发送中文任务通知。',
  } });
  assert.equal(sent.isError, false);
  assert.equal(sent.structuredContent.ok, true);
  assert.equal(sent.structuredContent.sent, values['send-test']);
  console.log(JSON.stringify({ ok: true, tools: tools.map(tool => tool.name), sent: sent.structuredContent.sent,
    message: values['send-test'] ? '飞书已确认 DSH MCP 测试消息发送成功。' : 'DSH v2 MCP 客户端发现、参数校验与 dry-run 调用通过，未发送消息。' }));
} finally {
  await client.close();
  if (temp) {
    await unlink(config);
    await rmdir(temp);
  }
}
