<#
DiskWatchOneShot.ps1  —— 一次性磁盘占用取证程序
------------------------------------------------------------
用途：
  后台低频监视“物理磁盘占用率”。当任一物理磁盘占用率连续超过阈值（默认 50%），
  立即进入 5 分钟（默认 300 秒）高密度信息采集，把当时所有能观测到的信息落盘，
  采集结束后生成中文摘要 SUMMARY.txt，然后程序自动退出（一次性）。

运行：
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\DiskWatchOneShot.ps1
  （可加 -Threshold 50 -CollectSeconds 300 -WatchHours 24 等参数）

自测：
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\DiskWatchOneShot.ps1 -Test -CollectSeconds 15 -OutputRoot .\selftest

输出目录：
  <脚本所在目录>\run_yyyyMMdd_HHmmss\
#>
[CmdletBinding()]
param(
    [double]$Threshold = 50.0,          # 磁盘占用率阈值（%）
    [int]$ConsecutiveSamples = 3,       # 连续超过阈值多少次采样才触发
    [int]$WatchIntervalSec = 2,         # 监视阶段采样间隔（秒）
    [int]$CollectSeconds = 300,         # 触发后采集时长（秒），默认 5 分钟
    [double]$WatchHours = 24.0,         # 最长监视时长（小时），超时未触发则退出
    [string]$OutputRoot = '',
    [switch]$Test                       # 自测：立即触发
)

if ([string]::IsNullOrWhiteSpace($OutputRoot)) { $OutputRoot = $PSScriptRoot }

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
$script:collectErrors  = 0
$script:idleApi        = $false
$script:pcList         = New-Object System.Collections.Generic.List[object]
$script:pcInitErrors   = 0

# ============================ 输出目录与日志 ============================
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$runName = 'run_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
$runDir  = Join-Path $OutputRoot $runName
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$script:runDir     = $runDir
$script:watchLog   = Join-Path $runDir 'watch.log'
$script:watchCsv   = Join-Path $runDir 'watch_phase.csv'
$script:counterCsv = Join-Path $runDir 'counter_samples.csv'
$script:processCsv = Join-Path $runDir 'process_samples.csv'

function Write-Log {
    param([string]$Message)
    try {
        ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $Message) |
            Add-Content -LiteralPath $script:watchLog -Encoding UTF8
    } catch { }
}

# ============================ 空闲时长（判断是否“无人操作”） ============================
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace DiskDiag {
    public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    public static class Native {
        [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
        [DllImport("kernel32.dll")] public static extern uint GetTickCount();
    }
}
'@ -ErrorAction Stop
    $script:idleApi = $true
} catch { }

function Get-IdleSeconds {
    if (-not $script:idleApi) { return $null }
    try {
        $lii = [DiskDiag.LASTINPUTINFO]::new()
        $lii.cbSize = [uint32][System.Runtime.InteropServices.Marshal]::SizeOf([type][DiskDiag.LASTINPUTINFO])
        if ([DiskDiag.Native]::GetLastInputInfo([ref]$lii)) {
            return [math]::Round(([DiskDiag.Native]::GetTickCount() - [double]$lii.dwTime) / 1000.0, 0)
        }
    } catch { }
    return $null
}

# ============================ 锁：避免重复运行 ============================
$script:lockFile = Join-Path $OutputRoot 'watcher.pid'
$script:locked   = $false
try {
    if (Test-Path -LiteralPath $script:lockFile) {
        $oldPidRaw = (Get-Content -LiteralPath $script:lockFile -Raw -ErrorAction SilentlyContinue)
        if ($oldPidRaw -and $oldPidRaw.Trim() -match '^\d+$') {
            $oldPid = [int]$oldPidRaw.Trim()
            $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($oldProc -and $oldProc.ProcessName -match 'pwsh|powershell') {
                Write-Log "已有一个监视实例在运行 PID=$oldPid，本次退出。"
                exit 0
            }
        }
    }
    Set-Content -LiteralPath $script:lockFile -Value $PID -Encoding ASCII
    $script:locked = $true
} catch { }

# ============================ 快速计数器对象（.NET PerformanceCounter） ============================
function Initialize-PerformanceCounters {
    $script:pcList.Clear()
    $diskCounters = @('% Idle Time','Disk Bytes/sec','Disk Read Bytes/sec','Disk Write Bytes/sec','Avg. Disk Queue Length','Current Disk Queue Length','Avg. Disk sec/Transfer','Avg. Disk sec/Read','Avg. Disk sec/Write')
    $logCounters  = @('% Idle Time','Disk Bytes/sec','Disk Read Bytes/sec','Disk Write Bytes/sec','Avg. Disk Queue Length','Avg. Disk sec/Transfer','% Free Space')
    $memCounters  = @('Available MBytes','Pages/sec','Page Reads/sec','Page Writes/sec','Page Faults/sec','Committed Bytes','Cache Bytes','Modified Page List Bytes','Pool Paged Bytes','Pool Nonpaged Bytes')
    $cpuCounters  = @('% Processor Time','% Idle Time','% Privileged Time','% User Time')
    $sysCounters  = @('Processor Queue Length','Context Switches/sec')

    foreach ($catName in @('PhysicalDisk','LogicalDisk')) {
        try {
            $cat = New-Object System.Diagnostics.PerformanceCounterCategory($catName)
            $insts = @($cat.GetInstanceNames())
            $cnts = if ($catName -eq 'PhysicalDisk') { $diskCounters } else { $logCounters }
            foreach ($inst in $insts) {
                foreach ($cn in $cnts) {
                    try {
                        $pc = New-Object System.Diagnostics.PerformanceCounter($catName, $cn, $inst)
                        $script:pcList.Add([pscustomobject]@{ Obj = $catName; Inst = $inst; Cnt = $cn; PC = $pc })
                    } catch { $script:pcInitErrors++ }
                }
            }
        } catch { $script:pcInitErrors++ }
    }
    foreach ($cn in $memCounters) {
        try { $script:pcList.Add([pscustomobject]@{ Obj = 'Memory'; Inst = ''; Cnt = $cn; PC = (New-Object System.Diagnostics.PerformanceCounter('Memory', $cn)) }) } catch { $script:pcInitErrors++ }
    }
    foreach ($cn in $cpuCounters) {
        try { $script:pcList.Add([pscustomobject]@{ Obj = 'Processor'; Inst = '_Total'; Cnt = $cn; PC = (New-Object System.Diagnostics.PerformanceCounter('Processor', $cn, '_Total')) }) } catch { $script:pcInitErrors++ }
    }
    foreach ($cn in $sysCounters) {
        try { $script:pcList.Add([pscustomobject]@{ Obj = 'System'; Inst = ''; Cnt = $cn; PC = (New-Object System.Diagnostics.PerformanceCounter('System', $cn)) }) } catch { $script:pcInitErrors++ }
    }
    try { $script:pcList.Add([pscustomobject]@{ Obj = 'Paging File'; Inst = '_Total'; Cnt = '% Usage'; PC = (New-Object System.Diagnostics.PerformanceCounter('Paging File', '% Usage', '_Total')) }) } catch { $script:pcInitErrors++ }
}

function Get-DiskSnapshotFast {
    $res = [pscustomobject]@{ Time = (Get-Date); BusyTotal = $null; BusyMax = $null; MBpsTotal = $null; PerDisk = @() }
    $idle = @{}; $bytes = @{}
    foreach ($x in $script:pcList) {
        if ($x.Obj -ne 'PhysicalDisk') { continue }
        try {
            if ($x.Cnt -eq '% Idle Time')        { $idle[$x.Inst]  = [double]$x.PC.NextValue() }
            elseif ($x.Cnt -eq 'Disk Bytes/sec') { $bytes[$x.Inst] = [double]$x.PC.NextValue() }
        } catch { }
    }
    $totKeys = @($idle.Keys | Where-Object { $_ -match '^_total$|^总计$' })
    $diskNames = @($idle.Keys | Where-Object { $_ -notmatch '^_total$|^总计$' })
    $per = @()
    foreach ($n in $diskNames) {
        $busy = $null
        if ($idle.ContainsKey($n)) { $busy = [math]::Max(0.0, [math]::Min(100.0, [math]::Round((100.0 - $idle[$n]), 1))) }
        $mbps = $null
        if ($bytes.ContainsKey($n)) { $mbps = [math]::Round($bytes[$n] / 1MB, 2) }
        $per += [pscustomobject]@{ Instance = $n; Busy = $busy; MBps = $mbps }
    }
    $res.PerDisk = @($per)
    $busyValues = @($per | Where-Object { $null -ne $_.Busy } | ForEach-Object { [double]$_.Busy })
    if ($busyValues.Count -gt 0) { $res.BusyMax = ($busyValues | Measure-Object -Maximum).Maximum }
    if ($totKeys.Count -gt 0) {
        $res.BusyTotal = [math]::Max(0.0, [math]::Min(100.0, [math]::Round((100.0 - $idle[$totKeys[0]]), 1)))
    } elseif ($busyValues.Count -gt 0) {
        $res.BusyTotal = [math]::Round(($busyValues | Measure-Object -Average).Average, 1)
    }
    $mbpsValues = @()
    foreach ($k in $bytes.Keys) {
        if ($k -notmatch '^_total$|^总计$') { $mbpsValues += [double]$bytes[$k] }
    }
    if ($mbpsValues.Count -gt 0) {
        $res.MBpsTotal = [math]::Round((($mbpsValues | Measure-Object -Sum).Sum) / 1MB, 2)
    }
    return $res
}

# ============================ 磁盘快照（优先快速方法，失败回退 Get-Counter） ============================
function Get-DiskSnapshot {
    if ($script:pcList.Count -gt 0) { return Get-DiskSnapshotFast }
    $res = [pscustomobject]@{ Time = (Get-Date); BusyTotal = $null; BusyMax = $null; MBpsTotal = $null; PerDisk = @() }
    try {
        $c = Get-Counter -Counter '\PhysicalDisk(*)\% Idle Time','\PhysicalDisk(*)\Disk Bytes/sec' -ErrorAction Stop
        $samples = @($c.CounterSamples)
        if ($samples.Count -eq 0) { return $res }
        $idle = @{}; $bytes = @{}
        foreach ($s in $samples) {
            $iname = [string]$s.InstanceName
            if ($s.Path -match 'idle time')          { $idle[$iname]  = [double]$s.CookedValue }
            elseif ($s.Path -match 'disk bytes/sec') { $bytes[$iname] = [double]$s.CookedValue }
        }
        $totKeys = @($idle.Keys | Where-Object { $_ -match '^_total$|^总计$' })
        $diskNames = @($idle.Keys | Where-Object { $_ -notmatch '^_total$|^总计$' })
        $per = @()
        foreach ($n in $diskNames) {
            $busy = $null
            if ($idle.ContainsKey($n)) { $busy = [math]::Max(0.0, [math]::Min(100.0, [math]::Round((100.0 - $idle[$n]), 1))) }
            $mbps = $null
            if ($bytes.ContainsKey($n)) { $mbps = [math]::Round($bytes[$n] / 1MB, 2) }
            $per += [pscustomobject]@{ Instance = $n; Busy = $busy; MBps = $mbps }
        }
        $res.PerDisk = @($per)
        $busyValues = @($per | Where-Object { $null -ne $_.Busy } | ForEach-Object { [double]$_.Busy })
        if ($busyValues.Count -gt 0) { $res.BusyMax = ($busyValues | Measure-Object -Maximum).Maximum }
        if ($totKeys.Count -gt 0) {
            $res.BusyTotal = [math]::Max(0.0, [math]::Min(100.0, [math]::Round((100.0 - $idle[$totKeys[0]]), 1)))
        } elseif ($busyValues.Count -gt 0) {
            $res.BusyTotal = [math]::Round(($busyValues | Measure-Object -Average).Average, 1)
        }
        $mbpsValues = @()
        foreach ($k in $bytes.Keys) {
            if ($k -notmatch '^_total$|^总计$') { $mbpsValues += [double]$bytes[$k] }
        }
        if ($mbpsValues.Count -gt 0) {
            $res.MBpsTotal = [math]::Round((($mbpsValues | Measure-Object -Sum).Sum) / 1MB, 2)
        }
    } catch { }
    return $res
}

# ============================ 采集：性能计数器（毫秒级快照） ============================
function Collect-CounterSample {
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')
    $arr = New-Object System.Collections.Generic.List[string]
    foreach ($x in $script:pcList) {
        try {
            $v = [double]$x.PC.NextValue()
            if ([double]::IsNaN($v) -or [double]::IsInfinity($v)) { continue }
            $inst = ([string]$x.Inst) -replace '"', ''
            $cnt  = ([string]$x.Cnt)  -replace '"', ''
            $arr.Add(('{0},"{1}","{2}","{3}",{4}' -f $ts, $x.Obj, $inst, $cnt, $v.ToString([System.Globalization.CultureInfo]::InvariantCulture)))
        } catch { $script:collectErrors++ }
    }
    if ($arr.Count -gt 0) {
        Add-Content -LiteralPath $script:counterCsv -Value ($arr.ToArray()) -Encoding UTF8
    }
}

# ============================ 采集：每进程资源 ============================
function Collect-ProcessSample {
    $ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')
    try {
        $rows = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -ErrorAction Stop |
                Where-Object { $_.Name -ne '_Total' -and $_.Name -ne 'Idle' }
        $arr = New-Object System.Collections.Generic.List[string]
        foreach ($r in $rows) {
            $name = ([string]$r.Name) -replace '"', ''
            $arr.Add(('{0},{1},"{2}",{3},{4},{5},{6},{7},{8},{9},{10},{11},{12},{13},{14},{15},{16},{17},{18},{19}' -f `
                $ts,
                [int]$r.IDProcess,
                $name,
                [double]$r.PercentProcessorTime,
                [double]$r.IODataBytesPersec,
                [double]$r.IOReadBytesPersec,
                [double]$r.IOWriteBytesPersec,
                [double]$r.IOOtherBytesPersec,
                [double]$r.IODataOperationsPersec,
                [double]$r.IOReadOperationsPersec,
                [double]$r.IOWriteOperationsPersec,
                [double]$r.PageFaultsPersec,
                [math]::Round(([double]$r.WorkingSet) / 1MB, 2),
                [math]::Round(([double]$r.PrivateBytes) / 1MB, 2),
                [int]$r.ThreadCount,
                [int]$r.HandleCount,
                [math]::Round(([double]$r.PoolPagedBytes) / 1MB, 2),
                [math]::Round(([double]$r.PoolNonpagedBytes) / 1MB, 2),
                [double]$r.PercentPrivilegedTime,
                [double]$r.PercentUserTime))
        }
        if ($arr.Count -gt 0) {
            Add-Content -LiteralPath $script:processCsv -Value ($arr.ToArray()) -Encoding UTF8
        }
    } catch { $script:collectErrors++ }
}

# ============================ 各类快照 ============================
function Save-ProcessSnapshot {
    param([string]$Path)
    try {
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Select-Object ProcessId, ParentProcessId, Name, CreationDate, ExecutablePath, CommandLine, WorkingSetSize, SessionId, HandleCount, ThreadCount |
            Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8
        return $true
    } catch { return $false }
}

function Save-Services {
    param([string]$Path)
    try {
        Get-CimInstance Win32_Service -ErrorAction Stop |
            Select-Object Name, DisplayName, State, StartMode, ProcessId, PathName |
            Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8
        return $true
    } catch { return $false }
}

function Save-SystemInfo {
    param([string]$Path)
    $L = New-Object System.Collections.Generic.List[string]
    $L.Add('================ 系统与硬件信息 ================')
    $L.Add(('采集时间      : {0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')))
    $L.Add(('计算机名      : {0}' -f $env:COMPUTERNAME))
    $L.Add(('当前用户      : {0}' -f ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)))
    $L.Add(('是否管理员    : {0}' -f ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)))
    $L.Add(('PS 版本       : {0}' -f $PSVersionTable.PSVersion.ToString()))
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $L.Add(('操作系统      : {0} {1} Build {2}' -f $os.Caption, $os.Version, $os.BuildNumber))
        $L.Add(('启动时间      : {0}' -f $os.LastBootUpTime))
        $L.Add(('运行时长      : {0} 小时' -f [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 2)))
        $L.Add(('物理内存      : 总 {0} GB / 可用 {1} GB' -f [math]::Round($os.TotalVisibleMemorySize / 1MB, 2), [math]::Round($os.FreePhysicalMemory / 1MB, 2)))
    } catch { }
    try {
        $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
        $L.Add(('机型          : {0} {1}' -f $cs.Manufacturer, $cs.Model))
    } catch { }
    try {
        $L.Add('')
        $L.Add('---------------- 物理磁盘 ----------------')
        $L.Add((Get-PhysicalDisk | Select-Object DeviceId, FriendlyName, MediaType, BusType, HealthStatus, OperationalStatus, @{n = 'SizeGB'; e = { [math]::Round($_.Size / 1GB, 1) } } | Format-Table -AutoSize | Out-String -Width 200).TrimEnd())
        $L.Add('')
        $L.Add('---------------- 卷 ----------------')
        $L.Add((Get-Volume | Select-Object DriveLetter, FileSystemLabel, FileSystem, HealthStatus, @{n = 'SizeGB'; e = { [math]::Round($_.Size / 1GB, 2) } }, @{n = 'FreeGB'; e = { [math]::Round($_.SizeRemaining / 1GB, 2) } }, @{n = 'FreePct'; e = { if ($_.Size -gt 0) { [math]::Round(100 * $_.SizeRemaining / $_.Size, 1) } } } | Format-Table -AutoSize | Out-String -Width 200).TrimEnd())
    } catch { }
    try {
        $L.Add('')
        $L.Add('---------------- 存储可靠性 / SMART ----------------')
        $rel = Get-PhysicalDisk | Get-StorageReliabilityCounter -ErrorAction Stop | Select-Object DeviceId, ReadErrorsTotal, WriteErrorsTotal, Wear, Temperature, PowerOnHours, StartStopCycleCount
        $L.Add(($rel | Format-Table -AutoSize | Out-String -Width 200).TrimEnd())
    } catch { $L.Add('（存储可靠性信息需要管理员权限，已跳过）') }
    try {
        $L.Add('')
        $L.Add('---------------- 页面文件 ----------------')
        $L.Add((Get-CimInstance Win32_PageFileUsage | Select-Object Name, AllocatedBaseSize, CurrentUsage, PeakUsage | Format-Table -AutoSize | Out-String -Width 120).TrimEnd())
    } catch { }
    try {
        $L.Add('')
        $L.Add('---------------- 最近安装的更新 ----------------')
        $L.Add((Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID, Description, InstalledOn | Format-Table -AutoSize | Out-String -Width 120).TrimEnd())
        $L.Add(('待重启-更新     : {0}' -f (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')))
        $L.Add(('待重启-组件     : {0}' -f (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')))
    } catch { }
    try {
        $L.Add('')
        $L.Add('---------------- 安全软件 ----------------')
        $L.Add((Get-CimInstance -Namespace root\SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction Stop | Select-Object displayName, productState, pathToSignedProductExe | Format-Table -AutoSize | Out-String -Width 200).TrimEnd())
    } catch { }
    try {
        $L.Add('')
        $L.Add('---------------- 正在运行的定时任务 ----------------')
        $L.Add((Get-ScheduledTask -ErrorAction Stop | Where-Object State -eq 'Running' | Select-Object TaskName, TaskPath, State | Format-Table -AutoSize | Out-String -Width 200).TrimEnd())
    } catch { }
    try {
        $L.Add('')
        $L.Add('---------------- 电源请求（谁在让系统保持唤醒） ----------------')
        $L.Add((powercfg /requests 2>&1 | Out-String).TrimEnd())
    } catch { }
    try {
        $L.Add('')
        $L.Add('---------------- 关键进程累计 CPU（前 15） ----------------')
        $L.Add((Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 ProcessName, Id, @{n = 'CPU_s'; e = { [math]::Round($_.CPU, 1) } }, @{n = 'WS_MB'; e = { [math]::Round($_.WorkingSet64 / 1MB, 1) } } | Format-Table -AutoSize | Out-String -Width 160).TrimEnd())
    } catch { }
    try {
        $L | Set-Content -LiteralPath $Path -Encoding UTF8
        return $true
    } catch { return $false }
}

function Save-EventsInWindow {
    param([string]$Dir, [datetime]$Since, [datetime]$Until)
    $logs = @(
        'System',
        'Application',
        'Microsoft-Windows-WindowsUpdateClient/Operational',
        'Microsoft-Windows-Windows Defender/Operational',
        'Microsoft-Windows-Kernel-Power/Thermal-Operational',
        'Microsoft-Windows-TaskScheduler/Operational',
        'Microsoft-Windows-Storage-Storport/Operational',
        'Microsoft-Windows-Ntfs/Operational',
        'Microsoft-Windows-Diagnostics-Performance/Operational'
    )
    $all = New-Object System.Collections.Generic.List[object]
    foreach ($lg in $logs) {
        try {
            $ev = Get-WinEvent -FilterHashtable @{ LogName = $lg; StartTime = $Since; EndTime = $Until } -ErrorAction Stop
            if ($ev) {
                $safe = ($lg -replace '[/\\ ]', '_')
                $ev | Select-Object @{n = 'time'; e = { $_.TimeCreated } }, @{n = 'log'; e = { $lg } }, Id,
                        @{n = 'level'; e = { [int]$_.Level } }, @{n = 'level_name'; e = { $_.LevelDisplayName } },
                        ProviderName, @{n = 'message'; e = { (($_.Message -replace "`r?`n", ' ') -replace '"', '') } } |
                    Export-Csv -LiteralPath (Join-Path $Dir ("events_" + $safe + '.csv')) -NoTypeInformation -Encoding UTF8
                foreach ($e in $ev) {
                    $all.Add([pscustomobject]@{
                        time = $e.TimeCreated; log = $lg; id = $e.Id; level = [int]$e.Level
                        level_name = $e.LevelDisplayName; provider = $e.ProviderName
                        message = (($e.Message -replace "`r?`n", ' ') -replace '"', '')
                    })
                }
            }
        } catch { }
    }
    return $all
}

function Save-RecentFiles {
    param([string]$Path, [datetime]$Since)
    $dirs = @(
        (Join-Path $env:SystemRoot 'Logs'),
        (Join-Path $env:SystemRoot 'Temp'),
        (Join-Path $env:SystemRoot 'SoftwareDistribution'),
        (Join-Path $env:SystemRoot 'Prefetch'),
        (Join-Path $env:SystemRoot 'Minidump'),
        (Join-Path $env:ProgramData 'Microsoft\Windows\WER'),
        (Join-Path $env:ProgramData 'USOShared'),
        (Join-Path $env:ProgramData 'Microsoft\Windows Defender'),
        (Join-Path $env:LOCALAPPDATA 'Temp'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Explorer'),
        (Join-Path $env:APPDATA 'Microsoft\Windows\Recent'),
        ('C:\Program Files (x86)\Steam\logs'),
        ('C:\Program Files\Huorong\Sysdiag\bin')
    )
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($d in ($dirs | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        try {
            $items = Get-ChildItem -LiteralPath $d -Recurse -File -Force -Depth 4 -ErrorAction SilentlyContinue |
                     Where-Object { $_.LastWriteTime -ge $Since }
            foreach ($f in $items) {
                $rows.Add([pscustomobject]@{
                    path = $f.FullName
                    size_mb = [math]::Round($f.Length / 1MB, 3)
                    last_write = $f.LastWriteTime
                })
            }
        } catch { }
    }
    if ($rows.Count -gt 0) {
        $rows | Sort-Object { $_.size_mb } -Descending |
            Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8
    } else {
        'path,size_mb,last_write' | Set-Content -LiteralPath $Path -Encoding UTF8
    }
    return ,$rows.ToArray()
}

function Save-CbsSnippet {
    param([string]$Path, [datetime]$Since)
    try {
        $cbs = Join-Path $env:SystemRoot 'Logs\CBS\CBS.log'
        if (-not (Test-Path -LiteralPath $cbs)) { return }
        $tail = Get-Content -LiteralPath $cbs -Tail 400 -ErrorAction SilentlyContinue
        $kept = @()
        foreach ($line in $tail) {
            if ($line -match '^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})') {
                try {
                    $t = [datetime]::ParseExact(($Matches[1] + ' ' + $Matches[2]), 'yyyy-MM-dd HH:mm:ss', $null)
                    if ($t -ge $Since.AddSeconds(-5)) { $kept += $line }
                } catch { }
            }
        }
        if ($kept.Count -gt 0) {
            $kept | Set-Content -LiteralPath $Path -Encoding UTF8
        }
    } catch { }
}

function Get-KeyLogSizes {
    $paths = @(
        (Join-Path $env:SystemRoot 'Logs\CBS\CBS.log'),
        (Join-Path $env:SystemRoot 'SoftwareDistribution\ReportingEvents.log'),
        (Join-Path $env:SystemRoot 'SoftwareDistribution\DataStore\DataStore.edb'),
        (Join-Path $env:SystemRoot 'System32\winevt\Logs\System.evtx'),
        (Join-Path $env:SystemRoot 'System32\winevt\Logs\Application.evtx'),
        (Join-Path $env:SystemRoot 'System32\winevt\Logs\Microsoft-Windows-WindowsUpdateClient%4Operational.evtx'),
        (Join-Path $env:SystemRoot 'System32\winevt\Logs\Microsoft-Windows-Windows Defender%4Operational.evtx'),
        'C:\pagefile.sys',
        'H:\pagefile.sys',
        ('C:\Program Files\Huorong\Sysdiag\bin\hipsdaemon.log'),
        ('C:\Program Files\Huorong\Sysdiag\bin\bugreport.exe.log'),
        ('C:\Program Files (x86)\Steam\logs\content_log.txt')
    )
    $h = @{}
    foreach ($p in $paths) {
        $size = -1
        try {
            $fi = Get-Item -LiteralPath $p -Force -ErrorAction Stop
            $size = [long]$fi.Length
        } catch { $size = -1 }
        $h[$p] = $size
    }
    return $h
}

# ============================ 初始化计数器 ============================
Initialize-PerformanceCounters
$warm = 0
foreach ($x in $script:pcList) { try { $null = $x.PC.NextValue(); $warm++ } catch { } }

# ============================ 主流程：监视 ============================
$script:triggered = $false
$script:triggerInfo = $null
$script:preBuffer = New-Object System.Collections.Generic.Queue[string]

Write-Log ('启动监视：阈值={0}% 连续={1}次 采样间隔={2}s 采集时长={3}s 最长监视={4}h 运行目录={5}' -f $Threshold, $ConsecutiveSamples, $WatchIntervalSec, $CollectSeconds, $WatchHours, $runDir)
Write-Log ('计数器对象：{0} 个（初始化失败 {1} 个，预热 {2} 个）' -f $script:pcList.Count, $script:pcInitErrors, $warm)
'watch_phase: 监视阶段（每行一次采样；仅占用率，轻量）' | Set-Content -LiteralPath $script:watchCsv -Encoding UTF8
'time,busy_total,busy_max,mbps_total,idle_sec,per_disk' | Add-Content -LiteralPath $script:watchCsv -Encoding UTF8

$ds0 = Get-DiskSnapshot
$ds0Text = (@($ds0.PerDisk) | ForEach-Object { '{0}={1}%@{2}MB/s' -f $_.Instance, $_.Busy, $_.MBps }) -join '; '
Write-Log ('当前物理磁盘占用快照：' + $ds0Text)
Write-Log ('空闲时长：{0} 秒（判断当前是否无人操作）' -f (Get-IdleSeconds))

$watchStart = Get-Date
$watchUntil = if ($Test) { (Get-Date) } else { $watchStart.AddHours($WatchHours) }
$consec = 0
$snap = $null
$idleSec = $null

while (-not $script:triggered) {
    $now = Get-Date
    if (-not $Test -and $now -ge $watchUntil) { break }

    $snap = Get-DiskSnapshot
    $idleSec = Get-IdleSeconds
    $perDiskText = (@($snap.PerDisk) | ForEach-Object { '{0}={1}%@{2}MB/s' -f $_.Instance, $_.Busy, $_.MBps }) -join '; '
    $row = ('{0},{1},{2},{3},{4},"{5}"' -f $snap.Time.ToString('yyyy-MM-dd HH:mm:ss'), $snap.BusyTotal, $snap.BusyMax, $snap.MBpsTotal, $idleSec, $perDiskText)
    try { Add-Content -LiteralPath $script:watchCsv -Value $row -Encoding UTF8 } catch { }
    $script:preBuffer.Enqueue($row)
    while ($script:preBuffer.Count -gt 90) { [void]$script:preBuffer.Dequeue() }

    $hit = $false
    if ($null -ne $snap.BusyTotal -and $snap.BusyTotal -gt $Threshold) { $hit = $true }
    if ($null -ne $snap.BusyMax   -and $snap.BusyMax   -gt $Threshold) { $hit = $true }

    if ($Test) {
        $consec = $ConsecutiveSamples
    } elseif ($hit) {
        $consec++
    } else {
        if ($consec -gt 0) { Write-Log ('占用率回落到阈值以下（总 {0}%），连续计数清零' -f $snap.BusyTotal) }
        $consec = 0
    }

    if ($consec -ge $ConsecutiveSamples) {
        $script:triggered = $true
        $reasonText = '磁盘占用持续超过阈值 ' + $Threshold + '%'
        if ($Test) { $reasonText = '自测立即触发' }
        $script:triggerInfo = [pscustomobject]@{
            time            = $snap.Time.ToString('yyyy-MM-dd HH:mm:ss.fff')
            reason          = $reasonText
            busy_total      = $snap.BusyTotal
            busy_max        = $snap.BusyMax
            mbps_total      = $snap.MBpsTotal
            idle_sec        = $idleSec
            per_disk        = @($snap.PerDisk)
            collect_seconds = $CollectSeconds
            threshold       = $Threshold
        }
        Write-Log ('!! 触发采集：' + ($script:triggerInfo | ConvertTo-Json -Depth 6 -Compress))
        break
    }

    Start-Sleep -Seconds $WatchIntervalSec
}

if (-not $script:triggered) {
    Write-Log '最长监视时间结束，未触发（磁盘占用未持续超过阈值）。'
    $status = @()
    $status += '状态: 未触发'
    $status += ('监视开始: {0}' -f $watchStart)
    $status += ('监视结束: {0}' -f (Get-Date))
    $status += ('阈值: {0}%  连续次数: {1}' -f $Threshold, $ConsecutiveSamples)
    $status += ('运行目录: {0}' -f $runDir)
    $status | Set-Content -LiteralPath (Join-Path $runDir 'STATUS.txt') -Encoding UTF8
    if ($script:locked) { try { Remove-Item -LiteralPath $script:lockFile -Force } catch { } }
    exit 0
}

# ============================ 主流程：采集 ============================
$triggerAt = Get-Date

try { $script:triggerInfo | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runDir 'trigger.json') -Encoding UTF8 } catch { }
try {
    $pre = @('time,busy_total,busy_max,mbps_total,idle_sec,per_disk') + @($script:preBuffer.ToArray())
    $pre | Set-Content -LiteralPath (Join-Path $runDir 'pre_trigger.csv') -Encoding UTF8
} catch { }

Write-Log '触发后保存 T0 现场快照（进程/服务/系统信息）...'
Save-ProcessSnapshot -Path (Join-Path $runDir 'processes_T0.csv')  | Out-Null
Save-Services        -Path (Join-Path $runDir 'services_T0.csv')   | Out-Null
Save-SystemInfo      -Path (Join-Path $runDir 'system_info.txt')   | Out-Null

$logSizes0 = Get-KeyLogSizes

'time,"obj","instance","counter",value' | Set-Content -LiteralPath $script:counterCsv -Encoding UTF8
'time,pid,"name",cpu_pct,io_data_bps,io_read_bps,io_write_bps,io_other_bps,data_ops,read_ops,write_ops,page_faults,ws_mb,private_mb,threads,handles,pool_paged_mb,pool_nonpaged_mb,priv_pct,user_pct' |
    Set-Content -LiteralPath $script:processCsv -Encoding UTF8

$collectStart = Get-Date
$collectEnd   = $collectStart.AddSeconds($CollectSeconds)
Write-Log ('开始 {0} 秒高密度采集（计数器 + 每进程资源）...' -f $CollectSeconds)

$midDone = $false
$nextLog = $collectStart.AddSeconds(30)
$sampleCount = 0
while ((Get-Date) -lt $collectEnd) {
    $cycleStart = Get-Date
    Collect-CounterSample
    Collect-ProcessSample
    $sampleCount++
    $now = Get-Date
    if (-not $midDone -and ($now - $collectStart).TotalSeconds -ge ($CollectSeconds / 2)) {
        Save-ProcessSnapshot -Path (Join-Path $runDir 'processes_mid.csv') | Out-Null
        $midDone = $true
        Write-Log '已保存 T+50% 进程快照'
    }
    if ($now -ge $nextLog) {
        $s = Get-DiskSnapshot
        Write-Log ('采集中… 已 {0} 秒 / {1} 秒，当前总占用 {2}% 单盘最高 {3}%，吞吐 {4} MB/s' -f [int]($now - $collectStart).TotalSeconds, $CollectSeconds, $s.BusyTotal, $s.BusyMax, $s.MBpsTotal)
        $nextLog = $nextLog.AddSeconds(30)
    }
    $elapsedMs = ((Get-Date) - $cycleStart).TotalMilliseconds
    if ($elapsedMs -lt 900) { Start-Sleep -Milliseconds ([int](900 - $elapsedMs)) }
}
$collectStop = Get-Date
Write-Log ('采集阶段结束，共 {0} 轮采样，计数器错误 {1} 次' -f $sampleCount, $script:collectErrors)

# ============================ 主流程：收尾信息 ============================
Save-ProcessSnapshot -Path (Join-Path $runDir 'processes_end.csv') | Out-Null
Save-Services        -Path (Join-Path $runDir 'services_end.csv')  | Out-Null
Write-Log '保存事件日志（触发时刻→现在）...'
$evRows = Save-EventsInWindow -Dir $runDir -Since $triggerAt.AddMinutes(-1) -Until (Get-Date)
Write-Log '保存窗口内写入过的文件...'
$fileRows = Save-RecentFiles -Path (Join-Path $runDir 'files_written.csv') -Since $triggerAt
Write-Log '保存 CBS 组件安装日志片段...'
Save-CbsSnippet -Path (Join-Path $runDir 'cbs_window.log') -Since $triggerAt
$logSizes1 = Get-KeyLogSizes
Write-Log '生成摘要 SUMMARY.txt ...'

# ============================ 生成中文摘要 ============================
$L = New-Object System.Collections.Generic.List[string]
$L.Add('============================================================')
$L.Add(' 磁盘占用取证报告（DiskWatchOneShot）')
$L.Add('============================================================')
$L.Add('')

# 1) 触发信息
$L.Add('【一、触发信息】')
try {
    $ti = $script:triggerInfo
    $L.Add(('触发时间      : {0}' -f $ti.time))
    $L.Add(('触发原因      : {0}' -f $ti.reason))
    $L.Add(('总占用率      : {0} %（阈值 {1}%）' -f $ti.busy_total, $ti.threshold))
    $L.Add(('单盘最高占用  : {0} %' -f $ti.busy_max))
    $L.Add(('总读写速率    : {0} MB/s' -f $ti.mbps_total))
    $L.Add(('触发时空闲时长: {0} 秒（即“无人操作”持续时长，空表示取不到）' -f $ti.idle_sec))
    $L.Add('各磁盘:')
    foreach ($d in @($ti.per_disk)) {
        $L.Add(('  - {0}  占用 {1}%  速率 {2} MB/s' -f $d.Instance, $d.Busy, $d.MBps))
    }
} catch { $L.Add('（触发信息解析失败：' + $_.Exception.Message + '）') }
$L.Add(('采集窗口      : {0} → {1}（共 {2} 秒）' -f $collectStart.ToString('yyyy-MM-dd HH:mm:ss'), $collectStop.ToString('yyyy-MM-dd HH:mm:ss'), [int]($collectStop - $collectStart).TotalSeconds))
$L.Add(('采样轮数      : {0} 轮（计数器 + 每进程）' -f $sampleCount))
$L.Add(('计数器错误次数: {0}' -f $script:collectErrors))
$L.Add('')

# 读取数据
$cRows = @(); $pRows = @()
try { $cRows = @(Import-Csv -LiteralPath $script:counterCsv -Encoding UTF8 -ErrorAction SilentlyContinue) } catch { }
try { $pRows = @(Import-Csv -LiteralPath $script:processCsv -Encoding UTF8 -ErrorAction SilentlyContinue) } catch { }

# 2) 物理/逻辑磁盘统计
$L.Add('【二、磁盘活动统计（采集窗口内）】')
try {
    $physIdle  = @($cRows | Where-Object { $_.obj -match 'physicaldisk' -and $_.counter -match 'idle time' })
    $physBytes = @($cRows | Where-Object { $_.obj -match 'physicaldisk' -and $_.counter -match '^disk bytes/sec$' })
    $physRead  = @($cRows | Where-Object { $_.obj -match 'physicaldisk' -and $_.counter -match 'disk read bytes/sec' })
    $physWrite = @($cRows | Where-Object { $_.obj -match 'physicaldisk' -and $_.counter -match 'disk write bytes/sec' })
    $physLat   = @($cRows | Where-Object { $_.obj -match 'physicaldisk' -and $_.counter -match 'avg\. disk sec/transfer' })
    $instances = @($physIdle | ForEach-Object { $_.instance } | Where-Object { $_ -notmatch '^_total$|^总计$' } | Select-Object -Unique)
    if ($instances.Count -eq 0) { $L.Add('（未解析到物理磁盘样本）') }
    function Convert-ToMb {
        param($Rows)
        $a = @($Rows | ForEach-Object { [double]$_.value })
        if ($a.Count -eq 0) { return 0 }
        return [math]::Round((($a | Measure-Object -Average).Average) / 1MB, 2)
    }
    function Get-MaxOf {
        param($Rows, [double]$Div = 1.0)
        $a = @($Rows | ForEach-Object { [double]$_.value })
        if ($a.Count -eq 0) { return 0 }
        return [math]::Round((($a | Measure-Object -Maximum).Maximum) / $Div, 2)
    }
    foreach ($i in $instances) {
        $idleVals = @($physIdle | Where-Object { $_.instance -eq $i } | ForEach-Object { [double]$_.value })
        $busyAvg = $null; $busyMax = $null
        if ($idleVals.Count -gt 0) {
            $busyAvg = [math]::Round(100 - (($idleVals | Measure-Object -Average).Average), 1)
            $busyMax = [math]::Round(100 - (($idleVals | Measure-Object -Minimum).Minimum), 1)
        }
        $avgBytes = Convert-ToMb @($physBytes | Where-Object { $_.instance -eq $i })
        $maxBytes = Get-MaxOf @($physBytes | Where-Object { $_.instance -eq $i }) 1MB
        $avgRead  = Convert-ToMb @($physRead  | Where-Object { $_.instance -eq $i })
        $maxRead  = Get-MaxOf @($physRead  | Where-Object { $_.instance -eq $i }) 1MB
        $avgWrite = Convert-ToMb @($physWrite | Where-Object { $_.instance -eq $i })
        $maxWrite = Get-MaxOf @($physWrite | Where-Object { $_.instance -eq $i }) 1MB
        $latVals  = @($physLat | Where-Object { $_.instance -eq $i } | ForEach-Object { [double]$_.value })
        $latAvgMs = if ($latVals.Count -gt 0) { [math]::Round((($latVals | Measure-Object -Average).Average) * 1000, 2) } else { 0 }
        $latMaxMs = if ($latVals.Count -gt 0) { [math]::Round((($latVals | Measure-Object -Maximum).Maximum) * 1000, 2) } else { 0 }
        $L.Add(('磁盘 {0}:' -f $i))
        $L.Add(('  占用率  平均 {0}% / 最高 {1}%' -f $busyAvg, $busyMax))
        $L.Add(('  总吞吐  平均 {0} MB/s / 最高 {1} MB/s（约合计 {2} MB）' -f $avgBytes, $maxBytes, [math]::Round($avgBytes * $CollectSeconds, 1)))
        $L.Add(('  读      平均 {0} MB/s / 最高 {1} MB/s' -f $avgRead, $maxRead))
        $L.Add(('  写      平均 {0} MB/s / 最高 {1} MB/s（约合计 {2} MB）' -f $avgWrite, $maxWrite, [math]::Round($avgWrite * $CollectSeconds, 1)))
        $L.Add(('  单次IO  平均 {0} ms / 最高 {1} ms' -f $latAvgMs, $latMaxMs))
    }
    $L.Add('')
    $L.Add('各卷吞吐（平均 / 最高 MB/s）：')
    $logBytes = @($cRows | Where-Object { $_.obj -match 'logicaldisk' -and $_.counter -match '^disk bytes/sec$' })
    foreach ($i in @($logBytes | ForEach-Object { $_.instance } | Where-Object { $_ -notmatch '^_total$|^总计$' } | Select-Object -Unique)) {
        $rows = @($logBytes | Where-Object { $_.instance -eq $i })
        $vals = @($rows | ForEach-Object { [double]$_.value })
        if ($vals.Count -gt 0) {
            $L.Add(('  卷 {0}: 平均 {1} MB/s，最高 {2} MB/s' -f $i, [math]::Round((($vals | Measure-Object -Average).Average) / 1MB, 2), [math]::Round((($vals | Measure-Object -Maximum).Maximum) / 1MB, 2)))
        }
    }
} catch { $L.Add('（磁盘统计解析失败：' + $_.Exception.Message + '）') }
$L.Add('')

# 3) CPU / 内存
$L.Add('【三、CPU 与内存】')
try {
    $cpu = @($cRows | Where-Object { $_.obj -match 'processor' -and $_.counter -match '% processor time' } | ForEach-Object { [double]$_.value })
    if ($cpu.Count -gt 0) { $L.Add(('CPU 平均占用 : {0} %，最高 {1} %' -f [math]::Round(($cpu | Measure-Object -Average).Average, 1), [math]::Round(($cpu | Measure-Object -Maximum).Maximum, 1))) }
    $avail = @($cRows | Where-Object { $_.obj -match 'memory' -and $_.counter -match 'available mbytes' } | ForEach-Object { [double]$_.value })
    if ($avail.Count -gt 0) { $L.Add(('可用内存 最低 : {0} MB' -f [math]::Round(($avail | Measure-Object -Minimum).Minimum, 0))) }
    $pg = @($cRows | Where-Object { $_.obj -match 'memory' -and $_.counter -match '^pages/sec$' } | ForEach-Object { [double]$_.value })
    if ($pg.Count -gt 0) { $L.Add(('Pages/sec 最高 : {0}（内存/页面文件压力）' -f [math]::Round(($pg | Measure-Object -Maximum).Maximum, 1))) }
    $pw = @($cRows | Where-Object { $_.obj -match 'memory' -and $_.counter -match 'page writes/sec' } | ForEach-Object { [double]$_.value })
    if ($pw.Count -gt 0) { $L.Add(('Page Writes/sec 最高 : {0}' -f [math]::Round(($pw | Measure-Object -Maximum).Maximum, 1))) }
    $pfr = @($cRows | Where-Object { $_.obj -match 'memory' -and $_.counter -match 'page reads/sec' } | ForEach-Object { [double]$_.value })
    if ($pfr.Count -gt 0) { $L.Add(('Page Reads/sec 最高 : {0}' -f [math]::Round(($pfr | Measure-Object -Maximum).Maximum, 1))) }
} catch { $L.Add('（CPU/内存解析失败：' + $_.Exception.Message + '）') }
$L.Add('')

# 4) 进程 IO Top
$L.Add('【四、进程磁盘 I/O Top（按窗口内单次采样最高值排序）】')
$L.Add('说明：IOData/IORead/IOWrite 包含文件、管道、设备 I/O；管道/内存映射不一定落盘。')
try {
    $cmdMap = @{}
    try {
        $snapEnd = @(Import-Csv -LiteralPath (Join-Path $runDir 'processes_end.csv') -Encoding UTF8 -ErrorAction SilentlyContinue)
        foreach ($r in $snapEnd) {
            $cmdMap[[string]$r.ProcessId] = [pscustomobject]@{ Cmd = [string]$r.CommandLine; Exe = [string]$r.ExecutablePath; Start = [string]$r.CreationDate }
        }
    } catch { }
    $svcMap = @{}
    try {
        $svcEnd = @(Import-Csv -LiteralPath (Join-Path $runDir 'services_end.csv') -Encoding UTF8 -ErrorAction SilentlyContinue)
        foreach ($s in $svcEnd) {
            if ($s.ProcessId -and $s.ProcessId -ne '0') {
                $pidKey = [string]$s.ProcessId
                if (-not $svcMap.ContainsKey($pidKey)) { $svcMap[$pidKey] = New-Object System.Collections.Generic.List[string] }
                $svcMap[$pidKey].Add([string]$s.Name)
            }
        }
    } catch { }

    $agg = @{}
    foreach ($r in $pRows) {
        $key = ([string]$r.name) + '|' + ([string]$r.pid)
        if (-not $agg.ContainsKey($key)) {
            $agg[$key] = [pscustomobject]@{
                Name = [string]$r.name; PID = [string]$r.pid; Samples = 0
                MaxWrite = 0.0; MaxRead = 0.0; MaxTotal = 0.0; SumTotal = 0.0; MaxCpu = 0.0
            }
        }
        $a = $agg[$key]
        $a.Samples++
        $w = [double]$r.io_write_bps; $rd = [double]$r.io_read_bps; $tot = [double]$r.io_data_bps; $cpu = [double]$r.cpu_pct
        if ($w -gt $a.MaxWrite) { $a.MaxWrite = $w }
        if ($rd -gt $a.MaxRead) { $a.MaxRead = $rd }
        if ($tot -gt $a.MaxTotal) { $a.MaxTotal = $tot }
        $a.SumTotal += $tot
        if ($cpu -gt $a.MaxCpu) { $a.MaxCpu = $cpu }
    }
    $ordered = @($agg.Values | Sort-Object { $_.MaxTotal } -Descending | Select-Object -First 30)
    $L.Add(('{0,-34} {1,7} {2,11} {3,11} {4,12} {5,12} {6,6}  {7}' -f '进程名', 'PID', '写最高MB/s', '读最高MB/s', '总IO最高MB/s', '平均总IOMB/s', '采样', '命令行/说明'))
    foreach ($a in $ordered) {
        $cmd = ''
        if ($cmdMap.ContainsKey($a.PID)) { $cmd = $cmdMap[$a.PID].Cmd }
        if ($svcMap.ContainsKey($a.PID)) { $svc = '服务: ' + (($svcMap[$a.PID] | Select-Object -First 5) -join ',') ; if (-not $cmd) { $cmd = $svc } }
        if ($cmd) { $cmd = ($cmd -replace '\s+', ' '); if ($cmd.Length -gt 110) { $cmd = $cmd.Substring(0, 110) + '...' } }
        $L.Add(('{0,-34} {1,7} {2,11} {3,11} {4,12} {5,12} {6,6}  {7}' -f `
            $a.Name, $a.PID,
            [math]::Round($a.MaxWrite / 1MB, 2), [math]::Round($a.MaxRead / 1MB, 2),
            [math]::Round($a.MaxTotal / 1MB, 2), [math]::Round(($a.SumTotal / [math]::Max(1, $a.Samples)) / 1MB, 2),
            $a.Samples, $cmd))
    }
    $L.Add('')
    $L.Add('按“写最高值”排名的前 15 个进程：')
    foreach ($a in @($agg.Values | Sort-Object { $_.MaxWrite } -Descending | Select-Object -First 15)) {
        $cmd = ''
        if ($cmdMap.ContainsKey($a.PID)) { $cmd = $cmdMap[$a.PID].Cmd }
        if ($cmd) { $cmd = ($cmd -replace '\s+', ' '); if ($cmd.Length -gt 90) { $cmd = $cmd.Substring(0, 90) + '...' } }
        $L.Add(('  {0} (PID {1}) 写最高 {2} MB/s, 总IO最高 {3} MB/s  {4}' -f $a.Name, $a.PID, [math]::Round($a.MaxWrite / 1MB, 2), [math]::Round($a.MaxTotal / 1MB, 2), $cmd))
    }
} catch { $L.Add('（进程 IO 解析失败：' + $_.Exception.Message + '）') }
$L.Add('')

# 5) 窗口内新启动的进程
$L.Add('【五、采集窗口内新启动的进程】')
try {
    $snapEnd2 = @(Import-Csv -LiteralPath (Join-Path $runDir 'processes_end.csv') -Encoding UTF8 -ErrorAction SilentlyContinue)
    $newProcs = @()
    foreach ($p in $snapEnd2) {
        $cd = $null
        try { $cd = [datetime]$p.CreationDate } catch { }
        if ($null -ne $cd -and $cd -ge $triggerAt.AddSeconds(-2)) {
            $newProcs += [pscustomobject]@{ Name = $p.Name; PID = $p.ProcessId; Start = $cd; Cmd = (([string]$p.CommandLine) -replace '\s+', ' ') }
        }
    }
    if ($newProcs.Count -eq 0) { $L.Add('（无）') }
    foreach ($np in ($newProcs | Sort-Object Start)) {
        $c = [string]$np.Cmd; if ($c.Length -gt 130) { $c = $c.Substring(0, 130) + '...' }
        $L.Add(('  {0}  ({1}) 启动于 {2}  {3}' -f $np.Name, $np.PID, $np.Start.ToString('HH:mm:ss'), $c))
    }
} catch { $L.Add('（解析失败：' + $_.Exception.Message + '）') }
$L.Add('')

# 6) 窗口内写过的文件（按大小）
$L.Add('【六、采集窗口内被写入过的文件 Top（按大小，仅扫描常见落盘目录）】')
try {
    $files = @(Import-Csv -LiteralPath (Join-Path $runDir 'files_written.csv') -Encoding UTF8 -ErrorAction SilentlyContinue)
    if ($files.Count -eq 0) {
        $L.Add('（未扫描到窗口内写入的文件）')
    } else {
        $files = @($files | Sort-Object { [double]$_.size_mb } -Descending | Select-Object -First 40)
        foreach ($f in $files) {
            $L.Add(('  {0,9} MB  {1}' -f $f.size_mb, $f.path))
        }
    }
} catch { $L.Add('（文件扫描解析失败：' + $_.Exception.Message + '）') }
$L.Add('')

# 7) 关键日志增长
$L.Add('【七、关键日志/文件的体积变化（采集窗口内）】')
try {
    $changed = $false
    foreach ($k in $logSizes0.Keys) {
        $s0 = [long]$logSizes0[$k]
        $s1 = [long]$logSizes1[$k]
        if ($s0 -ge 0 -and $s1 -ge 0) {
            $delta = $s1 - $s0
            if ($delta -ne 0) {
                $changed = $true
                $L.Add(('  {0,12} KB  {1}' -f [math]::Round($delta / 1KB, 1), $k))
            }
        } elseif ($s1 -ge 0 -and $s0 -lt 0) {
            $L.Add(('  （窗口内新建） {0}' -f $k))
            $changed = $true
        }
    }
    if (-not $changed) { $L.Add('（关键日志体积没有变化）') }
    if (Test-Path -LiteralPath (Join-Path $runDir 'cbs_window.log')) {
        $L.Add(('  CBS 组件安装日志窗口片段已保存：cbs_window.log（{0} 行）' -f @(Get-Content -LiteralPath (Join-Path $runDir 'cbs_window.log')).Count))
    }
} catch { $L.Add('（解析失败：' + $_.Exception.Message + '）') }
$L.Add('')

# 8) 事件日志摘要
$L.Add('【八、采集窗口内的事件日志摘要】')
try {
    if ($evRows.Count -eq 0) {
        $L.Add('（窗口内无事件）')
    } else {
        $L.Add('按日志统计：')
        foreach ($g in ($evRows | Group-Object log | Sort-Object Count -Descending)) {
            $L.Add(('  {0}: {1} 条' -f $g.Name, $g.Count))
        }
        $crit = @($evRows | Where-Object { [int]$_.level -le 3 } | Sort-Object time -Descending | Select-Object -First 40)
        $L.Add('')
        $L.Add(('警告/错误事件（最多 40 条，共 {0} 条）:' -f @($evRows | Where-Object { [int]$_.level -le 3 }).Count))
        foreach ($e in $crit) {
            $m = [string]$e.message
            if ($m.Length -gt 200) { $m = $m.Substring(0, 200) + '...' }
            $L.Add(('  {0}  [{1}] {2} (ID {3}) {4}' -f ([datetime]$e.time).ToString('HH:mm:ss'), $e.log, $e.provider, $e.id, $m))
        }
        $svcEv = @($evRows | Where-Object { $_.log -eq 'System' -and [int]$_.id -in 7036,7040,7045 })
        if ($svcEv.Count -gt 0) {
            $L.Add('')
            $L.Add(('窗口内服务状态变化（{0} 条）:' -f $svcEv.Count))
            foreach ($e in ($svcEv | Select-Object -First 20)) {
                $m = [string]$e.message; if ($m.Length -gt 140) { $m = $m.Substring(0, 140) + '...' }
                $L.Add(('  {0}  {1}' -f ([datetime]$e.time).ToString('HH:mm:ss'), $m))
            }
        }
    }
} catch { $L.Add('（事件解析失败：' + $_.Exception.Message + '）') }
$L.Add('')

$L.Add('【九、说明】')
$L.Add('  1. 本报告由一次性取证脚本自动生成；原始数据在同目录 CSV/JSON/TXT 中，可用于进一步分析。')
$L.Add('  2. “System”进程(PID 4) 的 I/O 通常是 NTFS/缓存/文件系统内核活动；杀毒软件的过滤驱动也会记到它身上。')
$L.Add('  3. 每进程 I/O 计数器包含管道与内存映射 I/O，不一定代表真实磁盘写入（例如视频解码管道）。')
$L.Add('  4. svchost 实例(PID)对应的服务名见“服务”映射，可据此判断是 Windows 搜索/更新/BITS 等哪个服务。')
$L.Add('  5. 若脚本非管理员运行，个别信息（SMART、部分系统进程命令行）可能缺失。')
$L.Add('')
$L.Add(('生成时间: {0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')))
$L.Add(('运行目录: {0}' -f $runDir))

$summaryPath = Join-Path $runDir 'SUMMARY.txt'
try {
    $L | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    $L | Set-Content -LiteralPath (Join-Path $OutputRoot 'LAST_SUMMARY.txt') -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $OutputRoot 'LATEST_RUN.txt') -Value $runDir -Encoding UTF8
    Write-Log '摘要已生成。'
} catch { Write-Log ('写摘要失败：' + $_.Exception.Message) }

$doneText = @()
$doneText += '状态: 采集完成'
$doneText += ('触发时间: {0}' -f $triggerAt.ToString('yyyy-MM-dd HH:mm:ss'))
$doneText += ('采集结束: {0}' -f $collectStop.ToString('yyyy-MM-dd HH:mm:ss'))
$doneText += ('运行目录: {0}' -f $runDir)
$doneText += ('摘要文件: {0}' -f $summaryPath)
$doneText | Set-Content -LiteralPath (Join-Path $runDir 'DONE.txt') -Encoding UTF8

if ($script:locked) { try { Remove-Item -LiteralPath $script:lockFile -Force } catch { } }
Write-Log '程序结束。'
Write-Output ('采集完成：' + $summaryPath)
exit 0
