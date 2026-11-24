import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const directory = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire('F:\\deepseekA\\package.json');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk/client/stdio.js');
const client = new Client({ name: 'desktop-input-verification', version: '1.0.0' });
try {
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [path.join(directory, 'server.mjs')], stderr: 'pipe' }));
  const { tools } = await client.listTools();
  assert.deepEqual(tools.map(tool => tool.name).sort(), ['capture_target','control_action','desktop_action','inspect_controls','list_windows','observe_desktop','pause_control','resume_control','run_input_sequence','run_visual_control','teach_visual_profile']);
  const windows = await client.callTool({ name: 'list_windows', arguments: {} });
  assert.equal(windows.isError, false);
  const observed = await client.callTool({ name: 'observe_desktop', arguments: {} });
  assert.equal(observed.isError, false);
  assert.equal(observed.structuredContent.applied, false);
  assert(observed.content.some(block => block.type === 'image' && block.mimeType === 'image/png'));
  const refused = await client.callTool({ name: 'desktop_action', arguments: { action:'type_text',observation_id:'0'.repeat(32),text:'MUST_NOT_APPEAR' } });
  assert.equal(refused.isError, true);
  assert.equal(refused.structuredContent.applied, false);
  console.log(JSON.stringify({ok:true,tools:tools.map(tool=>tool.name),imageReturned:true,invalidObservationRejected:true,inputInjected:false}));
} finally { await client.close(); }
