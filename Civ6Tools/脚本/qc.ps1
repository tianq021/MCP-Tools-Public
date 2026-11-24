param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Name,
    [Parameter(Position = 1)][string]$Json = '{}'
)
# Quick call to the Civ6 HTTP bridge (127.0.0.1:8766), UTF-8 safe.
$ErrorActionPreference = 'Stop'
$body = @{ name = $Name; arguments = ($Json | ConvertFrom-Json) } | ConvertTo-Json -Depth 20 -Compress
$url = 'http://127.0.0.1:8766/call'
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
$req = [System.Net.HttpWebRequest]::Create($url)
$req.Method = 'POST'
$req.ContentType = 'application/json; charset=utf-8'
$req.Timeout = 300000
$req.ReadWriteTimeout = 300000
$req.ContentLength = $bytes.Length
$stream = $req.GetRequestStream()
$stream.Write($bytes, 0, $bytes.Length)
$stream.Close()
$resp = $req.GetResponse()
$reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
$jsonText = $reader.ReadToEnd()
$reader.Close()
$resp.Close()
$respObj = $jsonText | ConvertFrom-Json
$text = ($respObj.content | ForEach-Object { $_.text }) -join "`n"
if ($respObj.isError) { Write-Output "### $Name [ERROR]" } else { Write-Output "### $Name [OK]" }
Write-Output $text
