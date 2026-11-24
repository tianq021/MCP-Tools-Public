# 生成/检查共享桥接配置 E:\SlimeRancher2MCP\config\bridge.json
# 游戏内 mod 与 MCP server 都读这一份；token 是凭据，不外发。
param(
    [int]$Port = 18762,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$configDir = 'E:\SlimeRancher2MCP\config'
$configPath = Join-Path $configDir 'bridge.json'
New-Item -ItemType Directory -Force -Path $configDir | Out-Null

if ((Test-Path $configPath) -and -not $Force) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    Write-Host "配置已存在: $configPath"
    Write-Host ("  host={0} port={1} token={2}..." -f $cfg.host, $cfg.port, $cfg.token.Substring(0, [Math]::Min(4, $cfg.token.Length)))
    Write-Host "  要重建请加 -Force"
    return
}

$token = -join (1..32 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
$cfg = [ordered]@{
    host               = '127.0.0.1'
    port               = $Port
    token              = $token
    request_timeout_ms = 9000
    idempotency_ttl_s  = 600
    log_path           = 'E:\SlimeRancher2MCP\out\bridge\server.log'
}
$json = $cfg | ConvertTo-Json
[System.IO.File]::WriteAllText($configPath, $json, (New-Object System.Text.UTF8Encoding $false))
New-Item -ItemType Directory -Force -Path 'E:\SlimeRancher2MCP\out\bridge' | Out-Null
Write-Host "已创建: $configPath (port=$Port)"
