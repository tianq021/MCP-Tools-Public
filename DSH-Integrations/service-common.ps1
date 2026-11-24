function Write-ServiceLog([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format o), $Message
    Write-Host $Message
    Add-Content -LiteralPath (Join-Path $PSScriptRoot 'logs\service-control.log') -Value $line -Encoding UTF8
}
$dshControlMutex = 'Local\DeepseekA_DshControl_3080'
$dshWatchMutex = 'Local\DeepseekA_DshWatch_3080'
$dshStopEvent = 'Local\DeepseekA_DshStop_3080'

function Wait-ServiceMutex($Mutex, [int]$Milliseconds) {
    try { return $Mutex.WaitOne($Milliseconds) }
    catch [System.Threading.AbandonedMutexException] { return $true }
}

function Open-DshStopEvent {
    try { return [System.Threading.EventWaitHandle]::OpenExisting($dshStopEvent) }
    catch [System.Threading.WaitHandleCannotBeOpenedException] { return $null }
}

function Get-DshProbeState([string]$Python) {
    try {
        $state = & $Python -B (Join-Path $PSScriptRoot 'integrations\dsh-service\service.py') 2>$null
        if ($LASTEXITCODE -eq 0 -and $state) { return $state }
    } catch {}
    return 'probe_failed'
}

function Test-DshPortOccupied {
    return [bool]([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq 3080 -and $_.Address.IPAddressToString -in @('127.0.0.1','0.0.0.0','::','::ffff:127.0.0.1') })
}

function Get-OllamaExecutable {
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path -LiteralPath $candidate) { return (Get-Item -LiteralPath $candidate).FullName }
    $command = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw 'Ollama was not found. Install it or add ollama.exe to PATH.'
}
function Stop-VerifiedProcess($Snapshot) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($Snapshot.ProcessId)" -ErrorAction Stop
    if (!$current) { return }
    if ($current.CreationDate -ne $Snapshot.CreationDate -or $current.ExecutablePath -ne $Snapshot.ExecutablePath -or $current.CommandLine -ne $Snapshot.CommandLine) {
        throw "Process identity changed for PID $($Snapshot.ProcessId); nothing stopped."
    }
    Stop-Process -Id $current.ProcessId -ErrorAction Stop
    $process = Get-Process -Id $current.ProcessId -ErrorAction SilentlyContinue
    if ($process -and !$process.WaitForExit(10000)) { throw "Process $($current.ProcessId) did not stop." }
    Write-ServiceLog "Stopped $($current.Name), PID $($current.ProcessId)."
}
