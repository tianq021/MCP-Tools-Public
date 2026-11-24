function Invoke-Civ {
    param([Parameter(Mandatory)][string]$Name, [hashtable]$Arguments = @{})
    $requestBody = @{name=$Name;arguments=$Arguments} | ConvertTo-Json -Depth 20 -Compress
    $result = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/call' -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($requestBody)) -ContentType 'application/json; charset=utf-8' -TimeoutSec 180
    Write-Output "[$Name]"
    $result.content | Where-Object type -eq 'text' | ForEach-Object { $_.text }
    if ($result.isError) { throw "MCP tool $Name returned an error" }
}
