$ErrorActionPreference = 'Stop'
$sourceRoot = $PSScriptRoot
$targetRoot = 'F:\deepseekA\integrations\desktop-control'
$skillTarget = 'C:\Users\hp\.codex\skills\desktop-control'
$profilePath = 'F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml'
$encoding = New-Object System.Text.UTF8Encoding($false)
$original = [System.IO.File]::ReadAllText($profilePath)
$originalBytes = [System.IO.File]::ReadAllBytes($profilePath)
$marker = '    - id: mcp-desktop-control'
$index = $original.IndexOf($marker, [System.StringComparison]::Ordinal)
if ($index -lt 0 -or $original.IndexOf($marker, $index + $marker.Length, [System.StringComparison]::Ordinal) -ge 0) { throw 'Expected one existing desktop-control configuration.' }
if ($original.Contains('DESKTOP_CONTROL_VERSION')) { throw 'UIA version marker already exists; inspect before updating.' }
$start = $original.Substring(0, $index)
$block = $original.Substring($index)
$needle = '        failOnStartupError: false'
if (-not $block.Contains($needle)) { throw 'Expected desktop-control config was not found.' }
$combined = $start + $block.Replace($needle, $needle + "`n        env:`n          DESKTOP_CONTROL_VERSION: '1.1.0'")
$preview = Join-Path $sourceRoot 'profile-uia-preview.yml'
[System.IO.File]::WriteAllText($preview, $combined, $encoding)
$checkScript = @'
const fs=require('node:fs'), yaml=require('F:/deepseekA-020-test/node_modules/js-yaml');
const before=yaml.load(fs.readFileSync(process.argv[1],'utf8'));
const after=yaml.load(fs.readFileSync(process.argv[2],'utf8'));
const row=after.flatMap(x=>x.insert||[]).find(x=>x.id==='mcp-desktop-control');
if(row?.config?.env?.DESKTOP_CONTROL_VERSION!=='1.1.0')throw Error('Missing version marker');
delete row.config.env;
if(JSON.stringify(before)!==JSON.stringify(after))throw Error('Unexpected profile change');
'@
& 'C:\nodejs\node.exe' -e $checkScript $profilePath $preview
if ($LASTEXITCODE -ne 0) { throw 'Profile upgrade verification failed.' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backupRoot = Join-Path $targetRoot ('backup-before-uia-' + $stamp)
New-Item -ItemType Directory -Path $backupRoot -ErrorAction Stop | Out-Null
foreach ($name in @('server.mjs','client.mjs','DesktopControl.cs','DesktopControl.exe','verify.mjs','README.md','cordis.patch.yml')) {
    Copy-Item -LiteralPath (Join-Path $targetRoot $name) -Destination (Join-Path $backupRoot $name) -ErrorAction Stop
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $targetRoot $name) -ErrorAction Stop
}
Copy-Item -LiteralPath (Join-Path $skillTarget 'SKILL.md') -Destination (Join-Path $backupRoot 'codex-SKILL.md') -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $skillTarget 'agents\openai.yaml') -Destination (Join-Path $backupRoot 'codex-openai.yaml') -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $sourceRoot 'codex-skill\SKILL.md') -Destination (Join-Path $skillTarget 'SKILL.md') -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $sourceRoot 'codex-skill\agents\openai.yaml') -Destination (Join-Path $skillTarget 'agents\openai.yaml') -ErrorAction Stop
if ([Convert]::ToBase64String($originalBytes) -ne [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($profilePath))) { throw 'Profile changed during upgrade; no profile update applied.' }
$pending = $profilePath + '.uia-pending'
$backup = $profilePath + '.before-uia-' + $stamp + '.bak'
[System.IO.File]::WriteAllText($pending, $combined, $encoding)
[System.IO.File]::Replace($pending, $profilePath, $backup)
Write-Output 'Upgraded desktop control to native UI Automation with optional screenshots.'
Write-Output ('Backup: ' + $backupRoot)
