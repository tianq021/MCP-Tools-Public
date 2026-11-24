[CmdletBinding(SupportsShouldProcess=$true)]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'service-common.ps1')
$control = [System.Threading.Mutex]::new($false, $dshControlMutex)
$locked = $false
$stopEvent = $null
try {
    $locked = Wait-ServiceMutex $control 40000
    if (!$locked) { throw 'Another DSH start or stop is in progress.' }

    $stopEvent = Open-DshStopEvent
    if ($stopEvent) {
        if (!$PSCmdlet.ShouldProcess('DSH supervisor and its server', 'Stop service')) { return }
        [void]$stopEvent.Set()
        $watch = [System.Threading.Mutex]::new($false, $dshWatchMutex)
        try {
            if (!(Wait-ServiceMutex $watch 25000)) { throw 'DSH supervisor did not stop in time.' }
            $watch.ReleaseMutex()
        } finally { $watch.Dispose() }
    }

    # Handle an instance launched before the supervisor was installed.
    if (!$stopEvent -or (Test-DshPortOccupied)) {
        $entry = 'F:\deepseekA-020-test\node_modules\@deepseek-ai\dsh\lib\bin.js'
        $entryPattern = '(?i)(?:^|[\s"''])' + [regex]::Escape($entry) + '(?=["'']?\s+web(?:\s|$))'
        $targets = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object {
            $_.CommandLine -match $entryPattern -and $_.CommandLine -match '--port\s+3080(?:\s|$)'
        })
        if (!$targets.Count -and !$stopEvent) { Write-Host 'DSH is already stopped.'; return }
        foreach ($target in $targets) {
            if ($PSCmdlet.ShouldProcess("DSH PID $($target.ProcessId)", 'Stop service')) { Stop-VerifiedProcess $target }
        }
    }
    if (!$WhatIfPreference) { Write-ServiceLog 'DSH shutdown complete. Files and saved settings are preserved.' }
} finally {
    if ($stopEvent) { $stopEvent.Dispose() }
    if ($locked) { $control.ReleaseMutex() }
    $control.Dispose()
}

