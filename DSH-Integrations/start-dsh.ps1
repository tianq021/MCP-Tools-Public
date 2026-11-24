$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'service-common.ps1')

$control = [System.Threading.Mutex]::new($false, $dshControlMutex)
$locked = $false
$stopEvent = $null
$watcher = $null
try {
    $locked = Wait-ServiceMutex $control 40000
    if (!$locked) { throw 'Another DSH start or stop is in progress.' }

    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $stopEvent = Open-DshStopEvent
    if ($stopEvent -and $stopEvent.WaitOne(0)) { throw 'DSH is still stopping.' }
    if (!$stopEvent) {
        if (Test-DshPortOccupied) {
            if ((Get-DshProbeState $python) -ne 'ready') {
                throw 'Port 3080 is occupied but the authenticated DSH API is unavailable.'
            }
            Write-Output 'DSH is already running. Use open-web.cmd to connect.'
            exit 0
        }
        $stopEvent = [System.Threading.EventWaitHandle]::new(
            $false, [System.Threading.EventResetMode]::ManualReset, $dshStopEvent)
        $watchScript = Join-Path $PSScriptRoot 'watch-dsh.ps1'
        $watchArgs = @{
            FilePath = (Get-Command powershell.exe -ErrorAction Stop).Source
            ArgumentList = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"' + $watchScript + '"'))
            WorkingDirectory = $PSScriptRoot
            WindowStyle = 'Hidden'
            PassThru = $true
        }
        $watcher = Start-Process @watchArgs
        try { $null = $watcher.Handle }
        catch [InvalidOperationException] { if (!$watcher.HasExited) { throw } }
    }

    $deadline = [datetime]::UtcNow.AddSeconds(32)
    do {
        if ((Get-DshProbeState $python) -eq 'ready') {
            Write-Output 'DSH is ready at http://127.0.0.1:3080. Use open-web.cmd to connect.'
            exit 0
        }
        if ($watcher -and $watcher.HasExited) {
            throw "DSH supervisor exited ($($watcher.ExitCode)). See logs\dsh-watch.log."
        }
        Start-Sleep -Milliseconds 750
    } while ([datetime]::UtcNow -lt $deadline)
    Write-Output 'DSH startup is still pending. See logs\dsh-watch.log.'
} finally {
    if ($watcher) { $watcher.Dispose() }
    if ($stopEvent) { $stopEvent.Dispose() }
    if ($locked) { $control.ReleaseMutex() }
    $control.Dispose()
}
