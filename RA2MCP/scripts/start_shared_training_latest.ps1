param(
    [ValidateRange(2, 24)][int]$Workers = 16,
    [int]$Steps = 0,
    [int]$EpisodeSeconds = 600,
    [switch]$Combat,
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cuda',
    [double]$EpisodeJitterSeconds = 60,
    [double]$LaunchStaggerSeconds = 2.5,
    [string]$RewardConfig = 'config/rewards-combat-survival-v1.json',
    [string]$RewardVersion = 'full_configurable_v4_survival',
    [switch]$AllowRewardChange = $true,
    [string]$ApprovalToken = '',
    [string]$ApprovalConfig = ''
)
# Resume the newest full-control checkpoint with the shared-policy trainer.
# Use the root command file 开始训练.cmd to start this.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$best = $null
Get-ChildItem -LiteralPath (Join-Path $root 'reports/rl') -Directory | ForEach-Object {
    # 保护：绝不自动续训被标记为禁止续训的退化轮次（stage-A-round3）。
    if ($_.Name -match 'round3') { return }
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
if (-not $best) { throw 'No saved full-control model found to resume from.' }
Write-Host ('Resume model: ' + $best.FullName)
Write-Host ('Workers: ' + $Workers + '  combat=' + [bool]$Combat + '  episode=' + $EpisodeSeconds + 's  device=' + $Device + '  jitter=' + $EpisodeJitterSeconds + 's  stagger=' + $LaunchStaggerSeconds + 's  reward=' + $RewardVersion)
& (Join-Path $PSScriptRoot 'start_shared_training.ps1') -Workers $Workers `
    -ResumePath $best.FullName -Steps $Steps -EpisodeSeconds $EpisodeSeconds -Combat:$Combat `
    -Device $Device -EpisodeJitterSeconds $EpisodeJitterSeconds -LaunchStaggerSeconds $LaunchStaggerSeconds `
    -RewardConfig $RewardConfig -RewardVersion $RewardVersion -AllowRewardChange:$AllowRewardChange `
    -ApprovalToken $ApprovalToken -ApprovalConfig $ApprovalConfig
