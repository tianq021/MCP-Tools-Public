param(
    [ValidateSet('all', 'reward', 'match')][string]$Only = 'all'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pythonw = 'F:\conda\envs\python_mmx_LLM\pythonw.exe'
$targets = @(
    @{ File = 'reward_editor.py'; Name = '训练奖励设置' },
    @{ File = 'match_editor.py'; Name = '对局设置' }
)
if (-not (Test-Path -LiteralPath $pythonw)) { throw "pythonw not found: $pythonw" }

$running = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine } | ForEach-Object { $_.CommandLine })

foreach ($target in $targets) {
    $key = $target.File.Replace('_editor.py', '')
    if ($Only -ne 'all' -and $Only -ne $key) { continue }
    $script = Join-Path $root ('scripts\' + $target.File)
    if (-not (Test-Path -LiteralPath $script)) { Write-Output ('missing: ' + $script); continue }
    $already = $running | Where-Object { $_ -like ('*' + $target.File + '*') }
    if ($already) {
        Write-Output ('already open: ' + $target.Name)
        continue
    }
    Start-Process -FilePath $pythonw -ArgumentList ('"' + $script + '"') -WorkingDirectory $root
    Write-Output ('opened: ' + $target.Name)
}
Write-Output 'settings launcher done.'
