$ErrorActionPreference = 'Stop'

# 编译 BroforceMapBridge.dll 到 dist/（带时间戳，避免游戏已加载的旧 DLL 被锁定）。
$Project = $PSScriptRoot
if (-not $Project) { $Project = Split-Path -Parent $MyInvocation.MyCommand.Definition }
$GameManaged = 'F:\SteamLibrary\steamapps\common\Broforce\Broforce_beta_Data\Managed'
$Compiler = 'C:\Windows\Microsoft.NET\Framework\v3.5\csc.exe'
$DistDir = Join-Path (Split-Path -Parent $Project) 'dist'
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$OutDll = Join-Path $DistDir ("BroforceMapBridge_" + $Stamp + ".dll")

if (-not (Test-Path -LiteralPath $Compiler)) { throw "C# compiler not found: $Compiler" }
if (-not (Test-Path -LiteralPath (Join-Path $GameManaged 'Assembly-CSharp.dll'))) {
    throw "Broforce assemblies not found: $GameManaged"
}
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

& $Compiler /nologo /target:library /optimize+ /platform:x64 `
    "/out:$OutDll" `
    "/reference:$GameManaged\Assembly-CSharp.dll" `
    "/reference:$GameManaged\UnityEngine.dll" `
    "/reference:$GameManaged\UnityEngine.CoreModule.dll" `
    (Join-Path $Project 'BroforceMapBridge.cs')
if ($LASTEXITCODE -ne 0) { throw "Bridge compilation failed with exit code $LASTEXITCODE" }

# 清理旧构建，只保留最近 5 个，避免 dist 无限增长。
Get-ChildItem $DistDir -Filter 'BroforceMapBridge_*.dll' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 5 |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "Bridge built: $OutDll"
