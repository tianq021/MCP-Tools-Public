// Manual driver for live testing: connects to server.mjs over stdio and calls one tool.
// Usage: node test/drive.mjs <tool> ['{"json":"args"}']
// Not part of `npm test` (filename does not match *.test.mjs).
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';

const [tool, argsJson] = process.argv.slice(2);
if (!tool) {
  console.error('usage: node test/drive.mjs <tool> [json-args]');
  process.exit(2);
}
const entry = fileURLToPath(new URL('../server.mjs', import.meta.url));
const client = new Client({ name: 'plague-drive', version: '0.1.0' });
try {
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [entry] }));
  const result = await client.callTool({ name: tool, arguments: argsJson ? JSON.parse(argsJson) : {} });
  console.log(JSON.stringify(result.structuredContent ?? { isError: result.isError, content: result.content }, null, 2));
  if (result.isError) process.exitCode = 1;
} finally {
  await client.close();
}
