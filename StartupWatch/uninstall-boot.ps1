# Run elevated as the same user who installed the capture task. Logs are retained.
$ErrorActionPreference='Stop'
$backup=Join-Path $env:ProgramData 'StartupWatchBoot\previous-config.json'
$previous=Get-Content -LiteralPath $backup -Raw | ConvertFrom-Json
if($previous.User -ne [Security.Principal.WindowsIdentity]::GetCurrent().Name) { throw 'Use the original installing user account.' }
Stop-ScheduledTask -TaskName 'StartupWatch-BootCapture' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'StartupWatch-BootCapture' -Confirm:$false
if($previous.OldRun) {
    $key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -LiteralPath $key -Name StartupWatch -Value $previous.OldRun -PropertyType String -Force | Out-Null
}
Write-Output 'Boot capture removed. Previous StartupWatch Run entry restored. Logs retained.'
