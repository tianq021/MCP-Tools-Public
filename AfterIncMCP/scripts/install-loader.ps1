param([string]$GameRoot = 'F:\SteamLibrary\steamapps\common\After Inc')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $projectRoot '.tools\bepinex'
if (-not (Test-Path -LiteralPath (Join-Path $GameRoot 'GameAssembly.dll'))) { throw 'Game not found' }
if (Get-Process -Name 'After Inc. Revival' -ErrorAction SilentlyContinue) { throw 'Close the game before installing' }

function Get-RelativePath([string]$base, [string]$full) {
    $prefix = $base.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Not under source root: $full" }
    return $full.Substring($prefix.Length)
}

$files = @(Get-ChildItem -LiteralPath $sourceRoot -File -Recurse)
foreach ($file in $files) {
    $relative = Get-RelativePath $sourceRoot $file.FullName
    if (Test-Path -LiteralPath (Join-Path $GameRoot $relative)) { throw "Existing loader file; refusing to overwrite: $relative" }
}
$backupRoot = Join-Path $projectRoot ('backups\before-loader-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$localSave = Join-Path $env:USERPROFILE 'AppData\LocalLow\Ndemic Creations\After Inc. Revival'
if (Test-Path -LiteralPath $localSave) { Copy-Item -LiteralPath $localSave -Destination (Join-Path $backupRoot 'local-save') -Recurse }
$steamRoot = (Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam').SteamPath
foreach ($userDir in Get-ChildItem -LiteralPath (Join-Path $steamRoot 'userdata') -Directory) {
    $appSave = Join-Path $userDir.FullName '3337140'
    if (Test-Path -LiteralPath $appSave) { Copy-Item -LiteralPath $appSave -Destination (Join-Path $backupRoot ('steam-save-' + $userDir.Name)) -Recurse }
}
$installed = @()
foreach ($file in $files) {
    $relative = Get-RelativePath $sourceRoot $file.FullName
    $target = Join-Path $GameRoot $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target
    $installed += @{ path = $relative; sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash }
}
@{ game_root = $GameRoot; files = $installed } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backupRoot 'loader-manifest.json') -Encoding utf8
$version = Split-Path -Leaf $sourceRoot
Write-Output "Installed BepInEx from $sourceRoot. Backup and added-file manifest: $backupRoot"
