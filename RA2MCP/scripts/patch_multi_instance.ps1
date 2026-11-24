param(
    [Parameter(Mandatory = $true)][string]$GameDir,
    [Parameter(Mandatory = $true)][ValidateRange(1, 4)][int]$Index
)
# Give a game copy its own single-instance mutex names.
#
# The vanilla engine creates two named mutexes at startup and treats an existing
# one as "TibSun is already running...Bail!" / waits for Autoplay to quit, so a
# second concurrently launched copy hangs before its window appears. Renaming
# the two GUID strings inside that copy's executable lets several real games run
# side by side (each still enforces its own single instance). Instance 0 keeps
# the vanilla names.
$ErrorActionPreference = 'Stop'
$GameDir = (Resolve-Path -LiteralPath $GameDir).Path
$exe = Join-Path $GameDir 'gamemd-spawn-ra2yrcpp.exe'
$marker = Join-Path $GameDir 'ra2mcp-instance.json'
if (-not (Test-Path -LiteralPath $exe)) { throw "Game executable not found: $exe" }

if (Test-Path -LiteralPath $marker) {
    $known = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if ($known -and $known.instance -eq $Index -and $known.patched) {
        Write-Output "Instance mutex already patched for index $Index ($marker)"
        return
    }
}

# Last hex digit becomes 2..5 (index 1..4); instance 0 keeps the original '1'.
$suffix = [string](1 + $Index)
$guids = @(
    '29e3bb2a-2f36-11d3-a72c-0090272fa661',   # AppMutex: "TibSun is already running...Bail!"
    '01AF9993-3492-11d3-8F6F-0060089C05B1'    # AutoPlayMutex: "Waiting for Autoplay to quit!"
)
$latin1 = [System.Text.Encoding]::GetEncoding(28591)   # byte <-> char bijection
$bytes = [System.IO.File]::ReadAllBytes($exe)
$text = $latin1.GetString($bytes)
$patched = @()
foreach ($guid in $guids) {
    $replacement = $guid.Substring(0, 35) + $suffix
    $first = $text.IndexOf($guid, [System.StringComparison]::Ordinal)
    if ($first -lt 0) { throw "Mutex GUID not found in $exe : $guid" }
    if ($text.IndexOf($guid, $first + $guid.Length, [System.StringComparison]::Ordinal) -ge 0) {
        throw "Mutex GUID appears more than once, refusing to guess: $guid"
    }
    $text = $text.Replace($guid, $replacement)
    $patched += $replacement
}
$newBytes = $latin1.GetBytes($text)
if ($newBytes.Length -ne $bytes.Length) { throw 'Patch changed executable size; refusing to write' }
[System.IO.File]::WriteAllBytes($exe, $newBytes)

@{
    schema_version = 1
    instance      = $Index
    patched       = $true
    patched_sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
    mutex_names   = $patched
    note          = 'Renamed single-instance/AutoPlay mutex GUIDs so parallel game copies can run together.'
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $marker -Encoding utf8
Write-Output "Patched instance $Index mutexes in $exe"
