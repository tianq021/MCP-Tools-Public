# Build (and optionally deploy) the SlimeRancher2Bridge MelonLoader mod.
# 编译目标 net6.0；引用 MelonLoader.dll（游戏内 MelonLoader 目录，或 toolchain 里的解压副本）。
#
# 用法：
#   & .\build.ps1                 # 只编译 -> E:\SlimeRancher2MCP\dist\SlimeRancher2Bridge.dll
#   & .\build.ps1 -Deploy         # 编译 + 复制到 <游戏>\Mods\（游戏必须退出）
param(
    [string]$Game = 'H:\SteamLibrary\steamapps\common\Slime Rancher 2',
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $root)     # E:\SlimeRancher2MCP
$csproj = Join-Path $root 'SlimeRancher2Bridge.csproj'

# ---------- dotnet SDK ----------
$dotnet = Join-Path $projectRoot 'toolchain\dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { $dotnet = 'dotnet' }
& $dotnet --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "找不到 dotnet SDK（试 tool\get_dotnet_sdk.ps1 装项目内 SDK）" }

# ---------- MelonLoader 引用目录 ----------
$candidates = @(
    (Join-Path $Game 'MelonLoader\net6'),
    (Join-Path $Game 'MelonLoader'),
    (Join-Path $projectRoot 'toolchain\melonloader-0.7.3\MelonLoader\net6'),
    (Join-Path $projectRoot 'toolchain\melonloader-0.7.3\net6'),
    (Join-Path $projectRoot 'toolchain\melonloader-0.7.3\MelonLoader'),
    (Join-Path $projectRoot 'toolchain\melonloader-0.7.3')
)
$melonDir = $null
foreach ($c in $candidates) {
    if (Test-Path (Join-Path $c 'MelonLoader.dll')) { $melonDir = $c; break }
}
if (-not $melonDir) {
    throw "找不到 MelonLoader.dll。先安装 MelonLoader（tool\install_melonloader.ps1 -Apply）或解压到 toolchain\melonloader-0.7.3\"
}
Write-Host "dotnet    : $dotnet"
Write-Host "MelonLoader: $melonDir"

# ---------- 编译 ----------
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_NOLOGO = '1'
Write-Host "building   : $csproj"
& $dotnet build $csproj -c Release --nologo "-p:MelonLoaderDir=$melonDir" "-p:GameDir=$Game"
if ($LASTEXITCODE -ne 0) { throw "编译失败 exit=$LASTEXITCODE" }

$outDll = Join-Path $root 'bin\Release\SlimeRancher2Bridge.dll'
if (-not (Test-Path $outDll)) { throw "没找到编译产物: $outDll" }
$dist = Join-Path $projectRoot 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
Copy-Item $outDll $dist -Force
Get-Item $outDll | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize

# ---------- 部署 ----------
if ($Deploy) {
    $proc = Get-Process -Name 'SlimeRancher2' -ErrorAction SilentlyContinue
    if ($proc) { throw "游戏正在运行（pid $($proc.Id)），请先退出游戏再部署" }
    $modDir = Join-Path $Game 'Mods'
    New-Item -ItemType Directory -Force -Path $modDir | Out-Null
    # 覆盖前按哈希备份旧 DLL：手工 -Deploy 也不能丢掉可回滚的上一版。
    $installed = Join-Path $modDir 'SlimeRancher2Bridge.dll'
    if (Test-Path -LiteralPath $installed) {
        $oldHash = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash
        $oldBackup = Join-Path (Join-Path $projectRoot 'backups') ("SlimeRancher2Bridge-" + $oldHash.Substring(0, 12) + ".dll")
        if (!(Test-Path -LiteralPath $oldBackup)) { Copy-Item -LiteralPath $installed -Destination $oldBackup }
        Write-Host "backed up  : $oldBackup"
    }
    Copy-Item (Join-Path $dist 'SlimeRancher2Bridge.dll') $modDir -Force
    $newHash = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash
    Write-Host "deployed   : $modDir (sha256 $newHash)"
    Get-ChildItem $modDir | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
}
