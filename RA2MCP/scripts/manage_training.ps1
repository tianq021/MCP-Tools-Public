param(
    [ValidateSet('start', 'stop', 'status')][string]$Action = 'start',
    [double]$DecisionSeconds = 1.0,
    [int]$EpisodeSeconds = 0,
    [string]$ResumePath = '',
    [switch]$Fresh,
    [ValidateRange(1, 4)][int]$Instances = 1,
    [switch]$RenderOff,
    [string]$TrainingPython = 'F:\conda\envs\python_mmx_LLM\python.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$activePath = Join-Path $projectRoot 'reports\rl\active-run.json'
$activeRunsPath = Join-Path $projectRoot 'reports\rl\active-runs.json'
$primaryPort = 14521

function Read-JsonFile($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json } catch { return $null }
}

function Get-ActiveRuns {
    # active-runs.json is the multi-instance registry; active-run.json is kept
    # as the instance-0 record so older editors/scripts keep working.
    $runs = @()
    if (Test-Path -LiteralPath $activeRunsPath) {
        $parsed = Read-JsonFile $activeRunsPath
        if ($parsed) { $runs = @($parsed) }
    }
    if ($runs.Count -eq 0) {
        $single = Read-JsonFile $activePath
        if ($single) { $runs = @($single) }
    }
    return $runs
}

function Get-InstanceGameDir([int]$index) {
    if ($index -eq 0) { return Join-Path $projectRoot 'test-game' }
    return Join-Path $projectRoot ('test-game-' + $index)
}

function Get-InstancePort([int]$index) { return $primaryPort + $index }

function Set-BridgePort($gameDir, [int]$port) {
    $bridgeConfig = Join-Path $gameDir 'ra2yrcpp.json'
    if (-not (Test-Path -LiteralPath $bridgeConfig)) { throw "Bridge config missing: $bridgeConfig" }
    $cfg = Get-Content -LiteralPath $bridgeConfig -Raw | ConvertFrom-Json
    $cfg.port = $port
    ($cfg | ConvertTo-Json -Compress) | Set-Content -LiteralPath $bridgeConfig -Encoding ascii
}

function Prepare-GameReplica([int]$index) {
    if ($index -eq 0) { return (Get-InstanceGameDir 0) }
    $source = Get-InstanceGameDir 0
    $target = Get-InstanceGameDir $index
    if (-not (Test-Path -LiteralPath $target)) {
        $sourceMb = [math]::Round(((Get-ChildItem -LiteralPath $source -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 0)
        $free = (Get-PSDrive ($target.Substring(0, 1))).Free / 1MB
        if ($free -lt ($sourceMb * 1.1)) {
            throw "Not enough disk space for game copy ${target}: need ${sourceMb} MB, free $([math]::Round($free,0)) MB"
        }
        # Write-Host, never Write-Output: the pipeline return value is the path.
        Write-Host "Creating isolated game copy for instance ${index}: $target (${sourceMb} MB, one-time)"
        $null = robocopy $source $target /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1
        if ($LASTEXITCODE -ge 8) { throw "Game copy failed (robocopy exit $LASTEXITCODE): $target" }
    }
    Set-BridgePort (Get-InstanceGameDir $index) (Get-InstancePort $index)
    # The vanilla engine refuses a second live copy (named App/AutoPlay mutexes).
    # Rename this copy's mutex GUIDs once so several real games can run together.
    & (Join-Path $PSScriptRoot 'patch_multi_instance.ps1') -GameDir $target -Index $index | Out-Null
    return (Get-InstanceGameDir $index)
}

function Stop-StaleGames {
    # The parallel group disables per-instance killing, so the launcher clears
    # leftovers once before starting. Never called while a group is running.
    Get-Process -Name 'gamemd-spawn-ra2yrcpp' -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

function Get-TrainingStatus {
    $runs = Get-ActiveRuns
    if ($runs.Count -eq 0) { Write-Output 'No training run recorded.'; return }
    foreach ($run in $runs) {
        Write-Output ('--- instance ' + $run.instance + ' ---')
        Write-Output ('Run: ' + $run.run_dir)
        if (Test-Path -LiteralPath $run.status_file) {
            $report = Read-JsonFile $run.status_file
            if ($report) { Write-Output ('State: ' + $report.status) }
        }
        if (Test-Path -LiteralPath $run.progress_file) {
            Get-Content -LiteralPath $run.progress_file
        }
        Write-Output ('Game: ' + $run.game_dir + '  port: ' + $run.port)
        $running = Get-Process -Id $run.pid -ErrorAction SilentlyContinue
        Write-Output ('Process alive: ' + [bool]$running)
        Write-Output ('Stop requested: ' + (Test-Path -LiteralPath (Join-Path $run.run_dir 'STOP')))
        Write-Output ('Reviews: ' + (Join-Path $run.run_dir 'reflections'))
    }
}

if ($Action -eq 'status') { Get-TrainingStatus; exit 0 }
if ($Action -eq 'stop') {
    $runs = Get-ActiveRuns
    if ($runs.Count -eq 0) { Write-Output 'No training run to stop.'; exit 0 }
    foreach ($run in $runs) {
        if ($run.run_dir -and (Test-Path -LiteralPath $run.run_dir)) {
            New-Item -ItemType File -Path (Join-Path $run.run_dir 'STOP') -Force | Out-Null
        }
    }
    Write-Output 'Stop requested. Learners will save weights and close their test games.'
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    foreach ($run in $runs) {
        $running = Get-Process -Id $run.pid -ErrorAction SilentlyContinue
        if ($running) {
            $left = [int][math]::Max(0, ($deadline - [DateTime]::UtcNow).TotalMilliseconds)
            if ($left -gt 0) { [void]$running.WaitForExit($left) }
        }
    }
    Get-TrainingStatus
    exit 0
}

# ---------------- start ----------------
$existing = @(Get-ActiveRuns | Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue })
if ($existing.Count -gt 0) {
    Write-Output 'Recorded training process is still running; no second run was started.'
    Get-TrainingStatus
    exit 0
}
if (-not (Test-Path -LiteralPath $TrainingPython)) { throw "Python not found: $TrainingPython" }

# Resume only Full checkpoints, preserving weights and optimizer state.
$candidates = @()
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'reports/rl') -Directory | ForEach-Object {
    $reportPath = Join-Path $_.FullName 'run.json'
    if (Test-Path -LiteralPath $reportPath) {
        $report = Read-JsonFile $reportPath
        if ($report -and $report.control_mode -eq 'full') {
            foreach ($name in @('model.zip', 'checkpoint.zip')) {
                $candidate = Join-Path $_.FullName $name
                if (Test-Path -LiteralPath $candidate) { $candidates += Get-Item -LiteralPath $candidate }
            }
        }
    }
}
$resume = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($ResumePath) {
    if (-not (Test-Path -LiteralPath $ResumePath)) { throw "Resume model not found: $ResumePath" }
    $resume = Get-Item -LiteralPath $ResumePath
}
if ($Fresh) { $resume = $null }
$stamp = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'China Standard Time').ToString('yyyyMMdd-HHmmss')

# Prepare all game copies and clear leftovers once, before any instance starts.
$gameDirs = @()
for ($i = 0; $i -lt $Instances; $i++) {
    $gameDir = Prepare-GameReplica $i
    if (-not (Test-Path -LiteralPath $gameDir)) { throw "Game copy for instance $i is missing: $gameDir" }
    $gameDirs += $gameDir
}
Stop-StaleGames
Start-Sleep -Seconds 1

$records = @()
$children = @()
for ($i = 0; $i -lt $Instances; $i++) {
    $runName = 'ppo-full-continuous-' + $stamp + '-' + [guid]::NewGuid().ToString('N').Substring(0,6)
    $runDir = Join-Path $projectRoot ('reports\rl\' + $runName)
    $stdoutLog = Join-Path $projectRoot ('build\' + $runName + '.stdout.log')
    $stderrLog = Join-Path $projectRoot ('build\' + $runName + '.stderr.log')
    $oldPythonPath, $oldTemp, $oldTmp, $oldUnbuffered = $env:PYTHONPATH, $env:TEMP, $env:TMP, $env:PYTHONUNBUFFERED
    try {
        $env:PYTHONPATH = Join-Path $projectRoot '.venv-rl\Lib\site-packages'
        $env:TEMP = Join-Path $projectRoot '.tmp'
        $env:TMP = $env:TEMP
        $env:PYTHONUNBUFFERED = '1'
        $argsList = @(('"' + (Join-Path $projectRoot 'scripts\dsh_rl\train.py') + '"'),
                      '--control', 'full', '--continuous', '--episode-seconds', $EpisodeSeconds,
                      '--decision-seconds', $DecisionSeconds,
                      '--steps', '1024', '--rollout', '32',
                      '--instance', $i, '--port', (Get-InstancePort $i),
                      '--game-dir', ('"' + $gameDirs[$i] + '"'),
                      '--out', ('"' + $runDir + '"'))
        # A parallel group must never kill sibling games; only a lone primary
        # instance is allowed to sweep stale leftovers (done above as well).
        if ($Instances -gt 1) { $argsList += '--no-kill-stale-games' }
        # Opt-in experimental render-off mode; the default (absent switch) keeps
        # normal rendering.  Validation failures abort the worker instead of
        # silently falling back (see scripts/dsh_episode/render_off.py).
        if ($RenderOff) { $argsList += '--render-off' }
        if ($resume) { $argsList += @('--resume', ('"' + $resume.FullName + '"')) }
        if ($resume) {
            $sourceReport = Read-JsonFile (Join-Path $resume.DirectoryName 'run.json')
            # Known migration: defaults preserve old scoring; future edits are versioned per episode.
            if ($sourceReport -and $sourceReport.reward_version -eq 'full_economy_ground_v1') { $argsList += '--allow-reward-change' }
        }
        $child = Start-Process -FilePath $TrainingPython -ArgumentList $argsList -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
    } finally {
        $env:PYTHONPATH, $env:TEMP, $env:TMP, $env:PYTHONUNBUFFERED = $oldPythonPath, $oldTemp, $oldTmp, $oldUnbuffered
    }
    $children += $child
    $records += [ordered]@{status='starting';pid=$child.Id;instance=$i;run_dir=$runDir;
        game_dir=$gameDirs[$i];port=(Get-InstancePort $i);
        source_model=$(if ($resume) { $resume.FullName } else { $null });
        control_mode='full';continuous=$true;episode_seconds=$EpisodeSeconds;
        decision_seconds=$DecisionSeconds;instances=$Instances;stop_method='train.cmd stop';
        stdout_log=$stdoutLog;stderr_log=$stderrLog;
        status_file=(Join-Path $runDir 'run.json');progress_file=(Join-Path $runDir 'progress.json');
        render_off=[bool]$RenderOff;
        started_at=$stamp;auto_promote=$false;full_control_training_started=$true}
}
$records | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath ($activeRunsPath + '.tmp') -Encoding utf8
Move-Item -LiteralPath ($activeRunsPath + '.tmp') -Destination $activeRunsPath -Force
($records[0] | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath ($activePath + '.tmp') -Encoding utf8
Move-Item -LiteralPath ($activePath + '.tmp') -Destination $activePath -Force

# Detect immediate startup failures before claiming launch succeeded.
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Seconds 1
    for ($i = 0; $i -lt $children.Count; $i++) {
        if ($children[$i].HasExited) {
            Get-Content -LiteralPath $records[$i].stderr_log -Tail 20 -ErrorAction SilentlyContinue
            # Never leave the other instances running half a group.
            foreach ($record in $records) {
                if (Test-Path -LiteralPath $record.run_dir) {
                    New-Item -ItemType File -Path (Join-Path $record.run_dir 'STOP') -Force | Out-Null
                }
            }
            throw "Training instance $i exited during startup (exit $($children[$i].ExitCode)). See $($records[$i].run_dir)"
        }
    }
    $collecting = 0
    foreach ($record in $records) {
        if (Test-Path -LiteralPath $record.status_file) {
            $report = Read-JsonFile $record.status_file
            if ($report -and $report.status -eq 'collecting_real_rollouts') { $collecting++ }
        }
    }
    if ($collecting -ge $records.Count) { break }
}

Write-Output ('Background training started. Instances: ' + $Instances)
for ($i = 0; $i -lt $records.Count; $i++) {
    Write-Output ('  instance ' + $i + '  PID: ' + $records[$i].pid + '  game: ' + $records[$i].game_dir + '  port: ' + $records[$i].port + '  run: ' + $records[$i].run_dir)
}
Write-Output ('Resume: ' + $(if ($resume) { $resume.FullName } else { '(fresh model)' }))
Write-Output ('Render mode: ' + $(if ($RenderOff) { 'render-off (EXPERIMENTAL, draw-skip patch; validated test copies only)' } else { 'normal rendering (default)' }))
Write-Output 'Play full games, review each result, start the next game. Continue until stopped.'
Write-Output 'Stop and save: .\train.cmd stop'
Write-Output 'Check progress: .\train.cmd status'
