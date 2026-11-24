# Dynamic resource sampler for multi-match render-off runs.
#
# Unlike monitor.ps1 (fixed PID list) this discovers the game processes by image
# name every sample, so it keeps working when a worker finishes one match, kills
# its game and launches the next match.  Two streams, ~1 Hz:
#   * per-process CPU / working set / private bytes -> $OutCsv (pid column);
#   * GPU Engine / GPU Process Memory counters -> $GpuCsv (typeperf, continuous).
# Read-only: no game interaction.
param(
    [Parameter(Mandatory = $true)][string]$OutCsv,
    [Parameter(Mandatory = $true)][string]$GpuCsv,
    [int]$DurationSec = 120,
    [int]$IntervalMs = 1000,
    [string]$ExeName = 'gamemd-spawn-ra2yrcpp'
)
$ErrorActionPreference = 'SilentlyContinue'
"ts_ms,ts_iso,pid,phase,cpu_seconds,ws_mb,private_mb,sys_avail_mb,nv_util,nv_mem_mb" |
    Set-Content -LiteralPath $OutCsv -Encoding ascii
$gpuPidCsv = ($OutCsv -replace '\.csv$', '') + '_gpu_by_pid.csv'
"ts_ms,ts_iso,pid,gpu_util,gpu_3d,vram_mb" |
    Set-Content -LiteralPath $gpuPidCsv -Encoding ascii

$gpuSamples = $DurationSec + 2
$typeperf = Start-Process -FilePath 'typeperf.exe' `
    -ArgumentList @('"\GPU Engine(*)\Utilization Percentage"',
                    '"\GPU Process Memory(*)\Dedicated Usage"',
                    '-si', '1', '-sc', "$gpuSamples", '-f', 'CSV', '-o', "$GpuCsv", '-y') `
    -WindowStyle Hidden -PassThru

$sw = [Diagnostics.Stopwatch]::StartNew()
$phaseFile = $OutCsv + '.phase'
$sample = 0
$lastSysAvail = ''
while ($sw.Elapsed.TotalSeconds -lt $DurationSec) {
    $sample++
    $phase = 'unknown'
    if (Test-Path -LiteralPath $phaseFile) {
        $phase = (Get-Content -LiteralPath $phaseFile -Raw).Trim()
    }
    $now = [DateTimeOffset]::UtcNow
    $tsMs = $now.ToUnixTimeMilliseconds()
    $tsIso = $now.ToString('o')
    $nv = (& nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>$null)
    $nvUtil = ''
    $nvMem = ''
    if ($nv) {
        $parts = $nv.Split(',')
        if ($parts.Count -ge 2) { $nvUtil = $parts[0].Trim(); $nvMem = $parts[1].Trim() }
    }
    if ($sample % 5 -eq 1) {
        $sys = Get-CimInstance Win32_OperatingSystem
        $lastSysAvail = [math]::Round($sys.FreePhysicalMemory / 1024, 0)
    }
    $procs = Get-Process -Name $ExeName -ErrorAction SilentlyContinue
    if (-not $procs) {
        "$tsMs,$tsIso,0,$phase,,,,$lastSysAvail,$nvUtil,$nvMem" |
            Add-Content -LiteralPath $OutCsv -Encoding ascii
    } else {
        foreach ($proc in $procs) {
            $cpu = ''
            try { $cpu = [math]::Round($proc.TotalProcessorTime.TotalSeconds, 3) } catch { }
            $ws = [math]::Round($proc.WorkingSet64 / 1MB, 1)
            $priv = [math]::Round($proc.PrivateMemorySize64 / 1MB, 1)
            "$tsMs,$tsIso,$($proc.Id),$phase,$cpu,$ws,$priv,$lastSysAvail,$nvUtil,$nvMem" |
                Add-Content -LiteralPath $OutCsv -Encoding ascii
        }
    }
    # Low-frequency per-pid GPU sampling: typeperf's instance set is fixed at
    # start, so a game restarted for the next match would be missing there.
    # Two wildcard Get-Counter queries per live pid every 5 samples (~5 s) keeps
    # GPU/VRAM evidence for every match without slowing the 1 Hz process stream.
    if ($sample % 5 -eq 1 -and $procs) {
        foreach ($proc in $procs) {
            $gpuNow = [DateTimeOffset]::UtcNow
            $gpuTs = $gpuNow.ToUnixTimeMilliseconds()
            $gpuIso = $gpuNow.ToString('o')
            $util = 0.0
            $util3d = 0.0
            $vram = 0.0
            $gs = (Get-Counter -Counter "\\GPU Engine(pid_$($proc.Id)_*)\\Utilization Percentage" -MaxSamples 1 -ErrorAction SilentlyContinue).CounterSamples
            if ($gs) {
                $util = [math]::Round((($gs | Measure-Object CookedValue -Sum).Sum), 3)
                $e3 = $gs | Where-Object { $_.InstanceName -match '_engtype_3d$' }
                if ($e3) { $util3d = [math]::Round((($e3 | Measure-Object CookedValue -Sum).Sum), 3) }
            }
            $ms = (Get-Counter -Counter "\\GPU Process Memory(pid_$($proc.Id)_*)\\Dedicated Usage" -MaxSamples 1 -ErrorAction SilentlyContinue).CounterSamples
            if ($ms) { $vram = [math]::Round((($ms | Measure-Object CookedValue -Sum).Sum) / 1MB, 2) }
            "$gpuTs,$gpuIso,$($proc.Id),$util,$util3d,$vram" |
                Add-Content -LiteralPath $gpuPidCsv -Encoding ascii
        }
    }
    $spent = $sw.Elapsed.TotalMilliseconds
    $sleep = $IntervalMs - ($spent % $IntervalMs)
    if ($sleep -lt 50) { $sleep = 50 }
    Start-Sleep -Milliseconds $sleep
}
if ($typeperf -and -not $typeperf.HasExited) {
    try { $typeperf.WaitForExit(5000) } catch { }
}
if ($typeperf -and -not $typeperf.HasExited) {
    try { $typeperf.Kill() } catch { }
}
Write-Output "dynamic monitor done: process samples=$sample gpu samples requested=$gpuSamples"
