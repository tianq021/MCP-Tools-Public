param(
    [int]$Steps = 128,
    [ValidateSet('full','micro')][string]$Control = 'full',
    [string]$Resume = '',
    [switch]$Continuous,
    [double]$EpisodeSeconds = 180,
    [switch]$AllowRewardChange,
    [double]$DecisionSeconds = 1.0,
    [string]$TrainingPython = 'F:\conda\envs\python_mmx_LLM\python.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$oldPythonPath = $env:PYTHONPATH
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
try {
    $env:PYTHONPATH = Join-Path $projectRoot '.venv-rl\Lib\site-packages'
    $env:TEMP = Join-Path $projectRoot '.tmp'
    $env:TMP = $env:TEMP
    $trainingArgs = @((Join-Path $projectRoot 'scripts\dsh_rl\train.py'), '--steps', $Steps, '--rollout', '32', '--control', $Control)
    $trainingArgs += @('--episode-seconds', $EpisodeSeconds)
    $trainingArgs += @('--decision-seconds', $DecisionSeconds)
    if ($Continuous) { $trainingArgs += '--continuous' }
    if ($Resume) { $trainingArgs += @('--resume', $Resume) }
    if ($AllowRewardChange) { $trainingArgs += '--allow-reward-change' }
    & $TrainingPython @trainingArgs
    if ($LASTEXITCODE -ne 0) { throw "Local PPO training failed with exit code $LASTEXITCODE" }
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
}
