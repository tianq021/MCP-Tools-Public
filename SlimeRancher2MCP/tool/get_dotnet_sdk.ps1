# 下载并安装【项目内】.NET SDK 6（不装系统、不改 PATH）
# 用于编译 plugin\SlimeRancher2Bridge（net6.0）。已装过会跳过。
#
#   & .\get_dotnet_sdk.ps1              # 8.0? 不，本脚本固定 6.0.428（MelonLoader 0.7 用 .NET 6）
#   & .\get_dotnet_sdk.ps1 -Version 6.0.428
param(
    [string]$Version = '6.0.428'
)

$ErrorActionPreference = 'Stop'
$root = 'E:\SlimeRancher2MCP'
$toolchain = Join-Path $root 'toolchain'
New-Item -ItemType Directory -Force -Path $toolchain | Out-Null
$dst = Join-Path $toolchain 'dotnet'
$dotnetExe = Join-Path $dst 'dotnet.exe'

if (Test-Path $dotnetExe) {
    Write-Host "已存在: $dotnetExe"
    & $dotnetExe --list-sdks
    return
}

$zip = Join-Path $toolchain "dotnet-sdk-$Version-win-x64.zip"
$url = "https://builds.dotnet.microsoft.com/dotnet/Sdk/$Version/dotnet-sdk-$Version-win-x64.zip"
if (-not (Test-Path $zip)) {
    Write-Host "下载 $url"
    & curl.exe -L --fail --retry 3 -o $zip $url
    if ($LASTEXITCODE -ne 0) { throw "下载失败 exit=$LASTEXITCODE（可手动下载后放到 $zip）" }
}
Write-Host "解压到 $dst"
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $dst -Force
& $dotnetExe --list-sdks
Write-Host "完成：build.ps1 会自动优先用 $dotnetExe"
