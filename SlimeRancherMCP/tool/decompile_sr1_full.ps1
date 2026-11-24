# 全量反编译 Slime Rancher 1 Assembly-CSharp.dll 到单个 .cs 文件（每个类型一个文件）
# 用法: & 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -File 本脚本 [-Filter 关键字]
param(
    [string]$Dll = 'F:\SteamLibrary\steamapps\common\Slime Rancher\SlimeRancher_Data\Managed\Assembly-CSharp.dll',
    [string]$DecompilerDll = 'E:\SlimeRancherMCP\tool\decompiler\pkg\lib\netstandard2.0\ICSharpCode.Decompiler.dll',
    [string]$OutDir = 'E:\SlimeRancherMCP\out\decompiled\full'
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
$utf8 = New-Object System.Text.UTF8Encoding $false

$types = @($dec.TypeSystem.MainModule.TypeDefinitions)
Write-Host "total types: $($types.Count)"
$i = 0
$ok = 0
$fail = 0
foreach ($t in $types) {
    $i++
    $full = $t.FullName
    if ($full.Contains('<')) { continue }   # 跳过编译器生成的嵌套类型
    try {
        $code = $dec.DecompileTypeAsString([ICSharpCode.Decompiler.TypeSystem.FullTypeName]::new($full))
        $safe = ($full -replace '[\\/:*?"<>|]', '_')
        [System.IO.File]::WriteAllText((Join-Path $OutDir "$safe.cs"), $code, $utf8)
        $ok++
    }
    catch {
        $fail++
        Write-Host "FAIL $full : $($_.Exception.Message)"
    }
    if ($i % 200 -eq 0) { Write-Host "progress $i/$($types.Count)  ok=$ok fail=$fail" }
}
Write-Host "DONE ok=$ok fail=$fail out=$OutDir"
