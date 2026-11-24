param(
    [string]$GameDir = 'F:\SteamLibrary\steamapps\common\Infection Free Zone'
)

$ErrorActionPreference = 'Stop'

$managed = Join-Path $GameDir 'Infection Free Zone_Data\Managed'
$ifzPath = Join-Path $managed 'Ifz.dll'
if (-not (Test-Path -LiteralPath $ifzPath)) {
    throw "Ifz.dll not found: $ifzPath"
}

foreach ($name in @('UnityEngine.CoreModule.dll', 'UnityEngine.dll', 'Zenject.dll', 'Ifz.dll')) {
    $path = Join-Path $managed $name
    if (Test-Path -LiteralPath $path) {
        try { [void][Reflection.Assembly]::LoadFrom($path) } catch { }
    }
}

$assembly = [Reflection.Assembly]::LoadFrom($ifzPath)
$flags = [Reflection.BindingFlags]'Public,Instance,Static,DeclaredOnly'
$handler = $assembly.GetType('Commands.GameConsoleCommandHandler', $true)

Write-Output '=== Build fingerprint ==='
Get-FileHash -Algorithm SHA256 -LiteralPath $ifzPath |
    Select-Object Path, Hash |
    Format-List

Write-Output '=== Public game-console commands ==='
$handler.GetMethods($flags) |
    Where-Object {
        $_.CustomAttributes.AttributeType.FullName -contains 'Commands.Attributes.GameCommand'
    } |
    Sort-Object Name |
    ForEach-Object {
        $parameters = ($_.GetParameters() | ForEach-Object {
            "$($_.ParameterType.Name) $($_.Name)"
        }) -join ', '
        "$($_.ReturnType.Name) $($_.Name)($parameters)"
    }

Write-Output '=== Resource IDs ==='
$resourceId = $assembly.GetType('Gameplay.GameResources.ResourceID', $true)
$resourceId.GetFields([Reflection.BindingFlags]'Public,Static') |
    Sort-Object Name |
    Select-Object -ExpandProperty Name
