param([string]$GameRoot='F:\SteamLibrary\steamapps\common\After Inc', [switch]$Restart)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$projectRoot=Split-Path -Parent $PSScriptRoot
$dll=Join-Path $projectRoot 'plugin\bin\Release\net6.0\AfterIncBridge.dll'
$plugins=Join-Path $GameRoot 'BepInEx\plugins'
function AssertStopped {
    if(Get-Process -Name 'After Inc. Revival' -ErrorAction SilentlyContinue){throw 'Exit the game and confirm its exit dialog before deployment.'}
    foreach($port in @(17840,17842,17843)){
        $tcp=[Net.Sockets.TcpClient]::new()
        try { $task=$tcp.ConnectAsync('127.0.0.1',$port); if($task.Wait(300) -and $tcp.Connected){throw "Bridge port $port is still active"} }
        catch [AggregateException] { } finally { $tcp.Dispose() }
    }
}
AssertStopped
if(!(Test-Path -LiteralPath $dll)){throw 'Build output missing'}
$sourceHash=(Get-FileHash -LiteralPath $dll).Hash
$target=Join-Path $plugins 'AfterIncBridge\AfterIncBridge.dll'
$known=@($target,(Join-Path $plugins 'NativeBridgeCandidate\AfterIncBridge.dll'),(Join-Path $plugins 'MiniProbe2\AfterIncProbe2.dll'),(Join-Path $plugins 'MiniProbe3\AfterIncProbe3.dll'))
$active=@(Get-ChildItem -LiteralPath $plugins -Recurse -Filter *.dll)
foreach($file in $active){if($file.FullName -notin $known){throw "Unexpected active plugin: $($file.FullName)"}}
$stamp=Get-Date -Format yyyyMMdd-HHmmss
$backup=Join-Path $projectRoot "backups\native-release-$stamp"
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item -LiteralPath $plugins -Destination "$backup\plugins" -Recurse
Copy-Item -LiteralPath "$GameRoot\BepInEx\config" -Destination "$backup\config" -Recurse
Copy-Item -LiteralPath "$GameRoot\BepInEx\LogOutput.log" -Destination $backup
$local=Join-Path $env:USERPROFILE 'AppData\LocalLow\Ndemic Creations\After Inc. Revival'
if(Test-Path $local){Copy-Item -LiteralPath $local -Destination "$backup\local-save" -Recurse}
$steam=(Get-ItemProperty 'HKCU:\Software\Valve\Steam').SteamPath
foreach($u in Get-ChildItem "$steam\userdata" -Directory){$save=Join-Path $u.FullName '3337140';if(Test-Path $save){Copy-Item -LiteralPath $save -Destination "$backup\steam-save-$($u.Name)" -Recurse}}
New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
$stage="$target.staging-$stamp"
Copy-Item -LiteralPath $dll -Destination $stage
if((Get-FileHash -LiteralPath $stage).Hash -ne $sourceHash){throw 'Staging hash mismatch'}
AssertStopped
$renamed=@()
try {
    foreach($file in $active){$disabled="$($file.FullName).disabled-$stamp";Move-Item -LiteralPath $file.FullName -Destination $disabled;$renamed+=@{Old=$file.FullName;New=$disabled}}
    Move-Item -LiteralPath $stage -Destination $target
    if((Get-FileHash -LiteralPath $target).Hash -ne $sourceHash){throw 'Deployed hash mismatch'}
} catch {
    if(Test-Path $target){Move-Item -LiteralPath $target -Destination "$target.failed-$stamp"}
    foreach($entry in $renamed){Move-Item -LiteralPath $entry.New -Destination $entry.Old}
    throw
}
@{sha256=$sourceHash;target=$target;backup=$backup;time=(Get-Date).ToString('o')}|ConvertTo-Json|Set-Content "$backup\deployment.json" -Encoding utf8
Write-Output "Deployed and verified: $sourceHash; backup: $backup"
if($Restart){Start-Process 'steam://rungameid/3337140'}
