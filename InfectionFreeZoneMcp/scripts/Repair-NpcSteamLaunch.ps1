param(
 [Parameter(Mandatory)][string]$GameRoot,
 [Parameter(Mandatory)][string]$BackupDirectory,
 [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$gamePath = (Resolve-Path -LiteralPath $GameRoot).Path.TrimEnd('\')
$backupPath = (Resolve-Path -LiteralPath $BackupDirectory).Path.TrimEnd('\')
if (-not $backupPath.StartsWith($gamePath + '\_npc_enhanced_backup_', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected backup location.' }
if (Get-Process -Name 'Infection Free Zone' -ErrorAction SilentlyContinue) { throw 'Close the game/error dialog before repairing.' }
$relative = 'Infection Free Zone_Data\Plugins\x86_64\steam_api64.dll'
$source = Join-Path $backupPath $relative
$target = Join-Path $gamePath $relative
$appid = Join-Path $gamePath 'steam_appid.txt'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Original Steam API backup missing.' }
$manifest = Get-Content -LiteralPath (Join-Path $backupPath 'manifest.json') -Raw | ConvertFrom-Json
if (-not ($manifest | Where-Object { $_.relative -eq $relative -and $_.existed })) { throw 'Backup does not record the original Steam API.' }
if (Test-Path -LiteralPath $appid) {
 if ((Get-Content -LiteralPath $appid -Raw).Trim() -ne '480') { throw 'AppID was changed separately; stopping to preserve it.' }
 if (-not ($manifest | Where-Object { $_.relative -eq 'steam_appid.txt' -and -not $_.existed })) { throw 'AppID was not introduced by this installer.' }
}
if ($CheckOnly) { Write-Output 'Preflight passed: original Steam API available; 480 override is installer-created.'; return }
$repairBackup = Join-Path $gamePath ('_npc_steam_launch_backup_' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $repairBackup | Out-Null
Copy-Item -LiteralPath $target -Destination (Join-Path $repairBackup 'coop-steam_api64.dll')
Copy-Item -LiteralPath $source -Destination $target -Force
if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) { throw 'Steam API restore verification failed.' }
if (Test-Path -LiteralPath $appid) { Move-Item -LiteralPath $appid -Destination (Join-Path $repairBackup 'steam_appid.txt') }
Write-Output "Restored official Steam API and removed AppID 480 override. Plugins retained. Repair backup: $repairBackup"
