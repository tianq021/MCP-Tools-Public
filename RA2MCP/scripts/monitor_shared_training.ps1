param(
    [Parameter(Mandatory=$true)][string]$Run,
    [int]$IntervalSeconds = 60,
    [int]$MaxMinutes = 240
)
# 只读监控：定期把训练吞吐/对局结果/资源/磁盘写入 run 目录的 monitor-status.log。
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $root $Run
$log = Join-Path $runDir 'monitor-status.log'
$deadline = (Get-Date).AddMinutes($MaxMinutes)
while ((Get-Date) -lt $deadline) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "$stamp | "
    $progress = Join-Path $runDir 'progress.json'
    if (Test-Path $progress) {
        $p = Get-Content $progress -Raw | ConvertFrom-Json
        $line += ("tps={0:N1} steps={1} episodes={2} discarded={3} instances={4} | " -f $p.transitions_per_second, $p.timesteps, $p.completed_episodes, $p.feedback_discarded_rollouts, $p.instances_running)
    }
    $kinds = @{}
    $lengths = @()
    Get-ChildItem (Join-Path $runDir 'workers\*\episodes\*.result.json') -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $r = Get-Content $_.FullName -Raw | ConvertFrom-Json
            $k = $r.kind
            if ($null -ne $r.elapsed_s) { $lengths += [double]$r.elapsed_s }
        } catch { $k = 'bad' }
        $kinds[$k] = 1 + [int]$kinds[$k]
    }
    $avgLen = if ($lengths.Count) { [math]::Round((($lengths | Measure-Object -Average).Average), 1) } else { 0 }
    $timeouts = [int]$kinds['timeout']
    $sum = ($kinds.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ','
    $games = (Get-Process -Name 'gamemd-spawn-ra2yrcpp' -ErrorAction SilentlyContinue | Measure-Object).Count
    $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    $gpu = (nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits) -join ''
    $free = [math]::Round((Get-PSDrive E).Free / 1GB, 2)
    $line += ("games={0} cpu={1:N0}% gpu={2}% disk_free={3}GB avg_ep={4}s timeouts={5} | kinds[{6}]" -f $games, $cpu, $gpu.Trim(), $free, $avgLen, $timeouts, $sum)
    Add-Content -Path $log -Value $line -Encoding utf8
    if (Test-Path (Join-Path $runDir 'STOP')) { Add-Content $log "$stamp | STOP file detected, monitor exiting"; break }
    Start-Sleep -Seconds $IntervalSeconds
}
