# Resource sampler for the headless-window experiments.
#
# Two streams, both ~1 Hz:
#  1. per-process CPU / working set / private bytes sampled with Get-Process
#     (cheap) -> $OutCsv, with the current experiment phase label;
#  2. GPU Engine / GPU Process Memory performance counters logged continuously
#     by typeperf -> $GpuCsv (one column per GPU counter instance, so the
#     analysis can sum the engines belonging to each game pid).
# Read-only: no game interaction.
param(
    [Parameter(Mandatory = $true)][string]$Pids,
    [Parameter(Mandatory = $true)][string]$OutCsv,
    [Parameter(Mandatory = $true)][string]$GpuCsv,
    [int]$DurationSec = 120,
    [int]$IntervalMs = 1000
)
$ErrorActionPreference = 'SilentlyContinue'
$pidList = $Pids.Split(',') | ForEach-Object { [int]$_.Trim() }
"ts_ms,ts_iso,pid,phase,cpu_seconds,ws_mb,private_mb,sys_avail_mb,nv_util,nv_mem_mb" |
    Set-Content -LiteralPath $OutCsv -Encoding ascii

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
    foreach ($p in $pidList) {
        $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
        if (-not $proc) {
            "$tsMs,$tsIso,$p,$phase,,,,$lastSysAvail,$nvUtil,$nvMem" | Add-Content -LiteralPath $OutCsv -Encoding ascii
            continue
        }
        $cpu = ''
        try { $cpu = [math]::Round($proc.TotalProcessorTime.TotalSeconds, 3) } catch { }
        $ws = [math]::Round($proc.WorkingSet64 / 1MB, 1)
        $priv = [math]::Round($proc.PrivateMemorySize64 / 1MB, 1)
        "$tsMs,$tsIso,$p,$phase,$cpu,$ws,$priv,$lastSysAvail,$nvUtil,$nvMem" |
            Add-Content -LiteralPath $OutCsv -Encoding ascii
    }
    $spent = $sw.Elapsed.TotalMilliseconds
    $sleep = $IntervalMs - ($spent % $IntervalMs)
    if ($sleep -lt 50) { $sleep = 50 }
    Start-Sleep -Milliseconds $sleep
}
# wait for the continuous counter logger to finish its requested sample count
if ($typeperf -and -not $typeperf.HasExited) {
    try { $typeperf.WaitForExit(5000) } catch { }
}
if ($typeperf -and -not $typeperf.HasExited) {
    try { $typeperf.Kill() } catch { }
}
Write-Output "monitor done: process samples=$sample gpu samples requested=$gpuSamples"
