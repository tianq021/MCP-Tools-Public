param(
    [Parameter(Mandatory)][string]$Calls,   # comma-separated name[=jsonargs]
    [string]$Out = 'E:\Civ6\logs\_civ_out.txt'
)
. "$PSScriptRoot\civ.ps1"

$sb = [System.Text.StringBuilder]::new()
foreach ($spec in $Calls.Split('|')) {
    $spec = $spec.Trim()
    if (-not $spec) { continue }
    $name, $json = $spec.Split('=', 2)
    $args = @{}
    if ($json) { $args = $json | ConvertFrom-Json -AsHashtable }
    try {
        $text = Invoke-Civ -Name $name -Arguments $args 2>&1 | Out-String
    } catch {
        $text = "ERROR: $_"
    }
    [void]$sb.AppendLine("### $name : $json")
    [void]$sb.AppendLine($text.TrimEnd())
    [void]$sb.AppendLine('')
}
[System.IO.File]::WriteAllText($Out, $sb.ToString(), [System.Text.UTF8Encoding]::new($false))
Write-Host "WROTE $Out"
