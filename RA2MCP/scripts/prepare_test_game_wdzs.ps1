#requires -Version 5
<#
.SYNOPSIS
  Prepare an isolated copy of E:\hj\wdzs_TEST (CnCNet "无敌之师" NPatch mod + cnc-ddraw
  sources) and inject the loopback-only ra2yrcpp bridge into the copy.
.DESCRIPTION
  E:\hj is the original installation and is treated as READ-ONLY: this script only
  robocopy's it into E:\RA2MCP\test-game-wdzs and patches the copy.

  Compatibility notes measured on this mod build (GameMD.exe, YR 1.11 + NPatch,
  5943296 bytes / 8 sections, extra .pd .pdata .pdrw .cncvk):
    * all six ra2yrcpp hook sites AND the loader detour site 0x7cd80f are byte-identical
      to a clean YR 1.001 gamemd, and IAT slots 0x7e1220/0x7e1250 are
      LoadLibraryA/GetProcAddress -> loader patch is address-compatible;
    * GameMD.exe has launcher copy-protection handshake: it must be started through
      Yuri.exe (launcher), otherwise it exits with "Launcher not running....Bail!";
    * the mod ships an ordinal-only Wsock32.dll proxy without named exports, so the
      bridge DLL must not import named functions from WSOCK32.dll (handled by the
      build-side CMake link-order fix; see reports/dsh-handoff.md).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\prepare_test_game_wdzs.ps1
#>
[CmdletBinding()]
param(
  [string]$SourceDir = 'E:\hj\wdzs_TEST',
  [string]$TestGame = 'E:\RA2MCP\test-game-wdzs',
  [string]$Repo = 'E:\RA2MCP\vendor\ra2yrcpp',
  [string]$Pkg = 'E:\RA2MCP\build\pkg',
  [string]$BackupRoot = 'E:\RA2MCP\backups\test-game-wdzs',
  [string]$Python = 'E:\RA2MCP\.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'
function Log($m) { Write-Host "[prepare-wdzs] $m" }

# Measured PE section map of GameMD.exe. Format: name:size:VA:file-offset
# (VA = image base 0x400000 + RVA). .p_text2 is appended by addscn.exe.
$ModSections = @(
  '.text:0x3df38d:0x401000:0x1000',
  '.rdata:0x30074:0x7e1000:0x3e1000',
  '.data:0x367be4:0x812000:0x412000',
  '.rsrc:0x184dc:0xb7a000:0x47e000',
  '.pd:0x10000:0xb93000:0x497000',
  '.pdata:0x4000:0xba3000:0x4a7000',
  '.pdrw:0x80000:0xba7000:0x4ab000',
  '.cncvk:0x80000:0xc27000:0x52b000'
)

$SourceDir = (Resolve-Path $SourceDir).Path
$Repo = (Resolve-Path $Repo).Path
$Pkg = (Resolve-Path $Pkg).Path
foreach ($p in (Join-Path $Pkg 'bin\libra2yrcpp.dll'), (Join-Path $Pkg 'bin\addscn.exe'), $Python) {
  if (-not (Test-Path $p)) { throw "missing prerequisite: $p" }
}

if (-not (Test-Path $BackupRoot)) { New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null }
$BackupRoot = (Resolve-Path $BackupRoot).Path

# --- 1. isolated copy (E:\hj is never written) ------------------------------
if (-not (Test-Path $TestGame)) {
  Log "copying $SourceDir -> $TestGame (read-only source)"
  New-Item -ItemType Directory -Force -Path $TestGame | Out-Null
  robocopy $SourceDir $TestGame /E /NFL /NDL /NJH /NJS /R:1 /W:1 /XD '.git' | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $LASTEXITCODE" }
} else {
  Log "test copy already exists: $TestGame"
}
$TestGame = (Resolve-Path $TestGame).Path

# --- 2. backup the original mod exe ----------------------------------------
$origExe = Join-Path $TestGame 'GameMD.original.exe'
$bakExe = Join-Path $BackupRoot 'GameMD.exe'
if (-not (Test-Path $bakExe)) {
  $srcExe = if (Test-Path $origExe) { $origExe } else { Join-Path $TestGame 'GameMD.exe' }
  Copy-Item $srcExe $bakExe
  Log "backed up original GameMD.exe -> $bakExe"
}
$origHash = (Get-FileHash $bakExe -Algorithm SHA256).Hash
Log "original GameMD.exe sha256=$origHash"

# --- 3. patch a copy of GameMD.exe with the ra2yrcpp loader ----------------
$tmp = Join-Path $TestGame 'GameMD.patch-tmp.exe'
Copy-Item $bakExe $tmp -Force

$addscn = Join-Path $Pkg 'bin\addscn.exe'
$out = & $addscn $tmp '.p_text2' '0x1000' '0x60000020'
$peInfo = ($out | Select-Object -Last 1).ToString().Trim()
Log "addscn: $peInfo"
if ($peInfo -notmatch '^\.p_text2:0x1000:(0x[0-9a-f]+):(0x[0-9a-f]+)$') { throw "unexpected addscn output: $peInfo" }
$detourVa = $Matches[1]

$args = @((Join-Path $Repo 'scripts\patch_gamemd.py')) + @('--raw', 'data/patches.txt', '--input', $tmp, '-o', (Join-Path $TestGame 'GameMD.exe'), '-d', $detourVa)
foreach ($s in $ModSections) { $args += @('-s', $s) }
$args += @('-s', $peInfo)
Push-Location $Repo
try {
  & $Python @args
  if ($LASTEXITCODE -ne 0) { throw "patch_gamemd.py failed: $LASTEXITCODE" }
} finally { Pop-Location }
Remove-Item $tmp -Force -ErrorAction SilentlyContinue

# --- 4. verify detour bytes and the untouched launcher-protection areas ----
$b = [IO.File]::ReadAllBytes((Join-Path $TestGame 'GameMD.exe'))
$site = 0x3cd80f  # file offset of VA 0x7cd80f (.text file ptr 0x1000, VA 0x401000)
$siteBytes = (($b[$site], $b[$site + 1], $b[$site + 2], $b[$site + 3], $b[$site + 4], $b[$site + 5]) | ForEach-Object { '{0:x2}' -f $_ }) -join ' '
Log "detour site bytes: $siteBytes (expect 68 xx xx xx xx c3)"
if ($siteBytes -notmatch '^68 .+ c3$') { throw "detour not applied at 0x7cd80f" }

# --- 5. bridge DLL + loopback-only config ----------------------------------
Copy-Item (Join-Path $Pkg 'bin\libra2yrcpp.dll') (Join-Path $TestGame 'libra2yrcpp.dll') -Force
$json = '{"port":14521,"allowedHostsRegex":"127\\.0\\.0\\.1","singleStep":false,"maxConnections":4,"logFilename":"ra2yrcpp.log"}'
Set-Content -Path (Join-Path $TestGame 'ra2yrcpp.json') -Value $json -Encoding ASCII -NoNewline

$manifest = [ordered]@{
  prepared_at = (Get-Date).ToString('o')
  source = $SourceDir
  test_copy = $TestGame
  original_GameMD_sha256 = $origHash
  original_GameMD_bytes = (Get-Item $bakExe).Length
  patched_GameMD_bytes = (Get-Item (Join-Path $TestGame 'GameMD.exe')).Length
  patched_GameMD_sha256 = (Get-FileHash (Join-Path $TestGame 'GameMD.exe') -Algorithm SHA256).Hash
  bridge_dll_bytes = (Get-Item (Join-Path $TestGame 'libra2yrcpp.dll')).Length
  bridge_dll_sha256 = (Get-FileHash (Join-Path $TestGame 'libra2yrcpp.dll') -Algorithm SHA256).Hash
  detour_section_va = $detourVa
  launch_command = 'Yuri.exe -SPEEDCONTROL   (launcher handshake is required by GameMD.exe copy protection)'
  e_hj_modified = $false
}
($manifest | ConvertTo-Json -Depth 4) | Set-Content (Join-Path $BackupRoot 'prepared-manifest.json') -Encoding UTF8
Log "manifest: $(Join-Path $BackupRoot 'prepared-manifest.json')"
Log "done. Launch with: $TestGame\Yuri.exe -SPEEDCONTROL   (then Single Player -> Skirmish -> Start)"
Log "restore with scripts\restore_test_game_wdzs.ps1"
