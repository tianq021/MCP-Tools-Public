param(
    [string]$Python = 'G:\Codex_mmx\2026-08-17\.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$spec = Join-Path $project 'BL3Trainer_CN.spec'
$config = Join-Path $project 'trainer_config.json'
$readme = Join-Path $project 'README.md'
$comparison = Join-Path $project '功能实现对比.md'
$dist = Join-Path $project 'dist'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}

Push-Location $project
try {
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed with exit code $LASTEXITCODE"
    }

    & $Python -m PyInstaller --noconfirm --clean $spec
    if ($LASTEXITCODE -ne 0) {
        throw "EXE packaging failed with exit code $LASTEXITCODE"
    }

    Copy-Item -LiteralPath $config -Destination (Join-Path $dist 'trainer_config.json') -Force
    Copy-Item -LiteralPath $readme -Destination (Join-Path $dist 'README.md') -Force
    Copy-Item -LiteralPath $comparison -Destination (Join-Path $dist '功能实现对比.md') -Force
}
finally {
    Pop-Location
}

Write-Host "Build complete: $dist\BL3Trainer_CN.exe"
