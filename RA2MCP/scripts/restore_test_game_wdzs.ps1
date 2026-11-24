#requires -Version 5
<#
.SYNOPSIS
  Undo scripts\prepare_test_game_wdzs.ps1 for the isolated wdzs_TEST copy.
.DESCRIPTION
  Restores the original GameMD.exe into E:\RA2MCP\test-game-wdzs and removes the
  bridge artifacts that preparation added. E:\hj is never touched.
#>
[CmdletBinding()]
param(
  [string]$TestGame = 'E:\RA2MCP\test-game-wdzs',
  [string]$BackupRoot = 'E:\RA2MCP\backups\test-game-wdzs'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $BackupRoot)) { throw "backup dir not found: $BackupRoot" }
$bakExe = Join-Path $BackupRoot 'GameMD.exe'
if (-not (Test-Path $bakExe)) { throw "original GameMD.exe backup missing: $bakExe" }

$restored = New-Object System.Collections.Generic.List[string]
Copy-Item $bakExe (Join-Path $TestGame 'GameMD.exe') -Force
$restored.Add('GameMD.exe')
foreach ($rel in 'libra2yrcpp.dll', 'ra2yrcpp.json', 'ra2yrcpp.log', 'GameMD.original.exe') {
  $p = Join-Path $TestGame $rel
  if (Test-Path $p) { Remove-Item $p -Force; $restored.Add("removed $rel") }
}
$restored | ForEach-Object { Write-Host "[restore-wdzs] $_" }
Write-Host "[restore-wdzs] done ($($restored.Count) items)"
