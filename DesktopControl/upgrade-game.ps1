param([switch]$PreviewOnly)
$ErrorActionPreference = 'Stop'
$sourceRoot = $PSScriptRoot
$targetRoot = 'F:\deepseekA\integrations\desktop-control'
$skillTarget = 'C:\Users\hp\.codex\skills\desktop-control'
$profilePath = 'F:\deepseekA\dsh-home-020-20261007\profiles\web\cordis.patch.yml'
$python = 'C:\Program Files\Python310\python.exe'
$encoding = New-Object System.Text.UTF8Encoding($false)
$originalBytes = [System.IO.File]::ReadAllBytes($profilePath)
$original = [System.Text.Encoding]::UTF8.GetString($originalBytes).TrimStart([char]0xFEFF)
$marker = '    - id: mcp-desktop-control'
$index = $original.IndexOf($marker, [System.StringComparison]::Ordinal)
if ($index -lt 0 -or $original.IndexOf($marker, $index + $marker.Length, [System.StringComparison]::Ordinal) -ge 0) { throw 'Expected one desktop-control profile entry.' }
$end = $original.IndexOf(([string][char]10 + '- '), $index, [System.StringComparison]::Ordinal)
if ($end -lt 0) { $end = $original.Length }
$block = $original.Substring($index, $end - $index)
$updated = [regex]::Replace($block, '(?m)^        toolCallTimeoutMs: \d+\r?$', '        toolCallTimeoutMs: 40000')
$updated = [regex]::Replace($updated, "(?m)^          DESKTOP_CONTROL_VERSION: '[^']+'\r?$", "          DESKTOP_CONTROL_VERSION: '2.0.0'")
if (-not $updated.Contains("DESKTOP_CONTROL_VERSION: '2.0.0'")) { throw 'Version marker missing.' }
$combined = $original.Substring(0, $index) + $updated + $original.Substring($end)
$preview = Join-Path $sourceRoot 'profile-game-preview.yml'
[System.IO.File]::WriteAllText($preview, $combined, $encoding)
$verifyProfile = @'
const fs=require('node:fs'),yaml=require('F:/deepseekA/node_modules/js-yaml');
const before=yaml.load(fs.readFileSync(process.argv[1],'utf8'));
const after=yaml.load(fs.readFileSync(process.argv[2],'utf8'));
const find=doc=>doc.flatMap(x=>x.insert||[]).find(x=>x.id==='mcp-desktop-control');
const old=find(before),next=find(after);
if(next.config.env.DESKTOP_CONTROL_VERSION!=='2.0.0'||next.config.toolCallTimeoutMs!==40000)throw Error('Upgrade fields missing');
next.config.env.DESKTOP_CONTROL_VERSION=old.config.env.DESKTOP_CONTROL_VERSION;
next.config.toolCallTimeoutMs=old.config.toolCallTimeoutMs;
if(JSON.stringify(before)!==JSON.stringify(after))throw Error('Unrelated profile changes');
'@
& 'C:\nodejs\node.exe' -e $verifyProfile $profilePath $preview
if ($LASTEXITCODE -ne 0) { throw 'Profile validation failed.' }
$packages = Join-Path $sourceRoot 'realtime\.venv\Lib\site-packages'
foreach ($name in @('dxcam', 'comtypes', 'dxcam-0.3.0.dist-info', 'comtypes-1.4.17.dist-info')) {
    if (-not (Test-Path -LiteralPath (Join-Path $packages $name))) { throw ('Missing existing dependency: ' + $name) }
}
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $packages
    & $python -B -c "import ctypes,cv2,numpy,dxcam,comtypes; print('Desktop runtime dependencies OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Python dependencies unavailable.' }
} finally { $env:PYTHONPATH = $previousPythonPath }
if ($PreviewOnly) { Write-Output 'Preview verified: only desktop-control version and timeout change. No installed files modified.'; return }
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$wpf = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\WPF'
& $csc /nologo /target:exe ("/out:" + (Join-Path $sourceRoot 'DesktopControl.exe')) /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll ("/reference:" + (Join-Path $wpf 'UIAutomationClient.dll')) ("/reference:" + (Join-Path $wpf 'UIAutomationTypes.dll')) ("/reference:" + (Join-Path $wpf 'WindowsBase.dll')) (Join-Path $sourceRoot 'DesktopControl.cs')
if ($LASTEXITCODE -ne 0) { throw 'Native controller compilation failed.' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backupRoot = Join-Path $targetRoot ('backup-before-game-' + $stamp)
New-Item -ItemType Directory -Path $backupRoot -ErrorAction Stop | Out-Null
$files = @('server.mjs','realtime.mjs','client.mjs','DesktopControl.cs','DesktopControl.exe','verify.mjs','README.md','cordis.patch.yml','.gitignore')
foreach ($name in $files) {
    $existing = Join-Path $targetRoot $name
    if (Test-Path -LiteralPath $existing) { Copy-Item -LiteralPath $existing -Destination (Join-Path $backupRoot $name) -ErrorAction Stop }
}
Copy-Item -LiteralPath (Join-Path $skillTarget 'SKILL.md') -Destination (Join-Path $backupRoot 'codex-SKILL.md') -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $skillTarget 'agents\openai.yaml') -Destination (Join-Path $backupRoot 'codex-openai.yaml') -ErrorAction Stop
$runtimeTarget = Join-Path $targetRoot 'realtime'
if (Test-Path -LiteralPath $runtimeTarget) { Copy-Item -LiteralPath $runtimeTarget -Destination (Join-Path $backupRoot 'realtime') -Recurse -ErrorAction Stop }
foreach ($name in $files) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $name) -Destination (Join-Path $targetRoot $name) -Force -ErrorAction Stop
}
New-Item -ItemType Directory -Path $runtimeTarget,(Join-Path $runtimeTarget 'vendor') -Force | Out-Null
foreach ($name in @('actions.py','engine.py','native.py','runner.py','watchdog.py','requirements.txt')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot ('realtime\' + $name)) -Destination (Join-Path $runtimeTarget $name) -Force -ErrorAction Stop
}
foreach ($name in @('dxcam', 'comtypes', 'dxcam-0.3.0.dist-info', 'comtypes-1.4.17.dist-info')) {
    Copy-Item -LiteralPath (Join-Path $packages $name) -Destination (Join-Path $runtimeTarget 'vendor') -Recurse -Force -ErrorAction Stop
}
Copy-Item -LiteralPath (Join-Path $sourceRoot 'codex-skill\SKILL.md') -Destination (Join-Path $skillTarget 'SKILL.md') -Force -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $sourceRoot 'codex-skill\agents\openai.yaml') -Destination (Join-Path $skillTarget 'agents\openai.yaml') -Force -ErrorAction Stop
if ([Convert]::ToBase64String($originalBytes) -ne [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($profilePath))) { throw 'DSH profile changed during upgrade. Runtime files backed up; profile not replaced.' }
if ($combined -ne $original) {
    $pending = $profilePath + '.game-pending'
    [System.IO.File]::WriteAllText($pending, $combined, $encoding)
    [System.IO.File]::Replace($pending, $profilePath, ($profilePath + '.before-game-' + $stamp + '.bak'))
}
Write-Output 'Installed desktop-control 2.0.0 for DSH and Codex.'
Write-Output ('Backup: ' + $backupRoot)
