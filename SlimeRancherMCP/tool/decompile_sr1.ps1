# 用 ICSharpCode.Decompiler 8.2 (netstandard2.0) 反编译 Slime Rancher 1 的 Assembly-CSharp.dll
# 必须用 PowerShell 7 运行： & 'C:\Program Files\PowerShell\7\pwsh.exe' -File 本脚本
param(
    [string]$Dll = 'F:\SteamLibrary\steamapps\common\Slime Rancher\SlimeRancher_Data\Managed\Assembly-CSharp.dll',
    [string]$DecompilerDll = 'E:\SlimeRancherMCP\tool\decompiler\pkg\lib\netstandard2.0\ICSharpCode.Decompiler.dll',
    [string]$OutDir = 'E:\SlimeRancherMCP\out\decompiled',
    [string[]]$Types = @(),
    [switch]$ListAll
)

$ErrorActionPreference = 'Stop'
Add-Type -Path $DecompilerDll

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$resolver = [ICSharpCode.Decompiler.Metadata.UniversalAssemblyResolver]::new($Dll, $false, $null)
$resolver.AddSearchDirectory((Split-Path $Dll))
$settings = [ICSharpCode.Decompiler.DecompilerSettings]::new()
$settings.ThrowOnAssemblyResolveErrors = $false
$pe = [ICSharpCode.Decompiler.Metadata.PEFile]::new($Dll)
$dec = [ICSharpCode.Decompiler.CSharp.CSharpDecompiler]::new($pe, $resolver, $settings)

if ($ListAll) {
    $names = $dec.TypeSystem.MainModule.TypeDefinitions |
        ForEach-Object { $_.FullName }
    $names | Set-Content "$OutDir\_all_types.txt" -Encoding utf8
    Write-Host "types: $($names.Count) -> $OutDir\_all_types.txt"
    return
}

if (-not $Types -or $Types.Count -eq 0) {
    $Types = @('PlayerState', 'SceneContext')
}

foreach ($t in $Types) {
    try {
        $full = [ICSharpCode.Decompiler.TypeSystem.FullTypeName]::new($t)
        $code = $dec.DecompileTypeAsString($full)
        $safe = ($t -replace '[\\/:*?"<>|]', '_')
        $path = Join-Path $OutDir "$safe.cs"
        [System.IO.File]::WriteAllText($path, $code, (New-Object System.Text.UTF8Encoding $false))
        Write-Host "OK  $t -> $path  ($($code.Length) chars)"
    }
    catch {
        Write-Host "FAIL $t : $($_.Exception.Message)"
    }
}
