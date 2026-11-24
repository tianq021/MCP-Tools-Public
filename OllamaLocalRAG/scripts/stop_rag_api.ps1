[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pidPath = Join-Path $projectRoot 'storage\run\rag_api.pid'
$expectedApiPath = Join-Path $projectRoot 'app\rag_api.py'

if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
    Write-Output 'RAG API PID file does not exist.'
    exit 0
}

$processId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
if (-not $processInfo) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Output 'RAG API process is no longer running.'
    exit 0
}

if ($processInfo.CommandLine -notlike "*$expectedApiPath*") {
    throw "PID $processId does not belong to this RAG API; refusing to stop it."
}

Stop-Process -Id $processId
Remove-Item -LiteralPath $pidPath -Force
Write-Output "Stopped RAG API PID=$processId"
