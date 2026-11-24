import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';
const client = new Client({ name: 'after-inc-smoke', version: '0.1.0' });
const transport = new StdioClientTransport({ command: process.execPath, args: [fileURLToPath(new URL('../server.mjs', import.meta.url))], stderr: 'inherit' });
try {
  await client.connect(transport);
  console.log(JSON.stringify({ tools: (await client.listTools()).tools.map(t => t.name) }));
  for (const name of ['status', 'get_state']) console.log(JSON.stringify({ name, result: await client.callTool({ name, arguments: {} }) }));
} finally { await client.close(); }
