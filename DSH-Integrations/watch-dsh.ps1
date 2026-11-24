$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'service-common.ps1')

function Write-DshWatchLog([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format o), $Message
    Add-Content -LiteralPath (Join-Path $PSScriptRoot 'logs\dsh-watch.log') -Encoding UTF8 -Value $line
}

function Start-DshProcess {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $outLog = Join-Path $PSScriptRoot "logs\web-$stamp.stdout.log"
    $errLog = Join-Path $PSScriptRoot "logs\web-$stamp.stderr.log"
    $serverArgs = @($entry,'web','--no-open','--host','127.0.0.1','--port','3080')
    $mobileState = Join-Path $PSScriptRoot 'logs\mobile-access.json'
    if (Test-Path -LiteralPath $mobileState) {
        $mobile = Get-Content -LiteralPath $mobileState -Raw | ConvertFrom-Json
        $wifiAddresses = @([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
            Where-Object { $_.NetworkInterfaceType -eq 'Wireless80211' -and $_.OperationalStatus -eq 'Up' } |
            ForEach-Object { $_.GetIPProperties().UnicastAddresses } |
            ForEach-Object { $_.Address.IPAddressToString })
        if ($mobile.address -in $wifiAddresses) {
            $serverArgs += @('--trusted-host', "$($mobile.address):3080")
        }
    }

    $launch = @{
        FilePath = $nodePath
        ArgumentList = $serverArgs
        WorkingDirectory = $PSScriptRoot
        WindowStyle = 'Hidden'
        RedirectStandardOutput = $outLog
        RedirectStandardError = $errLog
        PassThru = $true
    }
    $server = Start-Process @launch
    # Keep the Windows process handle open so ExitCode is still readable after exit.
    try { $null = $server.Handle }
    catch [InvalidOperationException] { if (!$server.HasExited) { throw } }
    [ordered]@{
        pid = $server.Id
        started = (Get-Date -Format o)
        executable = $nodePath
        entry = $entry
        stdout = $outLog
        stderr = $errLog
        url = 'http://127.0.0.1:3080'
        dshHome = $env:DSH_HOME
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'logs\server.json') -Encoding UTF8
    Write-DshWatchLog "started pid=$($server.Id) stdout=$([IO.Path]::GetFileName($outLog)) stderr=$([IO.Path]::GetFileName($errLog))"
    return $server
}

$mutex = [System.Threading.Mutex]::new($false, $dshWatchMutex)
$locked = $false
$stopEvent = $null
try {
    $locked = Wait-ServiceMutex $mutex 0
    if (!$locked) { exit 0 }
    $stopEvent = Open-DshStopEvent
    if (!$stopEvent) { throw 'Run start-dsh.ps1 to start the DSH supervisor.' }

    $env:DSH_HOME = 'F:\deepseekA\dsh-home-020-20261007'
    $env:npm_config_cache = Join-Path $PSScriptRoot 'npm-cache'
    $env:npm_config_prefix = Join-Path $PSScriptRoot 'npm-prefix'
    $env:TEMP = Join-Path $PSScriptRoot 'temp'
    $env:TMP = $env:TEMP
    $env:XDG_CACHE_HOME = Join-Path $PSScriptRoot 'cache'
    $env:XDG_CONFIG_HOME = Join-Path $PSScriptRoot 'config'
    $env:XDG_DATA_HOME = Join-Path $PSScriptRoot 'data'
    $env:COREPACK_HOME = Join-Path $PSScriptRoot 'corepack'
    $env:PNPM_HOME = Join-Path $PSScriptRoot 'pnpm'
    $nodePath = (Get-Command node.exe -ErrorAction Stop).Source
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $entry = 'F:\deepseekA-020-test\node_modules\@deepseek-ai\dsh\lib\bin.js'
    if (!(Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'DSH is not installed.' }

    $delay = 2
    while (!$stopEvent.WaitOne(0)) {
        if (Test-DshPortOccupied) {
            Write-DshWatchLog 'supervisor_exit reason=port_occupied'
            exit 1
        }
        $server = Start-DshProcess
        try {
            $uptime = [Diagnostics.Stopwatch]::StartNew()
            $nextProbe = 1
            $lastState = 'not_checked'
            $lastCheck = 'none'
            $requested = $false
            while (!$server.HasExited) {
                if ($stopEvent.WaitOne(500)) {
                    $requested = $true
                    if (!$server.HasExited) {
                        try { $server.Kill() }
                        catch [InvalidOperationException] { if (!$server.HasExited) { throw } }
                        if (!$server.WaitForExit(10000)) { throw "DSH PID $($server.Id) did not stop." }
                    }
                    break
                }
                if ($uptime.Elapsed.TotalSeconds -lt $nextProbe) { continue }
                $state = Get-DshProbeState $python
                $lastCheck = Get-Date -Format o
                if ($state -ne $lastState) {
                    Write-DshWatchLog "health pid=$($server.Id) state=$state"
                    $lastState = $state
                }
                $interval = if ($state -eq 'ready' -or $uptime.Elapsed.TotalSeconds -gt 40) { 30 } else { 2 }
                $nextProbe = $uptime.Elapsed.TotalSeconds + $interval
            }
            $exitCode = $server.ExitCode
            if ($null -eq $exitCode) { $exitCode = 'unavailable' }
            Write-DshWatchLog "exit pid=$($server.Id) code=$exitCode requested=$requested last_health=$lastState last_check=$lastCheck"
            if ($requested) { break }
            if ($uptime.Elapsed.TotalMinutes -ge 5) { $delay = 2 }
        } finally {
            $server.Dispose()
        }
        if (!$stopEvent.WaitOne(0)) {
            Write-DshWatchLog "restart_pending delay_seconds=$delay"
            if ($stopEvent.WaitOne($delay * 1000)) { break }
            $delay = [Math]::Min($delay * 2, 60)
        }
    }
} catch {
    Write-DshWatchLog "supervisor_error $($_.Exception.Message)"
    throw
} finally {
    if ($stopEvent) { $stopEvent.Dispose() }
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
