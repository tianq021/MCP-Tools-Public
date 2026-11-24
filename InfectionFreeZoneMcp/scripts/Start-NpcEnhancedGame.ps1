param([string]$GameRoot = 'F:\SteamLibrary\steamapps\common\Infection Free Zone')
$ErrorActionPreference = 'Stop'
$gamePath = (Resolve-Path -LiteralPath $GameRoot).Path
$overrideFile = Join-Path $gamePath 'steam_appid.txt'
if ((Test-Path -LiteralPath $overrideFile) -and (Get-Content -LiteralPath $overrideFile -Raw).Trim() -ne '1465460') { throw 'Conflicting Steam AppID override. Restore the official Steam launch configuration first.' }
if (Get-Process -Name 'Infection Free Zone' -ErrorAction SilentlyContinue) { throw 'Game is already running.' }
& (Join-Path $PSScriptRoot 'Install-CoopChinese.ps1') -GameRoot $gamePath
# Ask Steam to launch its installed, licensed game with the correct application context.
Start-Process 'steam://rungameid/1465460'
