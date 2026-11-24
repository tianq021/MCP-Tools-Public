param(
    [Parameter(Mandatory)][string]$GameRoot,
    [switch]$InstallCoop,
    [string]$CoopArchive,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$gamePath = (Resolve-Path -LiteralPath $GameRoot).Path.TrimEnd('\')
$exePath = Join-Path $gamePath 'Infection Free Zone.exe'
if (-not (Test-Path -LiteralPath $exePath)) { throw 'Game executable not found.' }
if (Get-Process -Name 'Infection Free Zone' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exePath }) { throw 'Exit the game before installing.' }
$gameDll = Join-Path $gamePath 'Infection Free Zone_Data\Managed\Ifz.dll'
if ((Get-FileHash -LiteralPath $gameDll).Hash -ne '56464F9C355C350D3EB274CA2E56455CA629A00A7DC02774F4E99A125009DB8F') { throw 'Unsupported game build; expected Steam build 26104777.' }
if (-not (Test-Path -LiteralPath (Join-Path $gamePath 'BepInEx\core\BepInEx.dll'))) { throw 'BepInEx 5 must already be installed.' }
$addon = Join-Path $projectRoot 'npc-enhanced\bin\Release\netstandard2.1\IfzNpcEnhanced.dll'
if (-not (Test-Path -LiteralPath $addon)) { throw 'Build npc-enhanced/IfzNpcEnhanced.csproj first.' }
if (-not $CoopArchive) { $CoopArchive = Join-Path $projectRoot 'ifz-coop-mod-0.3.0-rc7-p7.zip' }
$expectedCoop = 'A36C10726AA1B127BF1F06F5F6B87F974A7869CA342387ACE3CBA640C64CAD8B'
$coopTarget = Join-Path $gamePath 'BepInEx\plugins\IFZMultiplayer\IFZ.Multiplayer.GameIntegration.dll'
$files = [Collections.Generic.List[object]]::new()
$zip = $null
try {
    if ($InstallCoop) {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $CoopArchive).Path)
        foreach ($entry in $zip.Entries) {
            if ($entry.Length -eq 0) { continue }
            $relative = $entry.FullName.Replace('/', '\')
            # Explicit allowlist; never execute launch scripts from the archive during installation.
            # Steam retail installs retain their original Steam API and AppID.
            if ($relative -notmatch '^BepInEx\\plugins\\(IFZMultiplayer|EconomyConfigBridge)\\[^\\]+\.dll$') { continue }
            $destination = [IO.Path]::GetFullPath((Join-Path $gamePath $relative))
            if (-not $destination.StartsWith($gamePath + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Archive path escaped game directory.' }
            $files.Add([pscustomobject]@{ Relative = $relative; Entry = $entry; Source = $null })
        }
        $integration = $files | Where-Object Relative -eq 'BepInEx\plugins\IFZMultiplayer\IFZ.Multiplayer.GameIntegration.dll'
        if (-not $integration) { throw 'Missing integration DLL in archive.' }
        $stream = $integration.Entry.Open(); $sha = [Security.Cryptography.SHA256]::Create()
        try { $actual = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') } finally { $stream.Dispose(); $sha.Dispose() }
        if ($actual -ne $expectedCoop) { throw 'Archive differs from inspected RC7-P7.' }
    } elseif (-not (Test-Path -LiteralPath $coopTarget) -or (Get-FileHash -LiteralPath $coopTarget).Hash -ne $expectedCoop) {
        throw 'Install the supplied coop mod first, or pass -InstallCoop.'
    }
    $files.Add([pscustomobject]@{ Relative = 'BepInEx\plugins\IfzNpcEnhanced\IfzNpcEnhanced.dll'; Entry = $null; Source = $addon })
    # Detect duplicate plugin assemblies before making any writes.
    foreach ($item in $files | Where-Object { $_.Relative -like 'BepInEx\plugins\*.dll' }) {
        $leaf = Split-Path -Leaf $item.Relative
        $expected = Join-Path $gamePath $item.Relative
        $duplicate = Get-ChildItem -LiteralPath (Join-Path $gamePath 'BepInEx\plugins') -Recurse -File -Filter $leaf |
            Where-Object { $_.FullName -ne $expected }
        if ($duplicate) { throw "Duplicate plugin found elsewhere: $($duplicate.FullName). Resolve it before installing." }
    }
    if ($CheckOnly) { Write-Output "Preflight passed: $($files.Count) files, game and coop versions matched. No files changed."; return }
    $backup = Join-Path $gamePath ('_npc_enhanced_backup_' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,6))
    New-Item -ItemType Directory -Path $backup | Out-Null
    $manifest = @()
    foreach ($item in $files) {
        $target = Join-Path $gamePath $item.Relative
        $existed = Test-Path -LiteralPath $target
        if ($existed) {
            $backupFile = Join-Path $backup $item.Relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $backupFile) -Force | Out-Null
            Copy-Item -LiteralPath $target -Destination $backupFile
        }
        $manifest += [pscustomobject]@{ relative = $item.Relative; existed = $existed }
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backup 'manifest.json') -Encoding UTF8
    foreach ($item in $files) {
        $target = Join-Path $gamePath $item.Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        if ($item.Entry) { [IO.Compression.ZipFileExtensions]::ExtractToFile($item.Entry, $target, $true) }
        else { Copy-Item -LiteralPath $item.Source -Destination $target -Force }
    }
    if ((Get-FileHash -LiteralPath $coopTarget).Hash -ne $expectedCoop) { throw "Installed coop hash mismatch. Backup: $backup" }
    if ((Get-FileHash -LiteralPath (Join-Path $gamePath 'BepInEx\plugins\IfzNpcEnhanced\IfzNpcEnhanced.dll')).Hash -ne (Get-FileHash -LiteralPath $addon).Hash) { throw 'Installed addon hash mismatch.' }
    [pscustomobject]@{ Installed = $true; GameRoot = $gamePath; Backup = $backup; Files = $files.Count; GameStarted = $false }
} finally { if ($zip) { $zip.Dispose() } }
