# 安装 / 卸载 MelonLoader（SR2）
# 默认【预演】：只列出将写入的文件，不动游戏目录。加 -Apply 才真装。
#
#   & .\install_melonloader.ps1                     # 预演
#   & .\install_melonloader.ps1 -Apply              # 安装
#   & .\install_melonloader.ps1 -Uninstall          # 移除 MelonLoader（保留 Mods 里的插件；-Purge 全清）
param(
    [string]$Game = 'H:\SteamLibrary\steamapps\common\Slime Rancher 2',
    [string]$Zip = 'E:\SlimeRancher2MCP\toolchain\MelonLoader.x64.zip',
    [switch]$Apply,
    [switch]$Uninstall,
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\SlimeRancher2MCP'
$backupDir = Join-Path $projectRoot 'backups'

if (-not (Test-Path $Game)) { throw "游戏目录不存在: $Game" }
$manifest = Join-Path (Split-Path -Parent (Split-Path -Parent $Game)) 'appmanifest_1657630.acf'

# ---------------- 卸载 ----------------
if ($Uninstall) {
    $targets = @('MelonLoader', 'dotnet', 'winhttp.dll', 'version.dll', 'dobby.dll', 'MelonLoader.zip')
    if ($Purge) { $targets += 'Mods' }
    foreach ($t in $targets) {
        $p = Join-Path $Game $t
        if (Test-Path $p) {
            if ($Apply) { Remove-Item $p -Recurse -Force; Write-Host "removed: $p" }
            else { Write-Host "would remove: $p" }
        }
    }
    if (-not $Apply) { Write-Host "`n[预演] 加 -Apply 才会真正删除" }
    return
}

# ---------------- 安装 ----------------
if (-not (Test-Path $Zip)) {
    throw "缺少安装包: $Zip（先跑 tool\get_dotnet_sdk.ps1 或手动下载 MelonLoader.x64.zip）"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
try {
    $entries = $archive.Entries
    $top = $entries | ForEach-Object { ($_.FullName -split '/')[0] } | Sort-Object -Unique
    $totalMB = [math]::Round((($entries | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
    Write-Host "安装包: $Zip"
    Write-Host ("条目: {0} 个，解压后约 {1} MB" -f $entries.Count, $totalMB)
    Write-Host "顶层内容: $($top -join ', ')"
    Write-Host "目标目录: $Game"
}
finally { $archive.Dispose() }

if (-not $Apply) {
    Write-Host "`n[预演] 将写入："
    $archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        $archive.Entries | Select-Object -First 25 | ForEach-Object {
            "  " + $_.FullName + "  (" + [math]::Round($_.Length / 1KB, 1) + " KB)"
        }
        if ($archive.Entries.Count -gt 25) { "  ... 其余 $($archive.Entries.Count - 25) 个" }
    }
    finally { $archive.Dispose() }
    Write-Host "`n[预演] 备份：$manifest -> $backupDir"
    Write-Host "[预演] 加 -Apply 才会真正解压"
    return
}

# 备份 Steam 清单（回滚依据）
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
if ((Test-Path $manifest) -and -not (Test-Path (Join-Path $backupDir 'appmanifest_1657630.before-ml.acf'))) {
    Copy-Item $manifest (Join-Path $backupDir 'appmanifest_1657630.before-ml.acf')
    Write-Host "backup: $backupDir\appmanifest_1657630.before-ml.acf"
}

# 解压到游戏根目录
$archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
try {
    foreach ($entry in $archive.Entries) {
        $dest = Join-Path $Game ($entry.FullName -replace '/', '\')
        if ($entry.FullName.EndsWith('/')) {
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            continue
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true)
    }
}
finally { $archive.Dispose() }

Write-Host "`n已解压。当前游戏目录："
Get-ChildItem $Game | Select-Object Mode, Length, Name | Format-Table -AutoSize
Write-Host "下一步：通过 Steam 启动一次游戏，确认 MelonLoader 控制台出现（<游戏>\MelonLoader\Latest.log）"
