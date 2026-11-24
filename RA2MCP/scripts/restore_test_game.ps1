#requires -Version 5
<#
.SYNOPSIS
  Undo scripts\prepare_test_game.ps1: restore the backed-up files in the isolated game copy.
.DESCRIPTION
  Copies every file from backups\test-game\ back into the test copy and removes the
  files that preparation added (patched spawner, bridge log). Original E:\hj is untouched.
#>
[CmdletBinding()]
param(
  [string]$TestGame = 'E:\RA2MCP\test-game',
  [string]$BackupRoot = 'E:\RA2MCP\backups\test-game'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $BackupRoot)) { throw "backup dir not found: $BackupRoot" }

$restored = New-Object System.Collections.Generic.List[string]
Get-ChildItem $BackupRoot -Recurse -File | Where-Object { $_.Name -ne 'prepared-manifest.json' } | ForEach-Object {
  $rel = $_.FullName.Substring((Resolve-Path $BackupRoot).Path.Length).TrimStart('\')
  $dst = Join-Path $TestGame $rel
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
  Copy-Item -Force $_.FullName $dst
  $restored.Add($rel)
}
foreach ($rel in 'gamemd-spawn-ra2yrcpp.exe', 'ra2yrcpp.log', 'stats.dmp') {
  $p = Join-Path $TestGame $rel
  if (Test-Path $p) { Remove-Item -Force $p; $restored.Add("removed $rel") }
}
$restored | ForEach-Object { Write-Host "[restore] $_" }
Write-Host "[restore] done ($($restored.Count) items)"
