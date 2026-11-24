$ErrorActionPreference = 'Stop'
$source = $PSScriptRoot
$status = Join-Path $source 'boot-install-status.json'
$result = [ordered]@{ State='Starting'; Time=(Get-Date).ToString('o') }
try {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw 'Administrator elevation is required.' }
    $dest = Join-Path $env:ProgramFiles 'StartupWatchBoot'
    $data = Join-Path $env:ProgramData 'StartupWatchBoot'
    $logs = Join-Path $data 'logs'
    $taskName = 'StartupWatch-BootCapture'
    $testName = 'StartupWatch-BootCapture-Verify'
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Boot task already exists; inspect it before replacing.' }
    New-Item -ItemType Directory -Path $dest,$data,$logs -Force | Out-Null
    # SYSTEM must not execute a binary writable by ordinary accounts.
    & icacls.exe $dest /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to protect binary directory.' }
    & icacls.exe $data /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to protect log directory.' }
    Copy-Item -LiteralPath (Join-Path $source 'BootCapture.exe') -Destination $dest -Force
    $exe = Join-Path $dest 'BootCapture.exe'
    $runPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $oldRun = (Get-ItemProperty -LiteralPath $runPath -Name StartupWatch -ErrorAction SilentlyContinue).StartupWatch
    [ordered]@{ User=[Security.Principal.WindowsIdentity]::GetCurrent().Name; OldRun=$oldRun; TaskName=$taskName; Time=(Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $data 'previous-config.json') -Encoding UTF8
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 35)
    $testLogs = Join-Path $data 'verification'
    $testAction = New-ScheduledTaskAction -Execute $exe -Argument ('--seconds 20 --out "{0}"' -f $testLogs) -WorkingDirectory $dest
    Register-ScheduledTask -TaskName $testName -Action $testAction -Principal $principal -Settings $settings -Force | Out-Null
    try {
        Start-ScheduledTask -TaskName $testName
        Start-Sleep -Seconds 4
        $probe = Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -ArgumentList '/d /c "echo BOOTCAPTURE_PROBE & ping -n 3 127.0.0.1 >nul"' -WindowStyle Hidden -PassThru
        $probe.WaitForExit()
        $until=(Get-Date).AddSeconds(40)
        do { Start-Sleep -Seconds 1; $test=Get-ScheduledTask -TaskName $testName } while ($test.State -eq 'Running' -and (Get-Date) -lt $until)
        if ($test.State -eq 'Running') { Stop-ScheduledTask -TaskName $testName; throw 'Verification capture timed out.' }
        $info = Get-ScheduledTaskInfo -TaskName $testName
        if ($info.LastTaskResult -ne 0) { throw "Verification exit code: $($info.LastTaskResult)" }
        $raw = Get-ChildItem -LiteralPath $testLogs -Filter '*.tsv' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $raw) { throw 'No verification log.' }
        $text = Get-Content -LiteralPath $raw.FullName -Raw
        if ($text -notmatch 'TRACE_START_READY' -or $text -notmatch "START\t$($probe.Id)\tcmd.exe" -or $text -notmatch 'BOOTCAPTURE_PROBE') { throw 'Verification did not capture the probe event and command line.' }
        if ($text -match 'SNAPSHOT_ERROR|FIRST_ERROR|NEXT_ERROR') { throw 'Process snapshot API error during verification.' }
        $health = [regex]::Match($text,'HEALTH\ttoolhelp=(\d+)\tdotnet=(\d+)')
        if (-not $health.Success -or [int]$health.Groups[1].Value -lt 40 -or [Math]::Abs([int]$health.Groups[1].Value-[int]$health.Groups[2].Value) -gt 20) { throw 'Process enumeration coverage failed verification.' }
        $result.VerificationLog=$raw.FullName
        $result.ProbePid=$probe.Id
        $result.ProcessCount=[int]$health.Groups[1].Value
    } finally { Unregister-ScheduledTask -TaskName $testName -Confirm:$false -ErrorAction SilentlyContinue }
    $action = New-ScheduledTaskAction -Execute $exe -Argument ('--out "{0}"' -f $logs) -WorkingDirectory $dest
    $triggers = @((New-ScheduledTaskTrigger -AtStartup),(New-ScheduledTaskTrigger -AtLogOn))
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description 'Diagnostic capture: boot/logon, exits 3 minutes after explorer is seen, maximum 30 minutes. No visible window.' | Out-Null
    # Retire only this project's old collector after the replacement passed its real SYSTEM test.
    if ($oldRun -and $oldRun -like '*\startup-watch\StartupWatch.exe*') { Remove-ItemProperty -LiteralPath $runPath -Name StartupWatch }
    Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $data 'installed-task.xml') -Encoding UTF8
    $result.State='Ready'; $result.Task=$taskName; $result.Executable=$exe; $result.Logs=$logs
} catch { $result.State='Failed'; $result.Error=$_.Exception.ToString() }
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $status -Encoding UTF8
if($result.State -eq 'Ready') { exit 0 } else { exit 1 }
