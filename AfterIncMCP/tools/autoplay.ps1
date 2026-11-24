param(
  [int]$MaxMinutes = 60,
  [int]$LoopMs = 2500
)
$ErrorActionPreference = 'Continue'
$client = 'dsh-autoplay'
$base = 'http://127.0.0.1:17840/v1/call'
$script:ctl = $false
$script:built = @{}
$script:bought = @{}
$script:cool = @{}
$script:lastTurn = -1
$script:lastLog = [datetime]::MinValue

function Log([string]$m) {
  $ts = (Get-Date).ToString('HH:mm:ss')
  Write-Output "$ts  $m"
}

function Call([string]$cmd, $a) {
  $body = @{ command = $cmd; args = $a; request_id = [guid]::NewGuid().ToString(); client_id = $client } | ConvertTo-Json -Depth 8
  for ($i = 0; $i -lt 2; $i++) {
    try {
      $r = (Invoke-WebRequest -Uri $base -Method POST -Body $body -ContentType 'application/json' -Headers @{ 'X-IncMcp' = '1' } -UseBasicParsing -TimeoutSec 20).Content
      return ($r | ConvertFrom-Json)
    } catch { Start-Sleep -Milliseconds 350 }
  }
  return $null
}

function NeedControl([int]$ttl = 150) {
  $r = Call 'acquire_control' @{ label = 'autoplay'; ttl_seconds = $ttl }
  if ($r -and $r.ok) { $script:ctl = $true; return $true }
  return $false
}
function DropControl([bool]$pause = $false) {
  if ($script:ctl) { Call 'release_control' @{ pause = $pause } | Out-Null; $script:ctl = $false }
}
function SetSpeed([string]$s) { Call 'set_speed' @{ speed = $s } | Out-Null }

function GetFood() {
  $st = Call 'get_state' @{}
  if (-not $st -or -not $st.ok) { return 0.0 }
  $f = $st.data.resources | Where-Object { $_.id -eq 'Food' }
  if ($f) { return [double]$f.total }
  return 0.0
}

function DismissDialog() {
  $gc = Call 'get_game_controls' @{}
  if (-not $gc -or -not $gc.ok) { return $false }
  $btns = @($gc.data.controls)
  if ($btns.Count -eq 0) { return $false }
  $rev = $gc.data.revision
  # decision screens: pick a sensible option
  $opts = @($btns | Where-Object { $_.name -like 'DecisionScreenOption*' })
  if ($opts.Count -gt 0) {
    $food = (GetFood)
    $pick = $null
    $usable = @($opts | Where-Object { $_.label -notmatch '没有.*可用|不可用|需要' })
    if ($usable.Count -eq 0) { $usable = $opts }
    if ($food -ge 7) { $pick = $usable | Where-Object { $_.label -match '驯养' } | Select-Object -First 1 }
    if (-not $pick) { $pick = $usable | Where-Object { $_.label -match '吃掉' } | Select-Object -First 1 }
    if (-not $pick) { $pick = $usable | Select-Object -First 1 }
    if ($pick) {
      if (-not $script:ctl -and -not (NeedControl)) { return $false }
      $r = Call 'activate_game_control' @{ control_id = $pick.control_id; expected_revision = $rev }
      Log ("decision -> " + (($pick.label -split "`n")[0]))
      Start-Sleep -Milliseconds 700
      return $true
    }
    return $false
  }
  # confirmation / info dialogs: prefer cancel over build when a shortfall warning is shown
  $cancel = $btns | Where-Object { $_.label -match '^取消$|^否$' -and $_.name -match 'Negative|No' } | Select-Object -First 1
  $ok = $btns | Where-Object { $_.label -match '确定|OK|继续|^是$' } | Select-Object -First 1
  $target = $ok
  if (-not $target) { $target = $cancel }
  elseif ($cancel -and ($gc.data.screen_text -join ' ') -match '不足|尚未达到|服务需求|无法提供') { $target = $cancel }
  if (-not $target) { return $false }
  if (-not $script:ctl -and -not (NeedControl)) { return $false }
  $r = Call 'activate_game_control' @{ control_id = $target.control_id; expected_revision = $rev }
  if ($r -and $r.ok) { Log ("click " + ($target.label -replace "`r?`n", ' ')); Start-Sleep -Milliseconds 600; return $true }
  return $false
}

function ClickMarkers() {
  $mk = Call 'get_map_markers' @{}
  if (-not $mk -or -not $mk.ok) { return $false }
  $markers = @($mk.data.markers)
  if ($markers.Count -eq 0) { return $false }
  $did = $false
  foreach ($m in $markers) {
    if (-not $script:ctl -and -not (NeedControl)) { break }
    $r = Call 'activate_map_marker' @{ marker_id = $m.marker_id }
    if ($r -and $r.ok) { Log ("marker " + $m.kind + " " + $m.icon); $did = $true; Start-Sleep -Milliseconds 700 }
  }
  return $did
}

function ShopStep() {
  if (-not (NeedControl)) { return }
  SetSpeed 'Paused'
  Start-Sleep -Milliseconds 250
  $p = Call 'get_settlement_purchases' @{}
  if (-not $p -or -not $p.ok) { SetSpeed 'Normal'; DropControl; return }
  if ($p.data.blocker) { SetSpeed 'Normal'; DropControl; return }
  $offers = @($p.data.offers)
  $rev = $p.data.revision
  $order = @(
    'wood_scavenging', 'food_scavenging', 'water_scavenging',
    'build_housing_1', 'build_housing_2', 'research_level_2',
    'warmth_1', 'toilet_1', 'train_militia_1', 'water_1', 'water_2',
    'fuel_scavenging', 'research_level_3', 'research_level_4', 'build_housing_3', 'food_storage_1'
  )
  $limits = @{ wood_scavenging = 5; food_scavenging = 3; water_scavenging = 2;
    build_housing_1 = 2; build_housing_2 = 5; research_level_2 = 1; warmth_1 = 3; toilet_1 = 2;
    train_militia_1 = 2; water_1 = 1; water_2 = 1; fuel_scavenging = 2;
    research_level_3 = 1; research_level_4 = 1; build_housing_3 = 5; food_storage_1 = 1 }
  $food = (GetFood)
  foreach ($want in $order) {
    $have = 0; if ($script:bought.ContainsKey($want)) { $have = [int]$script:bought[$want] }
    $cap = 3
    if ($limits.ContainsKey($want)) { $cap = $limits[$want] }
    if ($have -ge $cap) { continue }
    if ($script:cool.ContainsKey($want) -and ((Get-Date) - $script:cool[$want]).TotalSeconds -lt 45) { continue }
    $o = $offers | Where-Object { $_.tech_id -eq $want -and $_.available } | Select-Object -First 1
    if (-not $o) { continue }
    if ($want -match 'research_level' -and $food -lt 4) { continue }   # never buy tech without food
    $r = Call 'purchase_settlement' @{ tech_id = $want; expected_revision = $rev }
    if ($r -and $r.ok -and $r.data.applied) {
      $err = if ($r.data.activation_error) { ' (post-refresh NPE, harmless)' } else { '' }
      Log ("buy " + $o.name + $err)
      $script:bought[$want] = $have + 1
      break
    }
    if ($r -and $r.ok -and -not $r.data.applied) {
      # confirmation popup appeared; leave it for DismissDialog and stop buying this tick
      Log ("buy blocked: " + $o.name + " -> cooldown")
      $script:cool[$want] = Get-Date
      break
    }
  }
  SetSpeed 'Normal'
  DropControl
}

function ZoneStep([int]$zoneId) {
  if (-not (NeedControl)) { return }
  SetSpeed 'Paused'
  Start-Sleep -Milliseconds 200
  $la = Call 'list_actions' @{ zone_id = $zoneId }
  if (-not $la -or -not $la.ok) { SetSpeed 'Normal'; DropControl; return }
  $rev = $la.data.revision
  foreach ($act in @($la.data.actions)) {
    if (-not $act.details.available) { continue }
    $key = "$zoneId/" + $act.details.ident
    if ($script:built.ContainsKey($key)) { continue }
    $r = Call 'execute_action' @{ action_id = $act.action_id; expected_revision = $rev }
    if ($r -and $r.ok -and $r.data.applied) {
      Log ("build " + $act.details.title + " @zone " + $zoneId)
      $script:built[$key] = $true
      break
    }
  }
  SetSpeed 'Normal'
  DropControl
}

function ExploreStep() {
  $map = Call 'get_map' @{}
  if (-not $map -or -not $map.ok) { return }
  $zones = @($map.data.zones)
  $target = $zones | Where-Object { $_.scoutable -and -not $_.explored } | Select-Object -First 1
  if (-not $target) { return }
  if (-not (NeedControl)) { return }
  $r = Call 'scout_zone' @{ zone_id = $target.zone_id }
  if ($r -and $r.ok) { Log ("scout zone " + $target.zone_id) }
  DropControl
  Start-Sleep -Milliseconds 900
  # claim the freshly explored zone when possible
  $map2 = Call 'get_map' @{}
  if ($map2 -and $map2.ok) {
    $fresh = @($map2.data.zones) | Where-Object { $_.explored -and -not $_.claimed -and $_.scoutable -eq $false } | Select-Object -First 1
    if ($fresh) {
      if (NeedControl) {
        $c = Call 'claim_zone' @{ zone_id = $fresh.zone_id }
        if ($c -and $c.ok) { Log ("claim zone " + $fresh.zone_id + " cost " + $c.data.claim_cost) }
        elseif ($c) { Log ("claim rejected " + $fresh.zone_id + ": " + $c.error.code) }
        DropControl
      }
    }
  }
}

Log "autoplay start"
$end = (Get-Date).AddMinutes($MaxMinutes)
$loop = 0
while ((Get-Date) -lt $end) {
  $loop++
  $st = Call 'get_state' @{}
  if (-not $st -or -not $st.ok) { Start-Sleep -Milliseconds 1200; continue }
  if (-not $st.data.ready) { Start-Sleep -Milliseconds 1500; continue }
  $turn = [int]$st.data.turn
  if ($turn -ne $script:lastTurn) {
    if (($loop % 6) -eq 0) { Log ("turn " + $turn + " pop " + $st.data.population + " morale " + [math]::Round(100*[double]$st.data.morale) + "% fuel " + (($st.data.resources | Where-Object id -eq 'Fuel').total) ) }
    $script:lastTurn = $turn
  }
  if (DismissDialog) { Start-Sleep -Milliseconds 400; continue }
  if ((ClickMarkers)) { Start-Sleep -Milliseconds 400 }
  # detect end of run
  $obj = Call 'get_objectives' @{}
  if ($obj -and $obj.ok -and $obj.data.outcome -ne 'in_progress') {
    Log ("RUN FINISHED: " + $obj.data.outcome); break
  }
  if (($loop % 20) -eq 0) { Log ("alive turn " + $turn + " pop " + $st.data.population + " food " + [math]::Round((($st.data.resources | Where-Object id -eq 'Food').total),1)) }
  if (($loop % 4) -eq 0) { ShopStep }
  if (($loop % 6) -eq 2) { ZoneStep 28 }
  if (($loop % 6) -eq 3) { ZoneStep 22 }
  if (($loop % 10) -eq 1) { ExploreStep }
  Start-Sleep -Milliseconds $LoopMs
}
Log "autoplay end"






