param([string]$Python = 'C:\Python312\python.exe')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    New-Item -ItemType Directory -Force -Path '.tmp','tests\artifacts','logs','downloads\wheels' | Out-Null
    $env:TEMP = Join-Path $projectRoot '.tmp'
    $env:TMP = $env:TEMP
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
    }
    $projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    & $projectPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'pip bootstrap failed' }
    & $projectPython -m pip --isolated install --no-index --find-links downloads\wheels --only-binary=:all: --require-hashes --no-cache-dir -r requirements.lock.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Offline dependency installation failed' }
    & $projectPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed' }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    Pop-Location
}
