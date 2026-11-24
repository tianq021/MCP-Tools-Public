param(
    [string]$GameDir = 'F:\SteamLibrary\steamapps\common\Infection Free Zone'
)

$ErrorActionPreference = 'Stop'

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$managed = Join-Path $project 'managed'
$gameManaged = Join-Path $GameDir 'Infection Free Zone_Data\Managed'
$compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$python = 'G:\Codex_mmx\2026-08-17\.venv\Scripts\python.exe'
$bridgeSource = Join-Path $managed 'InfectionFreeZoneBridge.cs'
$bridgeOutput = Join-Path $managed 'InfectionFreeZoneBridgeV3.dll'
$spec = Join-Path $project 'InfectionFreeZoneResourceTrainer.spec'

if (-not (Test-Path -LiteralPath $compiler)) {
    throw "C# compiler not found: $compiler"
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $gameManaged 'Ifz.dll'))) {
    throw "Infection Free Zone assemblies not found: $gameManaged"
}

& $compiler /nologo /target:library /optimize+ /platform:x64 `
    "/out:$bridgeOutput" `
    "/reference:$gameManaged\Ifz.dll" `
    "/reference:$gameManaged\netstandard.dll" `
    "/reference:$gameManaged\UnityEngine.dll" `
    "/reference:$gameManaged\UnityEngine.CoreModule.dll" `
    $bridgeSource
if ($LASTEXITCODE -ne 0) {
    throw "Bridge compilation failed with exit code $LASTEXITCODE"
}

& $python -m unittest discover -s (Join-Path $project 'tests') -v
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed with exit code $LASTEXITCODE"
}

& $python -m PyInstaller --noconfirm --clean $spec
if ($LASTEXITCODE -ne 0) {
    throw "EXE packaging failed with exit code $LASTEXITCODE"
}

Write-Host "Build complete: $project\dist\Infection_Free_Zone_资源修改器.exe"
