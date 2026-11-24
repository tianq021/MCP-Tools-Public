param(
    [ValidateRange(2,24)][int]$Workers = 8,
    [Parameter(Mandatory=$true)][string]$ResumePath,
    [int]$Steps = 0,
    [int]$EpisodeSeconds = 0,
    [switch]$Combat,
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cpu',
    [double]$EpisodeJitterSeconds = 0,
    [double]$LaunchStaggerSeconds = 0,
    [string]$RewardConfig = '',
    [string]$RewardVersion = '',
    [switch]$AllowRewardChange,
    [string]$ApprovalToken = '',
    [string]$ApprovalConfig = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$registry = Join-Path $root 'reports/rl/active-runs.json'
if (Test-Path $registry) {
    foreach ($r in (Get-Content $registry -Raw | ConvertFrom-Json)) {
        if (Get-Process -Id $r.pid -ErrorAction SilentlyContinue) { throw 'Stop existing training first.' }
    }
}
# ---- P0 审批安全底座：启动入口强制校验 ----
# 无一次性令牌/配置文件时直接拒绝，不创建任何训练进程。
if (-not $ApprovalToken -and $env:DSH_APPROVAL_TOKEN) { $ApprovalToken = $env:DSH_APPROVAL_TOKEN }
if (-not $ApprovalConfig -and $env:DSH_APPROVAL_CONFIG) { $ApprovalConfig = $env:DSH_APPROVAL_CONFIG }
if (-not $ApprovalToken -or -not $ApprovalConfig) {
    throw ('TRAINING REFUSED (P0 approval gate): -ApprovalToken and -ApprovalConfig are required. ' +
        'Issue a one-time token: python scripts/dsh_learning/approvals.py issue --config <run-config.json> ' +
        '[--model <model.zip>]; or set env DSH_APPROVAL_TOKEN / DSH_APPROVAL_CONFIG.')
}
$approvalConfigPath = $ApprovalConfig
if (-not [System.IO.Path]::IsPathRooted($approvalConfigPath)) {
    $approvalConfigPath = Join-Path $root $approvalConfigPath
}
if (-not (Test-Path -LiteralPath $approvalConfigPath)) {
    throw ('TRAINING REFUSED (P0 approval gate): approval config not found: ' + $approvalConfigPath)
}
$name = 'ppo-shared-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$out = Join-Path $root ('reports/rl/' + $name)
$stdout = Join-Path $root ('build/' + $name + '.stdout.log')
$stderr = Join-Path $root ('build/' + $name + '.stderr.log')
$oldPath, $oldTemp, $oldTmp = $env:PYTHONPATH, $env:TEMP, $env:TMP
$oldCombat = $env:DSH_COMBAT_ENV
$oldRewardCfg, $oldRewardVer = $env:DSH_COMBAT_REWARDS, $env:DSH_REWARD_VERSION
try {
    $env:PYTHONPATH = Join-Path $root '.venv-rl/Lib/site-packages'
    $env:TEMP = Join-Path $root '.tmp'
    $env:TMP = $env:TEMP
    if ($Combat) { $env:DSH_COMBAT_ENV = '1' } else { Remove-Item Env:\DSH_COMBAT_ENV -ErrorAction SilentlyContinue }
    if ($RewardConfig) { $env:DSH_COMBAT_REWARDS = $RewardConfig } else { Remove-Item Env:\DSH_COMBAT_REWARDS -ErrorAction SilentlyContinue }
    if ($RewardVersion) { $env:DSH_REWARD_VERSION = $RewardVersion } else { Remove-Item Env:\DSH_REWARD_VERSION -ErrorAction SilentlyContinue }
    $argsList = @('-u', ('"' + (Join-Path $root 'scripts/dsh_rl/train_shared.py') + '"'),
        '--workers', $Workers, '--resume', ('"' + $ResumePath + '"'),
        '--out', ('"' + $out + '"'), '--steps', $Steps, '--device', $Device,
        '--episode-jitter-seconds', $EpisodeJitterSeconds,
        '--launch-stagger-seconds', $LaunchStaggerSeconds,
        '--approval-token', $ApprovalToken, '--approval-config', ('"' + $approvalConfigPath + '"'))
    if ($AllowRewardChange) { $argsList += '--allow-reward-change' }
    if ($EpisodeSeconds -gt 0) { $argsList += @('--episode-seconds', "$EpisodeSeconds") }
    $child = Start-Process 'F:\conda\envs\python_mmx_LLM\python.exe' -ArgumentList $argsList -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
} finally {
    $env:PYTHONPATH, $env:TEMP, $env:TMP = $oldPath, $oldTemp, $oldTmp
    if ($oldCombat) { $env:DSH_COMBAT_ENV = $oldCombat } else { Remove-Item Env:\DSH_COMBAT_ENV -ErrorAction SilentlyContinue }
    if ($oldRewardCfg) { $env:DSH_COMBAT_REWARDS = $oldRewardCfg } else { Remove-Item Env:\DSH_COMBAT_REWARDS -ErrorAction SilentlyContinue }
    if ($oldRewardVer) { $env:DSH_REWARD_VERSION = $oldRewardVer } else { Remove-Item Env:\DSH_REWARD_VERSION -ErrorAction SilentlyContinue }
}
$record = @{instance='shared';pid=$child.Id;run_dir=$out;workers=$Workers;shared_policy=$true;
    combat_stage=[bool]$Combat;episode_seconds=$EpisodeSeconds;
    approval_token=$ApprovalToken;approval_config=$approvalConfigPath;
    game_dir='isolated test-game replicas';port='14521+';status_file=(Join-Path $out 'run.json');
    progress_file=(Join-Path $out 'progress.json');stdout_log=$stdout;stderr_log=$stderr}
$record | ConvertTo-Json | Set-Content (Join-Path $root 'reports/rl/active-run.json') -Encoding utf8
ConvertTo-Json -InputObject @($record) | Set-Content $registry -Encoding utf8
$record | ConvertTo-Json
