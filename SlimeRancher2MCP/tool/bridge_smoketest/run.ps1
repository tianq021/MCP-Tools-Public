# 编译并运行通道冒烟测试（不启动游戏）
# 说明：同时验证 P0 真实工具（sr2_gameinfo / sr2_saves）对真实游戏目录的读取。
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent (Split-Path -Parent $here)
$dotnet = Join-Path $root 'toolchain\dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { $dotnet = 'dotnet' }

# 让 ToolsCore 读真实游戏目录（无游戏进程时用环境变量覆盖）
$env:SR2_GAME_DIR = 'H:\SteamLibrary\steamapps\common\Slime Rancher 2'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_NOLOGO = '1'

& $dotnet run --project (Join-Path $here 'SmokeTest.csproj') -c Release
exit $LASTEXITCODE
