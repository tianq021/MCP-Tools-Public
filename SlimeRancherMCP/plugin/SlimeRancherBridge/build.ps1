# Build and deploy the SlimeRancherBridge mod (HTTP bridge).
# Uses the .NET Framework bundled csc.exe (C# 5); no dotnet SDK required.
param(
    [string]$Game = 'F:\SteamLibrary\steamapps\common\Slime Rancher',
    [switch]$Deploy
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$managed = Join-Path $Game 'SlimeRancher_Data\Managed'
$srmlLibs = Join-Path $Game 'SRML\Libs'
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'

if (-not (Test-Path $csc)) { throw "csc.exe not found: $csc" }
foreach ($f in @('Assembly-CSharp.dll', 'SRML.dll', 'UnityEngine.CoreModule.dll', 'UnityEngine.dll')) {
    if (-not (Test-Path (Join-Path $managed $f))) { throw "missing dependency: $managed\$f (is SRML installed?)" }
}

# ---------- ensure shared config (same file is read by the game mod and the MCP server) ----------
$configDir = 'E:\SlimeRancherMCP\config'
$configPath = Join-Path $configDir 'bridge.json'
if (-not (Test-Path $configPath)) {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    $token = -join (1..32 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    $cfg = [ordered]@{
        host               = '127.0.0.1'
        port               = 18760
        token              = $token
        request_timeout_ms = 9000
        idempotency_ttl_s  = 600
        log_path           = 'E:\SlimeRancherMCP\out\bridge\server.log'
    }
    $json = ($cfg | ConvertTo-Json)
    [System.IO.File]::WriteAllText($configPath, $json, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "created config: $configPath"
} else {
    Write-Host "config exists: $configPath"
}

# ---------- compile ----------
$dist = Join-Path $root '..\dist\SlimeRancherBridge'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$out = Join-Path $dist 'SlimeRancherBridge.dll'

$refs = @(
    (Join-Path $managed 'Assembly-CSharp.dll'),
    (Join-Path $managed 'SRML.dll'),
    (Join-Path $managed 'UnityEngine.CoreModule.dll'),
    (Join-Path $managed 'UnityEngine.dll'),
    (Join-Path $managed 'UnityEngine.UI.dll'),
    (Join-Path $managed 'UnityEngine.UIModule.dll'),
    (Join-Path $managed 'UnityEngine.AnimationModule.dll'),
    (Join-Path $managed 'UnityEngine.PhysicsModule.dll'),
    (Join-Path $srmlLibs '0Harmony.dll')
)
# IMGUI / 动态字体 / 旧版输入 在 Unity 2019 拆成了独立模块；存在就加上。
foreach ($module in @('UnityEngine.IMGUIModule.dll', 'UnityEngine.TextRenderingModule.dll', 'UnityEngine.InputLegacyModule.dll')) {
    $modulePath = Join-Path $managed $module
    if (Test-Path $modulePath) { $refs += $modulePath }
}
$refArgs = @()
foreach ($r in $refs) { $refArgs += ('/r:' + $r) }

$sources = Get-ChildItem $root -Filter *.cs | ForEach-Object { $_.FullName }
Write-Host ("building -> " + $out + "  sources=" + $sources.Count)
$cscArgs = @('/nologo', '/target:library', '/platform:anycpu', '/optimize+', '/codepage:65001', ('/out:' + $out)) + $refArgs + @('/r:System.dll', '/r:System.Core.dll') + $sources
& $csc @cscArgs
if ($LASTEXITCODE -ne 0) { throw "compile failed exit=$LASTEXITCODE" }
Get-Item $out | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize

Copy-Item (Join-Path $root 'modinfo.json') $dist -Force

if ($Deploy) {
    $modDir = Join-Path $Game 'SRML\Mods\SlimeRancherBridge'
    New-Item -ItemType Directory -Force -Path $modDir | Out-Null
    Copy-Item (Join-Path $dist 'SlimeRancherBridge.dll') $modDir -Force
    Copy-Item (Join-Path $dist 'modinfo.json') $modDir -Force
    Write-Host "deployed to: $modDir"
    Get-ChildItem $modDir | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
}

