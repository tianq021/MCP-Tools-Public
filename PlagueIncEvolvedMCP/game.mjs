import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';
import copies from './双版本支持/installations.json' with { type: 'json' };

const run = promisify(execFile);
const appId = '246620';
const executableName = 'PlagueIncEvolved.exe';

async function optionalRead(file, encoding = 'utf8') {
  try {
    return await readFile(file, { encoding });
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null;
    throw error;
  }
}

async function hasExecutable(directory) {
  try {
    return (await stat(path.join(directory, executableName))).isFile();
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return false;
    throw error;
  }
}

// Steam stores these scalar fields one pair per line in its local ACF and VDF files.
function values(text, field) {
  const pairs = /^\s*"([^"]+)"[ \t]+"((?:\\.|[^"\\])*)"[ \t]*\r?$/gm;
  return Array.from(text.matchAll(pairs))
    .filter(match => match[1] === field)
    .map(match => match[2].replace(/\\(["\\])/g, '$1'));
}

export async function findInstallation(steamRoot, override = null) {
  const libraries = new Set();
  if (steamRoot) {
    libraries.add(steamRoot);
    const config = await optionalRead(path.join(steamRoot, 'steamapps', 'libraryfolders.vdf'));
    for (const library of values(config ?? '', 'path')) libraries.add(library);
  }
  const selected = override && path.resolve(override);
  for (const library of libraries) {
    const apps = path.join(library, 'steamapps');
    const manifest = await optionalRead(path.join(apps, 'appmanifest_' + appId + '.acf'));
    if (!manifest || values(manifest, 'appid')[0] !== appId) continue;
    const folder = values(manifest, 'installdir')[0];
    if (!folder) continue;
    const directory = path.resolve(apps, 'common', folder);
    if (selected && path.relative(selected, directory) !== '') continue;
    if (await hasExecutable(directory)) {
      return { installed: true, directory, buildId: values(manifest, 'buildid')[0] ?? null, source: 'steam' };
    }
  }
  if (selected) {
    if (!await hasExecutable(selected)) throw new Error('PLAGUE_GAME_DIR must contain ' + executableName);
    return { installed: true, directory: selected, buildId: null, source: 'configured' };
  }
  return { installed: false, directory: null, buildId: null, source: null };
}

async function snapshot() {
  if (process.platform !== 'win32') throw new Error('This local server requires Windows.');
  const shell = path.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const query = [
    "$ErrorActionPreference = 'Stop'",
    '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)',
    "$steam = Get-ItemPropertyValue -LiteralPath 'HKCU:\\Software\\Valve\\Steam' -Name SteamPath -ErrorAction SilentlyContinue",
    "$games = @(Get-Process -Name 'PlagueIncEvolved' -ErrorAction SilentlyContinue | ForEach-Object { @{pid=$_.Id; executable=$_.Path} })",
    '@{steamRoot=$steam; processes=$games} | ConvertTo-Json -Compress -Depth 3'
  ].join('\n');
  const { stdout } = await run(shell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', query],
    { windowsHide: true, timeout: 10000, encoding: 'utf8' });
  const snapshot = JSON.parse(stdout.trim());
  const steamRoot = process.env.PLAGUE_STEAM_ROOT || snapshot.steamRoot ||
    path.join(process.env['ProgramFiles(x86)'] ?? 'C:\\Program Files (x86)', 'Steam');
  return { steamRoot, processes: snapshot.processes };
}

async function versions(installation) {
  if (installation.installed) {
    const file = path.join(installation.directory, 'PlagueIncEvolved_Data', 'globalgamemanagers');
    const bytes = await optionalRead(file, null);
    if (bytes) {
      const key = createHash('sha256').update(bytes).digest('hex');
      if (copies.versionsBySettingsHash[key]) return copies.versionsBySettingsHash[key];
    }
  }
  return { gameVersion: null, unityVersion: null };
}

export async function inspectCopies(steamRoot, localDirectory, processes) {
  const steam = await findInstallation(steamRoot);
  const directory = path.resolve(localDirectory);
  const local = steam.directory && path.relative(steam.directory, directory) === ''
    ? steam
    : { installed: await hasExecutable(directory), directory, buildId: null, source: 'configured' };
  const installations = [];
  for (const [id, copy] of Object.entries({ steam, local })) {
    const exe = copy.directory && path.join(copy.directory, executableName);
    const matched = processes.filter(p => exe && p.executable && path.relative(exe, p.executable) === '');
    const unknown = copy.installed && processes.some(p => !p.executable);
    const running = matched.length > 0 ? true : unknown ? null : false;
    installations.push({ id, ...copy, ...await versions(copy), running, processes: matched });
  }
  return { appId, installations };
}

export async function listInstallations() {
  const { steamRoot, processes } = await snapshot();
  return inspectCopies(steamRoot, process.env.PLAGUE_LOCAL_DIR || copies.localDirectory, processes);
}

export async function getStatus(installationId) {
  if (installationId) {
    const { installations } = await listInstallations();
    const selected = installations.find(copy => copy.id === installationId);
    if (!selected) throw new Error('Unknown installation: ' + installationId);
    return { appId, ...selected };
  }
  const { steamRoot, processes } = await snapshot();
  const installation = await findInstallation(steamRoot, process.env.PLAGUE_GAME_DIR);
  return { appId, ...installation, ...await versions(installation), running: processes.length > 0, processes };
}
