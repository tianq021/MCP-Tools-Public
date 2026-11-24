param([Parameter(Mandatory)][string]$GameRoot, [Parameter(Mandatory)][string]$BackupDirectory, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$gamePath = (Resolve-Path -LiteralPath $GameRoot).Path.TrimEnd('\')
$backupPath = (Resolve-Path -LiteralPath $BackupDirectory).Path.TrimEnd('\')
if (-not $backupPath.StartsWith($gamePath + '\_npc_enhanced_backup_', [StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must be an NPC installer backup inside this game directory.' }
if (Get-Process -Name 'Infection Free Zone' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $gamePath 'Infection Free Zone.exe') }) { throw 'Exit the game before restoring.' }
$manifest = Get-Content -LiteralPath (Join-Path $backupPath 'manifest.json') -Raw | ConvertFrom-Json
foreach ($item in $manifest) {
    $target = [IO.Path]::GetFullPath((Join-Path $gamePath $item.relative))
    $source = [IO.Path]::GetFullPath((Join-Path $backupPath $item.relative))
    if (-not $target.StartsWith($gamePath + '\', [StringComparison]::OrdinalIgnoreCase) -or
        -not $source.StartsWith($backupPath + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid backup manifest path.' }
    if ($item.existed -and -not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Backup file missing: $source" }
}
if ($CheckOnly) { Write-Output "Restore preflight passed: $($manifest.Count) files."; return }
foreach ($item in $manifest) {
    $target = [IO.Path]::GetFullPath((Join-Path $gamePath $item.relative))
    if ($item.existed) { Copy-Item -LiteralPath (Join-Path $backupPath $item.relative) -Destination $target -Force }
    elseif (Test-Path -LiteralPath $target -PathType Leaf) { Remove-Item -LiteralPath $target }
}
Write-Output 'Original files restored. Game saves and backup retained.'
