import { readFileSync, writeFileSync, copyFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire('F:/deepseekA/package.json');
const YAML = require('yaml');
const path = 'F:/deepseekA/dsh-home-020-20261007/profiles/web/cordis.patch.yml';
const snippet = readFileSync(new URL('../config/dsh.patch.yml', import.meta.url), 'utf8');
const original = readFileSync(path, 'utf8');
const parsed = YAML.parse(original);
if (!Array.isArray(parsed)) throw new Error('Unexpected DSH patch layout');
if (parsed.some(p => p.id === 'mcp-after-inc' || p.insert?.some(i => i.id === 'mcp-after-inc'))) {
  throw new Error('after_inc already configured; inspect before updating');
}
const updated = `${original.trimEnd()}\n\n# After Inc shared game MCP\n${snippet}`;
YAML.parse(updated);
const backup = `${path}.before-after-inc-${Date.now()}.bak`;
copyFileSync(path, backup);
writeFileSync(path, updated, 'utf8');
console.log(JSON.stringify({ registered: 'after_inc', backup, command: 'C:/nodejs/node.exe', args: ['E:/incMCP/server.mjs'] }));
