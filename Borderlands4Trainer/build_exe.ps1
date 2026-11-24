param([string]$OutputDirectory = 'dist')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    python -m PyInstaller --noconfirm --distpath $OutputDirectory BL4Trainer.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
    $configTarget = Join-Path $OutputDirectory 'trainer_config.json'
    if (-not (Test-Path -LiteralPath $configTarget)) {
        Copy-Item -LiteralPath trainer_config.json -Destination $configTarget
    }
    Copy-Item -LiteralPath docs/EXE使用说明.md -Destination (Join-Path $OutputDirectory 'EXE使用说明.md') -Force
    $legacyLauncher = Join-Path $OutputDirectory '启动修改器.cmd'
    if (Test-Path -LiteralPath $legacyLauncher) { Remove-Item -LiteralPath $legacyLauncher }
} finally {
    Pop-Location
}
