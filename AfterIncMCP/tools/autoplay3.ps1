param([int]$MaxMinutes = 45, [int]$LoopMs = 2500)
$ErrorActionPreference = 'Continue'
$client = 'dsh-auto3'
$base = 'http://127.0.0.1:17840/v1/call'
$script:ctl = $false
$script:pop = 2
$script:lastTurn = -1

function Log([string]$m) { Write-Output ((Get-Date).ToString('HH:mm:ss') + "  " + $m) }

function Call([string]$cmd, $a) {
  $body = @{ command = $cmd; args = $a; request_id = [guid]::NewGuid().ToString(); client_id = $client } | ConvertTo-Json -Depth 8
  for ($i = 0; $i -lt 2; $i++) {
    try { return ((Invoke-WebRequest -Uri $base -Method POST -Body $body -ContentType 'application/json' -Headers @{ 'X-IncMcp' = '1' } -UseBasicParsing -TimeoutSec 20).Content | ConvertFrom-Json) } catch { Start-Sleep -Milliseconds 300 }
  }
  return $null
}
function NeedControl() { $r = Call 'acquire_control' @{ label = 'auto3'; ttl_seconds = 240 }; if ($r -and $r.ok) { $script:ctl = $true; return $true }; return $false }
function DropControl() { if ($script:ctl) { Call 'release_control' @{ pause = $false } | Out-Null; $script:ctl = $false } }

function Dismiss() {
  for ($i = 0; $i -lt 3; $i++) {
    $gc = Call 'get_game_controls' @{}
    if (-not $gc -or -not $gc.ok) { return $false }
    $btns = @($gc.data.controls)
    $opts = @($btns | Where-Object { $_.name -like 'DecisionScreenOption*' })
    $target = $null
    if ($opts.Count -gt 0) { $target = @($opts | Where-Object { $_.label -notmatch '没有.*可用|不可用' })[0] }
    if (-not $target) { $target = $btns | Where-Object { $_.label -match '^取消$|^否$' } | Select-Object -First 1 }
    if (-not $target) { $target = $btns | Where-Object { $_.label -match '确定|OK|继续|^是$' } | Select-Object -First 1 }
    if (-not $target) { return $false }
    if (-not $script:ctl -and -not (NeedControl)) { return $false }
    $r = Call 'activate_game_control' @{ control_id = $target.control_id; expected_revision = $gc.data.revision }
    Log ("dialog -> " + (($target.label -split "`n")[0]))
    Start-Sleep -Milliseconds 600
    if (-not $r -or -not $r.ok) { return $false }
  }
  return $true
}

function CloseScreenUI() {
  for ($i = 0; $i -lt 2; $i++) {
    $gc = Call 'get_game_controls' @{}
    if (-not $gc -or -not $gc.ok) { return $false }
    $cl = @($gc.data.controls) | Where-Object { $_.name -eq 'Close' } | Select-Object -First 1
    if (-not $cl) { return $false }
    [void](Call 'activate_game_control' @{ control_id = $cl.control_id; expected_revision = $gc.data.revision })
    Start-Sleep -Milliseconds 600
  }
  return $true
}
function Buy([string]$id, [string]$tag) {
  $r = Call 'buy_native' @{ tech_id = $id }
  if ($r -and $r.ok) { Log ("  native buy " + $tag + " ok") ; Start-Sleep -Milliseconds 700; return $true }
  if ($r) { Log ("  native buy " + $tag + " -> " + $r.error.code) }
  return $false
}

function BuildPass() {
  if (-not (NeedControl)) { return }
  # 1) services (fire pits / toilets)
  Call 'open_morale' @{} | Out-Null
  Start-Sleep -Milliseconds 900
  [void](Dismiss)
  $st = Call 'get_state' @{}
  $pop = 2
  if ($st -and $st.ok) { $pop = [int]$st.data.population }
  $fire = [math]::Max(2, [math]::Min(4, $pop))
  for ($i = 0; $i -lt $fire; $i++) { [void](Buy 'warmth_1' 'firepit'); [void](Dismiss) }
  for ($i = 0; $i -lt 2; $i++) { [void](Buy 'toilet_1' 'toilet'); [void](Dismiss) }
  # 2) water + storage rows
  Call 'open_settlement' @{} | Out-Null
  Start-Sleep -Milliseconds 700
  $gc = Call 'get_game_controls' @{}
  $tab = @($gc.data.controls) | Where-Object { $_.name -eq 'Resources' } | Select-Object -First 1
  if ($tab) { Call 'activate_game_control' @{ control_id = $tab.control_id; expected_revision = $gc.data.revision } | Out-Null; Start-Sleep -Milliseconds 700 }
  [void](Buy 'water_1' 'well'); [void](Dismiss)
  [void](Buy 'water_2' 'well2'); [void](Dismiss)
  [void](Buy 'water_storage_1' 'water-store'); [void](Dismiss)
  # 3) overview rows: housing / research / militia
  $gc = Call 'get_game_controls' @{}
  $ov = @($gc.data.controls) | Where-Object { $_.name -eq 'Overview' } | Select-Object -First 1
  if ($ov) { Call 'activate_game_control' @{ control_id = $ov.control_id; expected_revision = $gc.data.revision } | Out-Null; Start-Sleep -Milliseconds 700 }
  [void](Buy 'build_housing_1' 'tent'); [void](Dismiss)
  [void](Buy 'build_housing_2' 'shack'); [void](Dismiss)
  $food = 0
  $st2 = Call 'get_state' @{}
  if ($st2 -and $st2.ok) { $f = $st2.data.resources | Where-Object { $_.id -eq 'Food' }; if ($f) { $food = [double]$f.total } }
  if ($food -ge 4) { [void](Buy 'research_level_2' 'tech2'); [void](Dismiss) }
  [void](Buy 'train_militia_1' 'militia'); [void](Dismiss)
  [void](CloseScreenUI)
  DropControl
}

function ExplorePass() {
  if (-not (NeedControl)) { return }
  [void](CloseScreenUI)
  Start-Sleep -Milliseconds 300
  $map = Call 'get_map' @{}
  if ($map -and $map.ok) {
    $t = @($map.data.zones) | Where-Object { $_.scoutable -and -not $_.explored } | Select-Object -First 1
    if ($t) { $r = Call 'scout_zone' @{ zone_id = $t.zone_id }; if ($r -and $r.ok) { Log ("scout " + $t.zone_id) } ; Start-Sleep -Milliseconds 800 }
    $map2 = Call 'get_map' @{}
    if ($map2 -and $map2.ok) {
      $f = @($map2.data.zones) | Where-Object { $_.explored -and -not $_.claimed -and $_.zone_id -ne 27 } | Select-Object -First 1
      if ($f) { $c = Call 'claim_zone' @{ zone_id = $f.zone_id }; if ($c -and $c.ok) { Log ("claim " + $f.zone_id) } elseif ($c) { Log ("claim " + $f.zone_id + " -> " + $c.error.code) } }
    }
  }
  DropControl
}

Log "auto3 start"
$end = (Get-Date).AddMinutes($MaxMinutes)
$loop = 0
while ((Get-Date) -lt $end) {
  $loop++
  if (-not $script:ctl -and -not (NeedControl)) { Start-Sleep -Milliseconds 1500; continue }
  $st = Call 'get_state' @{}
  if (-not $st -or -not $st.ok) { Start-Sleep -Milliseconds 1200; continue }
  $turn = [int]$st.data.turn
  $pop = [int]$st.data.population
  if ($turn -ne $script:lastTurn) {
    if (($loop % 8) -eq 0) {
      $rs = ($st.data.resources | ForEach-Object { $_.id + "=" + [math]::Round($_.total,1) }) -join ' '
      Log ("turn " + $turn + " pop " + $pop + " morale " + [math]::Round(100 * [double]$st.data.morale) + "% | " + $rs)
    }
    $script:lastTurn = $turn
  }
  Call 'set_speed' @{ speed = 'Normal' } | Out-Null
  if (Dismiss) { continue }
  # markers
  $mk = Call 'get_map_markers' @{}
  if ($mk -and $mk.ok -and (@($mk.data.markers)).Count -gt 0) {
    foreach ($m in @($mk.data.markers)) { [void](Call 'activate_map_marker' @{ marker_id = $m.marker_id }); Start-Sleep -Milliseconds 500 }
    Log "markers clicked"
    continue
  }
  if (($loop % 5) -eq 0) { BuildPass }
  if (($loop % 7) -eq 3) { ExplorePass }
  if ($pop -le 0) { Log "POPULATION ZERO - run over"; break }
  Start-Sleep -Milliseconds $LoopMs
}
Log "auto3 end"


