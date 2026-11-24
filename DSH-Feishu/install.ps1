$ErrorActionPreference = 'Stop'
$sourceRoot = $PSScriptRoot
$targetRoot = 'F:\deepseekA\integrations\feishu-notify'
$profilePath = 'F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml'
$patchPath = Join-Path $sourceRoot 'cordis.patch.yml'
$configSource = 'G:\Codex_mmx\feishu-notify\config.local.json'
$encoding = New-Object System.Text.UTF8Encoding($false)

if (Test-Path -LiteralPath $targetRoot) { throw 'Integration directory already exists; refusing to overwrite.' }
$originalBytes = [System.IO.File]::ReadAllBytes($profilePath)
$originalText = [System.IO.File]::ReadAllText($profilePath)
if ($originalText.Contains('mcp-feishu-notify')) { throw 'Feishu MCP is already configured.' }
if (-not (Test-Path -LiteralPath $configSource -PathType Leaf)) { throw 'Existing Feishu configuration is missing.' }
$config = [System.IO.File]::ReadAllText($configSource) | ConvertFrom-Json
if (-not $config.webhook_url) { throw 'Existing Feishu configuration is incomplete.' }
$null = & 'C:\Program Files\Python310\python.exe' -B (Join-Path $sourceRoot 'send_message.py') --config $configSource --check
if ($LASTEXITCODE -ne 0) { throw 'Existing Feishu configuration failed validation.' }

$backup = $profilePath + '.before-feishu-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.bak'
$combined = $originalText.TrimEnd() + "`n`n# Feishu outbound notification tools`n" + [System.IO.File]::ReadAllText($patchPath)
$preview = Join-Path $sourceRoot 'profile-preview.yml'
[System.IO.File]::WriteAllText($preview, $combined, $encoding)

# Validate the exact prospective YAML without printing profile values or credentials.
$checkScript = @'
const fs = require('node:fs');
const yaml = require('F:/deepseekA-020-test/node_modules/js-yaml');
const original = yaml.load(fs.readFileSync(process.argv[1], 'utf8'));
const combined = yaml.load(fs.readFileSync(process.argv[2], 'utf8'));
if (!Array.isArray(original) || !Array.isArray(combined)) throw Error('Profile must be an array');
if (JSON.stringify(original) !== JSON.stringify(combined.slice(0, original.length))) throw Error('Existing profile changed');
const inserted = combined.slice(original.length).flatMap(row => row.insert || []);
if (inserted.length !== 1 || inserted[0].id !== 'mcp-feishu-notify') throw Error('Unexpected patch');
'@
& 'C:\nodejs\node.exe' -e $checkScript $profilePath $preview
if ($LASTEXITCODE -ne 0) { throw 'Merged profile validation failed.' }

New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
foreach ($name in @('server.mjs','send_message.py','verify.mjs','README.md','.gitignore','cordis.patch.yml')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $targetRoot $name) -ErrorAction Stop
}
Copy-Item -LiteralPath $configSource -Destination (Join-Path $targetRoot 'config.local.json') -ErrorAction Stop
# Ensure the file has not changed since the preview was validated.
$currentBytes = [System.IO.File]::ReadAllBytes($profilePath)
if ([Convert]::ToBase64String($originalBytes) -ne [Convert]::ToBase64String($currentBytes)) { throw 'Profile changed during installation; no profile update applied.' }
[System.IO.File]::WriteAllBytes($backup, $originalBytes)
$temporaryProfile = $profilePath + '.feishu-pending'
[System.IO.File]::WriteAllText($temporaryProfile, $combined, $encoding)
[System.IO.File]::Replace($temporaryProfile, $profilePath, $backup)
Write-Output 'Installed DSH Feishu notification tools without restarting DSH.'
Write-Output ('Profile backup: ' + $backup)
