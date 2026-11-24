import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const directory = path.dirname(fileURLToPath(import.meta.url));
const { values } = parseArgs({ options: {
  capture: { type: 'boolean', default: false },
  'runtime-root': { type: 'string', default: 'F:\\deepseekA' },
} });
const runtimeRequire = createRequire('F:\\deepseekA-020-test\\package.json');
const { Client } = runtimeRequire('@modelcontextprotocol/client');
const { StdioClientTransport } = runtimeRequire('@modelcontextprotocol/client/stdio');
const client = new Client({ name: 'desktop-screen-verification', version: '1.0.0' });
try {
  await client.connect(new StdioClientTransport({
    command: process.execPath,
    args: [path.join(directory, 'server.mjs'), '--runtime-root', values['runtime-root']],
    stderr: 'pipe',
  }));
  const { tools } = await client.listTools();
  assert.deepEqual(tools.map(tool => tool.name).sort(), ['capture_screen', 'check_feishu_config', 'send_screen_to_feishu']);
  const checked = await client.callTool({ name: 'check_feishu_config', arguments: {} });
  assert.equal(checked.isError, false);
  assert.equal(checked.structuredContent.sent, false);
  if (values.capture) {
    const captured = await client.callTool({ name: 'capture_screen', arguments: {} });
    assert.equal(captured.isError, false);
    const image = captured.content.find(block => block.type === 'image');
    assert.equal(image.mimeType, 'image/png');
    const bytes = Buffer.from(image.data, 'base64');
    assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(bytes.readUInt32BE(16), captured.structuredContent.width);
    assert.equal(bytes.readUInt32BE(20), captured.structuredContent.height);
    console.log(JSON.stringify({ ok: true, tools: tools.map(tool => tool.name), ...captured.structuredContent, imageReturned: true }));
  } else {
    console.log(JSON.stringify({ ok: true, tools: tools.map(tool => tool.name), sent: false, captured: false }));
  }
} finally {
  await client.close();
}
