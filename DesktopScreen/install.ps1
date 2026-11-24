$ErrorActionPreference = 'Stop'
$sourceRoot = $PSScriptRoot
$targetRoot = 'F:\deepseekA\integrations\desktop-screen'
$skillTarget = 'C:\Users\hp\.codex\skills\desktop-screen'
$profilePath = 'F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml'
$encoding = New-Object System.Text.UTF8Encoding($false)
if (Test-Path -LiteralPath $targetRoot) { throw 'Screen integration already exists; refusing to overwrite.' }
if (Test-Path -LiteralPath $skillTarget) { throw 'Codex screen skill already exists; refusing to overwrite.' }
$originalBytes = [System.IO.File]::ReadAllBytes($profilePath)
$original = [System.IO.File]::ReadAllText($profilePath)
if ($original.Contains('mcp-desktop-screen')) { throw 'Screen MCP is already configured.' }
$combined = $original.TrimEnd() + "`n`n# On-demand desktop screenshots`n" + [System.IO.File]::ReadAllText((Join-Path $sourceRoot 'cordis.patch.yml'))
$preview = Join-Path $sourceRoot 'profile-preview.yml'
[System.IO.File]::WriteAllText($preview, $combined, $encoding)
$checkScript = @'
const fs = require('node:fs');
const yaml = require('F:/deepseekA-020-test/node_modules/js-yaml');
const old = yaml.load(fs.readFileSync(process.argv[1], 'utf8'));
const next = yaml.load(fs.readFileSync(process.argv[2], 'utf8'));
if (!Array.isArray(old) || !Array.isArray(next) || JSON.stringify(old) !== JSON.stringify(next.slice(0, old.length))) throw Error('Existing profile changed');
const additions = next.slice(old.length).flatMap(x => x.insert || []);
if (additions.length !== 1 || additions[0].id !== 'mcp-desktop-screen') throw Error('Unexpected new configuration');
'@
& 'C:\nodejs\node.exe' -e $checkScript $profilePath $preview
if ($LASTEXITCODE -ne 0) { throw 'Merged profile validation failed.' }
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
foreach ($name in @('server.mjs','screen_tools.py','CaptureScreen.cs','CaptureScreen.exe','config.json','cordis.patch.yml','verify.mjs','README.md','.gitignore')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $targetRoot $name) -ErrorAction Stop
}
New-Item -ItemType Directory -Path $skillTarget,(Join-Path $skillTarget 'agents') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot 'codex-skill\SKILL.md') -Destination (Join-Path $skillTarget 'SKILL.md') -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $sourceRoot 'codex-skill\agents\openai.yaml') -Destination (Join-Path $skillTarget 'agents\openai.yaml') -ErrorAction Stop
if ([Convert]::ToBase64String($originalBytes) -ne [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($profilePath))) { throw 'Profile changed during installation; no configuration applied.' }
$backup = $profilePath + '.before-desktop-screen-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.bak'
$pending = $profilePath + '.desktop-screen-pending'
[System.IO.File]::WriteAllText($pending, $combined, $encoding)
[System.IO.File]::Replace($pending, $profilePath, $backup)
Write-Output 'Installed DSH desktop screenshot MCP and Codex desktop-screen skill.'
Write-Output ('Profile backup: ' + $backup)
