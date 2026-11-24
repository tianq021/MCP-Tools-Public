[CmdletBinding()]
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

function Get-Json([string]$Url, [hashtable]$Headers = @{}) {
    $request = [System.Net.WebRequest]::Create($Url)
    $request.Proxy = $null
    $request.Timeout = 5000
    foreach ($key in $Headers.Keys) { $request.Headers[$key] = [string]$Headers[$key] }
    $response = $request.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        return ($reader.ReadToEnd() | ConvertFrom-Json)
    } finally {
        if ($reader) { $reader.Dispose() }
        $response.Dispose()
    }
}

function Get-ProcessInfo([int]$ProcessId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

$port = 17860
$urlFile = Join-Path $root 'data\server-url.txt'
if (Test-Path -LiteralPath $urlFile) {
    $text = Get-Content -LiteralPath $urlFile -Raw -ErrorAction SilentlyContinue
    if ($text -match ':(\d{2,5})') { $port = [int]$Matches[1] }
}
$base = "http://127.0.0.1:$port"
Write-Host "[1/3] 检查 $base 上的服务 ..."

$conn = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $conn) {
    Write-Host "      端口 $port 空闲，没有需要停止的服务，直接启动。"
    exit 0
}
$listenerPid = [int]$conn.OwningProcess

# The listener must identify itself as this app, otherwise never touch it.
$session = $null
try { $session = Get-Json "$base/api/session" } catch { }
if (-not $session -or $session.app -ne 'Qwen Atelier') {
    $owner = Get-ProcessInfo $listenerPid
    Write-Host "[错误] 端口 $port 被其他程序占用：PID $listenerPid，$($owner.ExecutablePath)"
    Write-Host "       它没有返回 Qwen Atelier 的会话信息，本次不做任何操作。"
    exit 2
}

# Walk up the launcher chain (uv venv shims spawn the real interpreter).
$chain = New-Object System.Collections.ArrayList
$current = $listenerPid
for ($depth = 0; $depth -lt 6; $depth++) {
    $info = Get-ProcessInfo $current
    if (-not $info) { break }
    if ($info.Name -notmatch '^python(w)?\.exe$' -or $info.CommandLine -notmatch 'run\.py') { break }
    [void]$chain.Add($info)
    if (-not $info.ParentProcessId) { break }
    $current = [int]$info.ParentProcessId
}
if ($chain.Count -eq 0) {
    Write-Host "[错误] 端口 $port 的监听进程不是通过 run.py 启动的，为避免误杀，本次不做任何操作。"
    exit 2
}
$rootProc = $chain[$chain.Count - 1]
Write-Host "      当前服务 PID $listenerPid，启动链：$(($chain | ForEach-Object { $_.ProcessId }) -join ' -> ')"

$busy = $false
try {
    $headers = @{ 'X-Studio-Token' = [string]$session.token }
    $status = Get-Json "$base/api/status" $headers
    if ($status.engine.active -or $status.engine.state -eq 'loading') { $busy = $true }
    $jobs = @(Get-Json "$base/api/image-tools/jobs" $headers)
    if (@($jobs | Where-Object { $_.status -eq 'queued' -or $_.status -eq 'running' }).Count -gt 0) { $busy = $true }
} catch {
    Write-Host "      无法读取任务状态（$($_.Exception.Message)），按空闲处理。"
}

if ($busy) {
    if ($DryRun) {
        Write-Host "      [DryRun] 有任务正在排队/运行，正式运行时会要求确认。"
    } else {
        $answer = Read-Host "      检测到任务正在排队/运行，强制重启会中断它。继续？输入 y 确认"
        if ($answer -notmatch '^(y|yes)$') {
            Write-Host "      已取消。"
            exit 1
        }
    }
}

if ($DryRun) {
    Write-Host "[DryRun] 将结束进程树 PID $($rootProc.ProcessId)，等待端口释放，然后由 cmd 启动新服务；本次未做任何修改。"
    exit 0
}

Write-Host "[2/3] 停止旧服务（进程树 PID $($rootProc.ProcessId)）..."
& taskkill.exe /PID $rootProc.ProcessId /T /F | Out-Null
$freed = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 250
    $still = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    $alive = Get-Process -Id $rootProc.ProcessId -ErrorAction SilentlyContinue
    if (-not $still -and -not $alive) {
        $freed = $true
        break
    }
}
if (-not $freed) {
    Write-Host "[错误] 旧服务或端口 $port 仍未释放，已取消启动。"
    exit 1
}
Write-Host "      旧服务已停止，端口 $port 已释放。"
Write-Host "[3/3] 交给 cmd 启动新服务 ..."
exit 0
