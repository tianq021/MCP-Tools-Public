#requires -Version 5
<#
.SYNOPSIS
  ShortGame A/B experiment for the offline skirmish in the isolated test copy.
.DESCRIPTION
  For each value of spawn.ini [Settings] ShortGame (No, Yes):
    1. write spawn.ini (all other settings identical),
    2. launch the patched legacy spawner in E:\RA2MCP\test-game,
    3. wait until a human unit exists and stage=INGAME,
    4. run scripts\dsh_defeat_watch.py (read-only, no commands) for N seconds,
    5. stop the game.
  The human never deploys its MCV, so it owns zero buildings for the whole run.
  Results: reports\dsh-defeat-watch-No.json / -Yes.json.
#>
[CmdletBinding()]
param(
  [string]$TestGame = 'E:\RA2MCP\test-game',
  [string]$Template = 'E:\RA2MCP\config\spawn.ini',
  [int]$WatchSeconds = 90,
  [string]$Python = 'E:\RA2MCP\.venv\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
$spawn = Join-Path $TestGame 'spawn.ini'
$tpl = [IO.File]::ReadAllText($Template)

function Stop-Game {
  Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'gamemd' } |
    ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
  Start-Sleep -Seconds 3
}

function Wait-Ready([int]$timeoutSec = 150) {
  $t0 = Get-Date
  while (((Get-Date) - $t0).TotalSeconds -lt $timeoutSec) {
    Start-Sleep -Seconds 3
    if (-not (netstat -ano | Select-String '14521.*LISTENING')) { continue }
    & $Python (Join-Path $PSScriptRoot 'probe.py') > (Join-Path $TestGame '..\build\_ready.json') 2>&1
    if ($LASTEXITCODE -ne 0) { continue }
    try {
      $j = Get-Content (Join-Path $TestGame '..\build\_ready.json') -Raw | ConvertFrom-Json
    } catch { continue }
    $human = $j.state.houses | Where-Object { $_.currentPlayer }
    if ($null -eq $human) { continue }
    $n = @($j.state.objects | Where-Object { $_.pointerHouse -eq $human.self }).Count
    if ($j.state.stage -eq 'STAGE_INGAME' -and $n -ge 1) { return $true }
  }
  return $false
}

$results = @()
foreach ($short in @('No', 'Yes')) {
  $text = $tpl -replace 'ShortGame=No', "ShortGame=$short"
  [IO.File]::WriteAllText($spawn, $text, (New-Object Text.UTF8Encoding($false)))
  Write-Host "== ShortGame=$short ; spawn.ini written =="
  Stop-Game
  Remove-Item (Join-Path $TestGame 'ra2yrcpp.log') -Force -ErrorAction SilentlyContinue
  $p = Start-Process -FilePath (Join-Path $TestGame 'gamemd-spawn-ra2yrcpp.exe') `
    -ArgumentList '-SPAWN', '-CD' -WorkingDirectory $TestGame -PassThru
  Write-Host "launched pid $($p.Id)"
  if (-not (Wait-Ready)) { Write-Host "NOT READY for ShortGame=$short"; $results += @{ shortGame = $short; ready = $false }; continue }
  Write-Host "ready; watching $WatchSeconds s"
  $out = Join-Path (Split-Path $PSScriptRoot -Parent) "reports\dsh-defeat-watch-$short.json"
  & $Python (Join-Path $PSScriptRoot 'dsh_defeat_watch.py') --label "shortgame-$short" `
    --seconds $WatchSeconds -o $out 2>&1 | ForEach-Object { Write-Host $_ }
  $results += @{ shortGame = $short; ready = $true; output = $out }
}
Stop-Game
# restore the canonical config
[IO.File]::WriteAllText($spawn, $tpl, (New-Object Text.UTF8Encoding($false)))
Write-Host "== restored spawn.ini (ShortGame=No) =="
$results | ForEach-Object { $_ | ConvertTo-Json -Compress }
