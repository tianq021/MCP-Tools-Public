import { readFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { BridgeClient } from './lib/bridge-client.mjs';

const { values, positionals } = parseArgs({ allowPositionals: true, options: {
  client: { type: 'string' }, 'request-file': { type: 'string' }, 'request-id': { type: 'string' }, endpoint: { type: 'string' },
} });
if (!positionals[0]) throw new Error('Usage: node client.mjs COMMAND [--client stable-client-id] [--request-file args.json]');
const args = values['request-file'] ? JSON.parse(await readFile(values['request-file'], 'utf8')) : {};
const result = await new BridgeClient({ endpoint: values.endpoint, clientId: values.client }).call(positionals[0], args, values['request-id']);
console.log(JSON.stringify(result, null, 2));
if (!result.ok) process.exitCode = 1;
