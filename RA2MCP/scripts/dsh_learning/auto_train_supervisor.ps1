# 无人值守训练监督器 v2（P4 实验 A / S1）
# - 从最新 checkpoint/model/emergency 续训（保留进度）
# - 每次尝试自动申请一次性审批令牌（P0）
# - 失败自动冷却重试；连续快速失败自动把 workers 6→4（缓解重开级联）
# - 关键事件通过本机飞书集成主动发送提醒（用户睡觉时也能收到）
# - 运行到 Hours 截止；资源监控独立进程写日志
param(
    [double]$Hours = 8,
    [int]$Workers = 6,
    [int]$SegmentSteps = 60000,
    [int]$SegmentMinutes = 55,
    [int]$CooldownSeconds = 60,
    [int]$Seed = 11,
    [string]$Experiment = 'A',
    [string]$Skill = 'S1',
    [switch]$NoAlerts
)
$ErrorActionPreference = 'Continue'
$scriptDir = $PSScriptRoot
if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$root = Split-Path -Parent (Split-Path -Parent $scriptDir)
$py = 'F:\conda\envs\python_mmx_LLM\python.exe'
$feishuPy = 'C:\Program Files\Python310\python.exe'
$feishuSend = 'F:\deepseekA\integrations\feishu-notify\send_message.py'
$feishuCfg = 'F:\deepseekA\integrations\feishu-notify\config.local.json'
$expDir = Join-Path $root 'reports/dsh-learning/experiment-a'
New-Item -ItemType Directory -Force $expDir | Out-Null
$logPath = Join-Path $expDir 'auto-supervisor.log'
$resLog = Join-Path $expDir 'auto-resource.log'
$deadline = (Get-Date).AddHours($Hours)
$alertThrottle = @{}
$script:alertCount = 0

function Log([string]$msg) {
    $line = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    Add-Content -Path $logPath -Value $line -Encoding utf8
}
function Send-Alert([string]$kind, [string]$text) {
    if ($NoAlerts) { Log "ALERT skipped (NoAlerts): $kind"; return }
    $script:alertCount = $script:alertCount + 1
    if ($script:alertCount -gt 12) { Log "ALERT suppressed (count cap): $kind"; return }
    $now = Get-Date
    if ($alertThrottle.ContainsKey($kind) -and ($now - $alertThrottle[$kind]).TotalMinutes -lt 10) {
        Log "ALERT throttled: $kind"; return
    }
    $alertThrottle[$kind] = $now
    $os = Get-CimInstance Win32_OperatingSystem
    $ram = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB, 1)
    $gpu = (nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader) -replace "`r", ''
    $msg = "[RA2MCP 训练监督器] $kind`n时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n$text`nRAM: ${ram}GB/31.7GB`nVRAM: $gpu`n主机: $env:COMPUTERNAME"
    Log ("ALERT $kind -> feishu")
    try {
        & $feishuPy -B -X utf8 $feishuSend --config $feishuCfg --text $msg 2>&1 | ForEach-Object { Log ("feishu: " + $_) }
    } catch { Log ("feishu send failed: " + $_) }
}

# 阻止睡眠（失败不致命）
try { powercfg /change standby-timeout-ac 0 | Out-Null } catch {}
try { powercfg /change monitor-timeout-ac 0 | Out-Null } catch {}

$env:PYTHONPATH = Join-Path $root '.venv-rl/Lib/site-packages'
$env:DSH_COMBAT_ENV = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:TEMP = Join-Path $root '.tmp'
$env:TMP = $env:TEMP

Log ("SUPERVISOR START root=$root hours=$Hours workers=$Workers seed=$Seed experiment=$Experiment skill=$Skill deadline=$deadline")
Send-Alert '启动' ("无人值守训练已启动：$Hours 小时，workers=$Workers，seed=$Seed，实验=$Experiment/$Skill。`n失败会自动续训重试；异常会再发消息。")

# 独立资源监控进程
$monitorScript = Join-Path $expDir 'auto-resource-monitor.ps1'
@'
param([string]$LogPath)
while ($true) {
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        $ram = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB, 2)
        $commit = [math]::Round(($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1MB, 2)
        $gpu = (nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader) -replace "`r", ''
        $games = (Get-Process -Name 'gamemd-spawn-ra2yrcpp' -ErrorAction SilentlyContinue | Measure-Object).Count
        $trainers = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'train_shared' } | Measure-Object).Count
        Add-Content -Path $LogPath -Value ('{0} ram_gb={1} commit_gb={2} vram={3} games={4} trainers={5}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $ram, $commit, $gpu, $games, $trainers) -Encoding utf8
    } catch {}
    Start-Sleep -Seconds 300
}
'@ | Set-Content -Path $monitorScript -Encoding utf8
$monPat = 'auto-resource-' + 'monitor.ps1'
$existingMon = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match $monPat }
if (-not $existingMon) {
    Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $monitorScript, '-LogPath', $resLog) -WindowStyle Hidden | Out-Null
}

$baseModel = Join-Path $root 'reports/rl/stage-A-round1-20261030-153225/model.zip'
$failures = 0
$attempt = 0
$workers = $Workers

while ((Get-Date) -lt $deadline) {
    $remainingMin = [math]::Floor(($deadline - (Get-Date)).TotalMinutes)
    if ($remainingMin -lt 3) { Log 'deadline nearly reached; stop'; break }
    $attempt++
    Get-Process -Name 'gamemd-spawn-ra2yrcpp' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $guard = 0
    while ($guard -lt 120) {
        $running = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'train_shared' } | Measure-Object).Count
        if ($running -eq 0) { break }
        Start-Sleep -Seconds 10
        $guard++
    }
    $cands = @()
    if (Test-Path $baseModel) { $cands += Get-Item $baseModel }
    Get-ChildItem (Join-Path $root 'reports/rl') -Directory -Filter 'experimentA-auto-*' -ErrorAction SilentlyContinue | ForEach-Object {
        foreach ($name in @('model.zip', 'checkpoint.zip', 'emergency.zip')) {
            $p = Join-Path $_.FullName $name
            if ((Test-Path $p) -and (Test-Path (Join-Path $_.FullName 'action-contract.json'))) {
                $cands += Get-Item $p
            }
        }
    }
    if ($cands.Count -eq 0) { Log 'no resumable model found; abort'; Send-Alert '停止' '没有可续训模型，监督器停止'; break }
    $resume = ($cands | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName

    # 1) 生成配置（显式检查）
    $cfg = Join-Path $expDir 'auto-run-config.json'
    $cfgArgs = @((Join-Path $root 'scripts/dsh_learning/make_experiment_config.py'),
        '--out', $cfg, '--experiment', $Experiment, '--skill', $Skill, '--seed', $Seed,
        '--workers', $workers, '--max-steps', $SegmentSteps, '--eval-every', 2500,
        '--eval-episodes', 20, '--episode-seconds', 300)
    & $py @cfgArgs 2>&1 | ForEach-Object { Log ("cfg: " + $_) }
    if (-not (Test-Path $cfg)) {
        Log 'config generation failed; retry after cooldown'
        Send-Alert '配置失败' 'run 配置生成失败，监督器将重试（训练未启动）'
        Start-Sleep -Seconds $CooldownSeconds
        continue
    }
    # 2) 发放令牌
    $issueArgs = @((Join-Path $root 'scripts/dsh_learning/approvals.py'), 'issue',
        '--config', $cfg, '--model', $resume, '--issued-by', 'USER_SLEEP_AUTORUN')
    $issueRaw = & $py @issueArgs 2>&1
    $issue = $null
    try { $issue = ($issueRaw | Out-String | ConvertFrom-Json) } catch {}
    if (-not $issue -or -not $issue.ok) {
        Log ("token issue failed: " + ($issueRaw | Out-String))
        Send-Alert '令牌失败' '一次性审批令牌发放失败，训练未启动（将重试）'
        Start-Sleep -Seconds $CooldownSeconds
        continue
    }
    $token = $issue.token_id
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $out = Join-Path $root "reports/rl/experimentA-auto-$stamp"
    Log ("ATTEMPT $attempt workers=$workers remaining=${remainingMin}min segment=${SegmentMinutes}min resume=$resume token=$token out=$out")
    $start = Get-Date
    $trainLog = Join-Path $expDir ("auto-train-$stamp.log")
    $runtimeCap = [int][math]::Min([math]::Max(2, $remainingMin), ($SegmentMinutes + 5))
    $trainArgs = @((Join-Path $root 'scripts/dsh_rl/train_shared.py'), '--resume', $resume,
        '--out', $out, '--steps', $SegmentSteps, '--workers', $workers, '--episode-seconds', 300,
        '--device', 'cpu', '--seed', $Seed, '--approval-token', $token,
        '--approval-config', $cfg, '--checkpoint-every-episodes', 2,
        '--max-runtime-minutes', $runtimeCap)
    & $py @trainArgs *> $trainLog
    $exit = $LASTEXITCODE
    $elapsed = ((Get-Date) - $start).TotalMinutes
    $summary = Get-Content $trainLog -Encoding utf8 -Tail 3
    Log ("ATTEMPT $attempt END exit=$exit elapsed=$([math]::Round($elapsed,1))min log=$trainLog")
    if ($exit -eq 0 -and $elapsed -gt 15) {
        $failures = 0
        if ($workers -lt $Workers) { $workers = $Workers; Log "workers restored to $workers" }
        Log ("ATTEMPT $attempt OK progress saved")
    } else {
        $failures++
        $tail = ($summary | Out-String).Trim()
        Log ("ATTEMPT $attempt FAILED failures=$failures")
        Send-Alert '训练异常' ("训练尝试失败（第 $attempt 次，exit=$exit，运行 $([math]::Round($elapsed,1)) 分钟）。`n回退到最新 checkpoint 继续。`n日志尾部: $($tail.Substring(0,[Math]::Min(300,$tail.Length)))")
        if ($failures -ge 2 -and $workers -gt 4) {
            $workers = 4
            Log "two consecutive failures -> reduce workers to $workers"
            Send-Alert '降并发' "连续失败，workers 自动降为 $workers 以缓解重开级联。"
        }
    }
    Get-Process -Name 'gamemd-spawn-ra2yrcpp' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds $CooldownSeconds
}
Log 'SUPERVISOR STOP (deadline or abort)'
Send-Alert '结束' ("无人值守训练监督器结束（$Hours 小时窗口）。`n请查看 $logPath 与 $resLog。")
