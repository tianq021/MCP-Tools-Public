param(
    [string]$Model = '',
    [int]$Episodes = 3,
    [double]$EpisodeSeconds = 600
)
# Load a saved PPO model and let it control a real RA2 game with rendering ON.
# Use the root command file 看模型打游戏.cmd to start this.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = 'F:\conda\envs\python_mmx_LLM\python.exe'

# Both training and demo need the same game copies and bridge locks.
$activePath = Join-Path $root 'reports/rl/active-run.json'
if (Test-Path -LiteralPath $activePath) {
    $active = Get-Content -LiteralPath $activePath -Raw | ConvertFrom-Json
    if ($active.pid -and (Get-Process -Id $active.pid -ErrorAction SilentlyContinue)) {
        Write-Host 'A training run is still running. Stop it first (double-click the stop script).'
        exit 1
    }
}

if (-not $Model) {
    $best = $null
    Get-ChildItem -LiteralPath (Join-Path $root 'reports/rl') -Directory | ForEach-Object {
        $reportPath = Join-Path $_.FullName 'run.json'
        if (Test-Path -LiteralPath $reportPath) {
            $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
            if ($report.control_mode -eq 'full') {
                foreach ($name in @('model.zip', 'checkpoint.zip')) {
                    $candidate = Join-Path $_.FullName $name
                    if (Test-Path -LiteralPath $candidate) {
                        $item = Get-Item -LiteralPath $candidate
                        if (-not $best -or $item.LastWriteTime -gt $best.LastWriteTime) { $best = $item }
                    }
                }
            }
        }
    }
    if (-not $best) { Write-Host 'No saved full-control model found.'; exit 1 }
    $Model = $best.FullName
}

Write-Host ('Model:   ' + $Model)
Write-Host ('Games:   ' + $Episodes + '  (rendering ON; the model controls the game)')
Write-Host 'Do not click or press keys while it plays; stop with Ctrl+C.'
$oldPythonPath, $oldTemp, $oldTmp = $env:PYTHONPATH, $env:TEMP, $env:TMP
try {
    $env:PYTHONPATH = Join-Path $root '.venv-rl/Lib/site-packages'
    $env:TEMP = Join-Path $root '.tmp'
    $env:TMP = $env:TEMP
    & $python -u (Join-Path $root 'scripts/dsh_rl/play.py') --model $Model `
        --control full --episode-seconds $EpisodeSeconds --steps 1600 --episodes $Episodes
} finally {
    $env:PYTHONPATH, $env:TEMP, $env:TMP = $oldPythonPath, $oldTemp, $oldTmp
    Get-Process -Name 'gamemd-spawn-ra2yrcpp' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
}
