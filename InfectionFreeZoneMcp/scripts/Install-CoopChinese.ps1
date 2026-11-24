param([string]$GameRoot = 'F:\SteamLibrary\steamapps\common\Infection Free Zone', [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$gamePath = (Resolve-Path -LiteralPath $GameRoot).Path.TrimEnd('\')
if (-not (Test-Path -LiteralPath (Join-Path $gamePath 'Infection Free Zone.exe'))) { throw 'Game executable not found.' }
if (Get-Process -Name 'Infection Free Zone' -ErrorAction SilentlyContinue) { throw 'Exit the game before updating Chinese UI.' }
$coop = Join-Path $gamePath 'BepInEx\plugins\IFZMultiplayer\IFZ.Multiplayer.GameIntegration.dll'
if ((Get-FileHash -LiteralPath $coop).Hash -ne 'A36C10726AA1B127BF1F06F5F6B87F974A7869CA342387ACE3CBA640C64CAD8B') { throw 'Expected the inspected RC7-P7 multiplayer mod.' }
$files = @(
    @{ Source = 'coop-zh\bin\Release\netstandard2.1\IfzCoopChinese.dll'; Relative = 'BepInEx\plugins\IfzCoopChinese\IfzCoopChinese.dll' },
    @{ Source = 'npc-enhanced\bin\Release\netstandard2.1\IfzNpcEnhanced.dll'; Relative = 'BepInEx\plugins\IfzNpcEnhanced\IfzNpcEnhanced.dll' }
)
$updates = @()
foreach ($item in $files) {
    $source = Join-Path $projectRoot $item.Source
    $target = Join-Path $gamePath $item.Relative
    if (-not (Test-Path -LiteralPath $source)) { throw "Build missing: $source" }
    $duplicate = Get-ChildItem -LiteralPath (Join-Path $gamePath 'BepInEx\plugins') -Recurse -File -Filter (Split-Path -Leaf $target) | Where-Object FullName -ne $target
    if ($duplicate) { throw "Duplicate plugin: $($duplicate.FullName)" }
    if (-not (Test-Path -LiteralPath $target) -or (Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) { $updates += $item }
}
if ($CheckOnly) { Write-Output "Chinese UI preflight passed; $($updates.Count) updates pending."; return }
if (-not $updates.Count) { Write-Output 'Chinese UI is up to date.'; return }
$backup = Join-Path $gamePath ('_coop_chinese_backup_' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,6))
New-Item -ItemType Directory -Path $backup | Out-Null
$manifest = @()
foreach ($item in $updates) {
    $target = Join-Path $gamePath $item.Relative
    $existed = Test-Path -LiteralPath $target
    if ($existed) {
        $saved = Join-Path $backup $item.Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $saved) -Force | Out-Null
        Copy-Item -LiteralPath $target -Destination $saved
    }
    $manifest += [pscustomobject]@{ relative = $item.Relative; existed = $existed }
}
ConvertTo-Json -InputObject $manifest | Set-Content -LiteralPath (Join-Path $backup 'manifest.json') -Encoding UTF8
foreach ($item in $updates) {
    $source = Join-Path $projectRoot $item.Source
    $target = Join-Path $gamePath $item.Relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
    if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) { throw "Installed hash mismatch: $target" }
}
[pscustomobject]@{ Installed = $true; Files = $updates.Count; Backup = $backup; GameStarted = $false }
