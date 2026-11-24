param([switch]$NoOpen, [switch]$NoOllama)
$ErrorActionPreference = 'Stop'
$localRoot = $PSScriptRoot
$localLogs = Join-Path $localRoot 'runtime\logs'
$localTemp = Join-Path $localRoot 'runtime\temp'
New-Item -ItemType Directory -Path $localLogs,$localTemp -Force | Out-Null
$nodeExe = 'C:\nodejs\node.exe'
$dshEntry = 'F:\deepseekA-020-test\node_modules\@deepseek-ai\dsh\lib\bin.js'
$ollamaExe = 'C:\Users\hp\AppData\Local\Programs\Ollama\ollama.exe'
$env:DSH_HOME = Join-Path $localRoot 'dsh-home'
$env:DSH_TELEMETRY_MODE = 'OFF'
$env:CIV6_LOCAL_OLLAMA_KEY = 'local-only-no-billing'
$env:TEMP = $localTemp
$env:TMP = $localTemp

function Test-LocalHttp([int]$Port, [string]$Route = '/') {
    try {
        $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port$Route")
        $request.Proxy = $null
        $request.Timeout = 1500
        $response = $request.GetResponse()
        $response.Close()
        return $true
    } catch [System.Net.WebException] {
        if ($_.Exception.Response) { $_.Exception.Response.Close(); return $true }
        return $false
    }
}

if (!$NoOllama -and !(Test-LocalHttp 11434 '/api/version')) {
    $env:OLLAMA_HOST = '127.0.0.1:11434'
    $env:OLLAMA_MODELS = 'F:\models\Ollmammods'
    $env:OLLAMA_CONTEXT_LENGTH = '8192'
    $env:OLLAMA_NUM_PARALLEL = '1'
    $env:OLLAMA_MAX_LOADED_MODELS = '1'
    $env:OLLAMA_NO_CLOUD = '1'
    $ollamaProcess = Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localLogs 'ollama.stdout.log') -RedirectStandardError (Join-Path $localLogs 'ollama.stderr.log')
    $ollamaProcess.Id | Set-Content -LiteralPath (Join-Path $localLogs 'ollama-owned.pid')
    for ($attempt=0; $attempt -lt 30 -and !(Test-LocalHttp 11434 '/api/version'); $attempt++) { Start-Sleep -Milliseconds 500 }
    if (!(Test-LocalHttp 11434 '/api/version')) { throw 'Ollama did not become ready. See runtime\logs\ollama.stderr.log.' }
}

$statePath = Join-Path $localLogs 'server.json'
if (!(Test-LocalHttp 3096)) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $outLog = Join-Path $localLogs "dsh-$stamp.stdout.log"
    $errLog = Join-Path $localLogs "dsh-$stamp.stderr.log"
    $serverProcess = Start-Process -FilePath $nodeExe -ArgumentList @($dshEntry,'web','--no-open','--host','127.0.0.1','--port','3096') -WorkingDirectory $localRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    @{pid=$serverProcess.Id;stdout=$outLog;stderr=$errLog;url='http://127.0.0.1:3096';dshHome=$env:DSH_HOME} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    for ($attempt=0; $attempt -lt 40 -and !(Test-LocalHttp 3096); $attempt++) { Start-Sleep -Milliseconds 500 }
    if (!(Test-LocalHttp 3096)) { throw "Local game client did not become ready. See $errLog" }
}

if (!(Test-Path -LiteralPath $statePath)) { throw 'Port 3096 is occupied by an unknown service. No second instance was started.' }
$serverState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$match = @()
for ($attempt=0; $attempt -lt 120; $attempt++) {
    $serverLog = [string](Get-Content -LiteralPath $serverState.stdout -Raw)
    $match = [regex]::Matches($serverLog, 'http://127\.0\.0\.1:3096/\?token=[A-Za-z0-9._~-]+')
    if ($match.Count) { break }
    if (!(Get-Process -Id $serverState.pid -ErrorAction SilentlyContinue)) { throw "Local client exited. See $($serverState.stderr)" }
    Start-Sleep -Milliseconds 250
}
if (!$match.Count) { throw 'Local client authentication URL is not ready. Check the local logs.' }
if (!$NoOpen) { Start-Process $match[$match.Count-1].Value }
Write-Output 'Civilization VI local client is ready at 127.0.0.1:3096. Default model: gemma4:e4b.'
Write-Output 'This command did not start Civilization VI or send a model prompt.'
