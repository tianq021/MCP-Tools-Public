#requires -Version 5
<#
.SYNOPSIS
  Prepare the isolated game copy (E:\RA2MCP\test-game) for a read-only ra2yrcpp probe run.
.DESCRIPTION
  Idempotent, backed-up preparation:
    1. backs up every file that is replaced (backups\test-game\),
    2. builds gamemd-spawn-ra2yrcpp.exe = legacy CnCNet5 spawner (yr-patches, non-hardened)
       patched with the ra2yrcpp loader (LoadLibrary libra2yrcpp.dll + init_iservice),
    3. installs the freshly built x86 bridge (build\pkg\bin\libra2yrcpp.dll) and drops the
       loopback-only ra2yrcpp.json,
    4. writes an offline 1-human/1-AI skirmish spawn.ini plus spawnmap.ini (copy of a stock
       2-player map from the test copy).
  E:\hj (the original installation) is never read or written. Ares/Phobos are not loaded:
  the patched spawner is started directly, not through Syringe.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\prepare_test_game.ps1
#>
[CmdletBinding()]
param(
  [string]$TestGame = 'E:\RA2MCP\test-game',
  [string]$Repo = 'E:\RA2MCP\vendor\ra2yrcpp',
  [string]$Pkg = 'E:\RA2MCP\build\pkg',
  [string]$BackupRoot = 'E:\RA2MCP\backups\test-game',
  [string]$SpawnerInput = 'E:\RA2MCP\vendor\spawner-patches\release\cncnet\gamemd-spawn.exe',
  [string]$Python = 'E:\RA2MCP\.venv\Scripts\python.exe',
  [string]$MapSource = "E:\RA2MCP\test-game\Maps\Yuri's Revenge\2peaks.map",
  [string]$SpawnIniTemplate = 'E:\RA2MCP\config\spawn.ini',
  [switch]$SkipSpawnerPatch
)

$ErrorActionPreference = 'Stop'
function Log($m) { Write-Host "[prepare] $m" }

$TestGame = (Resolve-Path $TestGame).Path
$Repo = (Resolve-Path $Repo).Path
$Pkg = (Resolve-Path $Pkg).Path
if (-not (Test-Path $BackupRoot)) { New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null }
$BackupRoot = (Resolve-Path $BackupRoot).Path

$manifest = [ordered]@{
  prepared_at = (Get-Date).ToString('o')
  test_game = $TestGame
  backups = $BackupRoot
  backups_list = @()
  files = @()
}

$backupList = New-Object System.Collections.Generic.List[object]
$fileList = New-Object System.Collections.Generic.List[object]

function Backup-File([string]$relPath) {
  $src = Join-Path $TestGame $relPath
  if (-not (Test-Path $src)) { Log "skip backup (absent): $relPath"; return }
  $dst = Join-Path $BackupRoot $relPath
  if (Test-Path $dst) { Log "backup exists, keep original: $relPath"; return }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
  Copy-Item -Force $src $dst
  $h = (Get-FileHash $src -Algorithm SHA256).Hash
  Log "backed up: $relPath  sha256=$h"
  $backupList.Add([ordered]@{ path = $relPath; sha256 = $h })
}

# --- 1. backups of every file we replace or add over -------------------------
foreach ($f in 'libra2yrcpp.dll', 'ra2yrcpp.json', 'spawn.ini', 'spawnmap.ini', 'gamemd-spawn.exe', 'RA2MD.ini') {
  Backup-File $f
}

# --- 2. prerequisites --------------------------------------------------------
$builtDll = Join-Path $Pkg 'bin\libra2yrcpp.dll'
$addscn = Join-Path $Pkg 'bin\addscn.exe'
if (-not (Test-Path $builtDll)) { throw "built DLL missing: $builtDll (run scripts\build_ra2yrcpp_docker.ps1 first)" }
if (-not (Test-Path $addscn)) { throw "addscn.exe missing: $addscn" }
if (-not (Test-Path $Python)) { throw "python missing: $Python" }
if (-not (Test-Path $SpawnerInput)) { throw "spawner input missing: $SpawnerInput" }
if (-not (Test-Path $MapSource)) { throw "map missing: $MapSource" }

# --- 3. patched spawner exe (legacy loader calls init_iservice) --------------
$patchedExe = Join-Path $TestGame 'gamemd-spawn-ra2yrcpp.exe'
if (-not $SkipSpawnerPatch) {
  Log "patching spawner: $SpawnerInput -> $patchedExe"
  Push-Location $Repo
  try {
    & $Python (Join-Path $Repo 'scripts\patch_gamemd.py') --auto-patch `
      --build-dir (Join-Path $Pkg 'bin') `
      --input $SpawnerInput `
      --output $patchedExe
    if ($LASTEXITCODE -ne 0) { throw "patch_gamemd.py failed: $LASTEXITCODE" }
  } finally { Pop-Location }
}
if (-not (Test-Path $patchedExe)) { throw "patched spawner not produced: $patchedExe" }

# --- 4. bridge DLL + loopback-only config ------------------------------------
Copy-Item -Force $builtDll (Join-Path $TestGame 'libra2yrcpp.dll')
$json = '{"port":14521,"allowedHostsRegex":"127\\.0\\.0\\.1","singleStep":false,"maxConnections":4,"logFilename":"ra2yrcpp.log"}'
Set-Content -Path (Join-Path $TestGame 'ra2yrcpp.json') -Value $json -Encoding ASCII -NoNewline
Log "installed built DLL + loopback ra2yrcpp.json (singleStep=false)"

# --- 5. offline skirmish spawn.ini / spawnmap.ini ---------------------------
Copy-Item -Force $SpawnIniTemplate (Join-Path $TestGame 'spawn.ini')
Copy-Item -Force $MapSource (Join-Path $TestGame 'spawnmap.ini')
Log "wrote spawn.ini (1 human + 1 AI, offline) and spawnmap.ini from $(Split-Path -Leaf $MapSource)"

# --- 6. manifest -------------------------------------------------------------
foreach ($rel in 'libra2yrcpp.dll', 'ra2yrcpp.json', 'spawn.ini', 'spawnmap.ini', 'gamemd-spawn-ra2yrcpp.exe') {
  $p = Join-Path $TestGame $rel
  $fileList.Add([ordered]@{
    path = $rel
    bytes = (Get-Item $p).Length
    sha256 = (Get-FileHash $p -Algorithm SHA256).Hash
  })
}
$manifest.backups_list = $backupList
$manifest.files = $fileList
$manifestPath = Join-Path $BackupRoot 'prepared-manifest.json'
($manifest | ConvertTo-Json -Depth 6) | Set-Content -Path $manifestPath -Encoding UTF8
Log "manifest: $manifestPath"
Log "done. restore with scripts\restore_test_game.ps1"
