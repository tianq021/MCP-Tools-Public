# Fix-DisableDefrag.ps1
# 用途：以管理员身份运行，禁用 Windows“优化驱动器/碎片整理”计划任务，并对固定磁盘做一次 TRIM。
# 说明：这个是给 修复-停止空闲碎片整理.cmd 内部调用的实际执行脚本，也可以直接在管理员 PowerShell 里运行。
$log = Join-Path $PSScriptRoot 'fix_log.txt'
function LogMsg([string]$m) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
    Write-Host $line
}
LogMsg '========== 开始修复 =========='
try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    LogMsg ('管理员权限: ' + $isAdmin)
    $before = Get-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' -TaskName 'ScheduledDefrag' -ErrorAction Stop
    LogMsg ('禁用前任务状态: ' + $before.State)
    Get-Process Defrag -ErrorAction SilentlyContinue | ForEach-Object {
        LogMsg ('正在停止运行中的 Defrag.exe PID=' + $_.Id)
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch { LogMsg ('停止失败: ' + $_.Exception.Message) }
    }
    Disable-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' -TaskName 'ScheduledDefrag' -ErrorAction Stop | Out-Null
    $after = Get-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' -TaskName 'ScheduledDefrag'
    LogMsg ('禁用后任务状态: ' + $after.State)
    if ($after.State -ne 'Disabled') { LogMsg '注意：任务状态未变成 Disabled，请手动检查' }
} catch {
    LogMsg ('禁用计划任务失败: ' + $_.Exception.Message)
}
try {
    $vols = @(Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.DriveLetter -and $_.FileSystemType -eq 'NTFS' })
    foreach ($v in $vols) {
        LogMsg ('TRIM ' + $v.DriveLetter + ': 开始 ...')
        try {
            Optimize-Volume -DriveLetter $v.DriveLetter -ReTrim -ErrorAction Stop
            LogMsg ('TRIM ' + $v.DriveLetter + ': 完成')
        } catch {
            LogMsg ('TRIM ' + $v.DriveLetter + ': 失败 ' + $_.Exception.Message)
        }
    }
} catch {
    LogMsg ('TRIM 过程异常: ' + $_.Exception.Message)
}
LogMsg '========== 修复结束 =========='
