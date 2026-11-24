# Install the verified build once the player closes the current game.
param([Parameter(Mandatory)][string]$ExpectedBuild,
      [Parameter(Mandatory)][string]$ExpectedInstalled)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$built = Join-Path $project 'dist\SlimeRancher2Bridge.dll'
$mods = 'H:\SteamLibrary\steamapps\common\Slime Rancher 2\Mods'
$installed = Join-Path $mods 'SlimeRancher2Bridge.dll'
$log = Join-Path $project 'out\bridge\deploy-after-exit.log'
function Write-Status([string]$message) {
    Add-Content -LiteralPath $log -Value ("{0:o} {1}" -f [datetime]::Now, $message) -Encoding UTF8
}
function Read-Sha256([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $sha.Dispose(); $stream.Dispose() }
}
try {
    $running = @(Get-Process -Name SlimeRancher2 -ErrorAction SilentlyContinue)
    Write-Status "Waiting for game exit. PIDs $($running.Id)"
    foreach ($gameProcess in $running) { $gameProcess.WaitForExit() }
    if ((Read-Sha256 $built) -ne $ExpectedBuild) { throw 'Staged build changed' }
    $current = Read-Sha256 $installed
    if ($current -eq $ExpectedBuild) { Write-Status 'Already installed'; exit 0 }
    if ($current -ne $ExpectedInstalled) { throw 'Installed DLL changed independently' }
    $backup = Join-Path $project ("backups\SlimeRancher2Bridge-" + $current.Substring(0, 12) + ".dll")
    if (!(Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $installed -Destination $backup }
    if ((Read-Sha256 $backup) -ne $current) { throw 'Backup verification failed' }
    $temp = Join-Path $mods ("SlimeRancher2Bridge-" + [guid]::NewGuid() + ".pending")
    try {
        Copy-Item -LiteralPath $built -Destination $temp
        if ((Read-Sha256 $temp) -ne $ExpectedBuild) { throw 'Staging verification failed' }
        if (Get-Process -Name SlimeRancher2 -ErrorAction SilentlyContinue) { throw 'Game reopened before deployment' }
        [IO.File]::Replace($temp, $installed, [System.Management.Automation.Language.NullString]::Value)
        if ((Read-Sha256 $installed) -ne $ExpectedBuild) { throw 'Installed verification failed' }
        Write-Status "Installed $ExpectedBuild"
    } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp } }
} catch { Write-Status ("Failed: " + $_.Exception.Message); exit 1 }

