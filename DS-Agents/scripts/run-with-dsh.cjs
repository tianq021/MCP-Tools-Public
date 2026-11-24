/* Reuse the existing local DSH credential in memory; never copy it to this project. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {spawn, execFileSync} = require('node:child_process');

function loadDshKey(rootPath) {
  const root = fs.realpathSync(rootPath);
  const server = JSON.parse(fs.readFileSync(path.join(root, 'logs', 'server.json'), 'utf8').replace(/^\uFEFF/, ''));
  if (typeof server.dshHome !== 'string' || typeof server.entry !== 'string') {
    throw new Error('DSH server.json is missing dshHome or entry.');
  }
  const home = fs.realpathSync(server.dshHome);
  const relative = path.relative(root, home);
  if (path.isAbsolute(relative) || relative === '..' || relative.startsWith('..' + path.sep)) {
    throw new Error('DSH credential home must be inside the specified DSH root.');
  }
  const credentialFile = fs.realpathSync(path.join(home, '.credentials.yaml'));
  if (path.dirname(credentialFile) !== home) throw new Error('DSH credential file cannot be an external link.');
  const requireDsh = createRequire(fs.realpathSync(server.entry));
  const yaml = requireDsh('js-yaml'); // DSH's own installed dependency, not a new package.
  const value = yaml.load(fs.readFileSync(credentialFile, 'utf8'));
  const key = value?.refs?.DEEPSEEK_API_KEY;
  if (typeof key !== 'string' || !key.startsWith('sk-') || /\s/.test(key)) {
    throw new Error('This DSH credential format has no usable DeepSeek API key. Use scripts/set-key.ps1 instead.');
  }
  return key;
}

function proxyEnvironment(settings) {
  if (!settings || Number(settings.ProxyEnable) !== 1 || typeof settings.ProxyServer !== 'string') {
    throw new Error('No enabled Windows proxy was found. Omit --system-proxy if direct access works.');
  }
  let address = settings.ProxyServer.trim();
  if (!/^[a-z]+:\/\//i.test(address)) address = 'http://' + address;
  const url = new URL(address);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.search || url.hash || (url.pathname && url.pathname !== '/')) {
    throw new Error('Unsupported Windows proxy format. Set HTTP_PROXY and HTTPS_PROXY in the launch environment.');
  }
  return {HTTP_PROXY: url.origin, HTTPS_PROXY: url.origin,
          NO_PROXY: 'localhost,127.0.0.1,::1'};
}

function readWindowsProxy() {
  if (process.platform !== 'win32') throw new Error('--system-proxy is supported only on Windows.');
  const script = "$p = Get-ItemProperty -LiteralPath 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'; @{ProxyEnable=$p.ProxyEnable;ProxyServer=$p.ProxyServer} | ConvertTo-Json -Compress";
  const value = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script],
    {encoding: 'utf8', timeout: 10000, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
  return proxyEnvironment(JSON.parse(value.trim()));
}

function main(argv) {
  let root = 'F:/deepseekA', useSystemProxy = false;
  const args = [...argv];
  while (args.length) {
    if (args[0] === '--dsh-root') {
      args.shift(); root = args.shift();
      if (!root) throw new Error('--dsh-root requires a path.');
    } else if (args[0] === '--system-proxy') {
      args.shift(); useSystemProxy = true;
    } else break;
  }
  if (!args.length) {
    console.log('Usage: node scripts/run-with-dsh.cjs [--dsh-root F:/deepseekA] [--system-proxy] [--config config.dsh.json] run --project PATH --question TEXT');
    return;
  }
  const env = {...process.env, DEEPSEEK_API_KEY: loadDshKey(root)};
  if (useSystemProxy) Object.assign(env, readWindowsProxy());
  const child = spawn(process.env.DS_AGENTS_PYTHON || 'python', ['-X', 'utf8', '-B', '-m', 'ds_agents', ...args],
    {cwd: path.resolve(__dirname, '..'), env, stdio: 'inherit', shell: false, windowsHide: true});
  child.on('error', () => { console.error('Cannot launch Python; set DS_AGENTS_PYTHON to its executable path.'); process.exitCode = 2; });
  child.on('exit', code => { process.exitCode = code ?? 1; });
}

module.exports = {loadDshKey, proxyEnvironment};
if (require.main === module) {
  try { main(process.argv.slice(2)); }
  catch (_) {
    // No raw parser or process errors: YAML syntax errors may embed credential text.
    console.error('DSH bridge setup failed. Check DSH root, saved DeepSeek key and Windows proxy. No credential values were logged.');
    process.exitCode = 2;
  }
}
