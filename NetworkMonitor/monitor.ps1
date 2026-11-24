param(
    [string]$AdapterName = 'WLAN',
    [ValidateRange(0.2, 3600)][double]$IntervalSeconds = 2,
    [ValidateRange(0, 2147483647)][int]$DurationSeconds = 0,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'logs')
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$lock = $null
$writer = $null
$stopFile = Join-Path $OutputDirectory 'stop.request'
try {
    # An exclusive handle prevents two monitors writing to the same output folder.
    $lock = [IO.File]::Open((Join-Path $OutputDirectory 'monitor.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
    $adapter = [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object { $_.Name -eq $AdapterName } | Select-Object -First 1
    if ($null -eq $adapter) { throw "Network adapter not found: $AdapterName" }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $csv = Join-Path $OutputDirectory "traffic-$stamp.csv"
    $metadata = Join-Path $OutputDirectory "session-$stamp.json"
    [ordered]@{
        StartedAt = (Get-Date).ToString('o'); PID = $PID
        AdapterName = $adapter.Name; AdapterDescription = $adapter.Description
        IntervalSeconds = $IntervalSeconds; DurationSeconds = $DurationSeconds
        CsvPath = $csv; Units = 'Kbps = bits/second / 1000; KiB/s = bytes/second / 1024'
        Scope = 'Adapter totals; does not identify per-process traffic or send probe requests.'
    } | ConvertTo-Json | Set-Content -LiteralPath $metadata -Encoding UTF8
    $writer = New-Object IO.StreamWriter($csv, $false, (New-Object Text.UTF8Encoding($true)))
    $writer.AutoFlush = $true
    $writer.WriteLine('Timestamp,Adapter,ElapsedSeconds,ReceiveBytes,SendBytes,ReceiveKbps,SendKbps,ReceiveKiBps,SendKiBps,Status')
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $previous = $adapter.GetIPv4Statistics()
    $previousTime = $timer.Elapsed.TotalSeconds
    $culture = [Globalization.CultureInfo]::InvariantCulture
    Write-Host "Monitoring $AdapterName every $IntervalSeconds seconds. CSV: $csv"
    Write-Host 'Press Ctrl+C or run stop.cmd to stop.'
    while ($true) {
        Start-Sleep -Milliseconds ([int]($IntervalSeconds * 1000))
        if (Test-Path -LiteralPath $stopFile) { break }
        $current = $adapter.GetIPv4Statistics()
        $now = $timer.Elapsed.TotalSeconds
        $elapsed = $now - $previousTime
        $rx = $current.BytesReceived - $previous.BytesReceived
        $tx = $current.BytesSent - $previous.BytesSent
        $status = 'OK'
        if ($rx -lt 0 -or $tx -lt 0) { $status = 'CounterReset'; $rx = 0; $tx = 0 }
        $values = @(
            (Get-Date).ToString('o'), ('"' + $AdapterName.Replace('"', '""') + '"'),
            $elapsed.ToString('F3', $culture), $rx, $tx,
            ($rx * 8 / $elapsed / 1000).ToString('F3', $culture),
            ($tx * 8 / $elapsed / 1000).ToString('F3', $culture),
            ($rx / $elapsed / 1024).ToString('F3', $culture),
            ($tx / $elapsed / 1024).ToString('F3', $culture), $status
        )
        $writer.WriteLine(($values -join ','))
        Write-Host ('{0}  RX {1} Kbps  TX {2} Kbps' -f (Get-Date -Format 'HH:mm:ss'), $values[5], $values[6])
        $previous = $current
        $previousTime = $now
        if ($DurationSeconds -gt 0 -and $now -ge $DurationSeconds) { break }
    }
} catch {
    $errorPath = Join-Path $OutputDirectory ('error-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.txt')
    $_ | Out-String | Set-Content -LiteralPath $errorPath -Encoding UTF8
    Write-Error $_
} finally {
    if ($null -ne $writer) { $writer.Dispose() }
    if ($null -ne $lock) { $lock.Dispose() }
}
