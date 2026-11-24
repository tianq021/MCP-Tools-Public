$dsProject = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $dsProject
try {
    python -B -m ds_agents run --project examples/sample_project --question 'Review edge cases and unresolved disagreements.' --mock
    if ($LASTEXITCODE -ne 0) { throw 'Demo failed. See the error above.' }
} finally {
    Pop-Location
}
