param(
 [string]$Text,
 [ValidateSet('cute','gentle')][string]$Style='cute',
 [string]$OutputDirectory,
 [switch]$Play,
 [switch]$VerifyOnly
)
$ErrorActionPreference='Stop'
$Style=$Style.ToLowerInvariant()
if(-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime.local.json'))){throw 'Copy runtime.template.json to runtime.local.json and configure your WSL paths first.'}
$runtime=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.local.json') -Encoding UTF8 -Raw | ConvertFrom-Json
function Convert-ToWslPath([string]$Value){
 $full=[IO.Path]::GetFullPath($Value).Replace('\','/')
 if($full -notmatch '^[A-Za-z]:/'){throw '本机版需要Windows盘符路径。'}
 return '/mnt/'+$full.Substring(0,1).ToLowerInvariant()+$full.Substring(2)
}
$worker=Convert-ToWslPath (Join-Path $PSScriptRoot 'infer.py')
$envArg='LD_LIBRARY_PATH='+$runtime.python_library_dir
if($VerifyOnly){
 & wsl.exe -d $runtime.distribution -- env $envArg $runtime.python -B $worker --verify-only --style $Style
 if($LASTEXITCODE -ne 0){throw '校验失败，请检查日志与runtime.local.json中的本机环境路径。'}
 return
}
if([string]::IsNullOrWhiteSpace($Text)){throw '请使用 -Text 输入需要朗读的文字。'}
if(-not $OutputDirectory){$OutputDirectory=Join-Path $PSScriptRoot ('outputs/'+(Get-Date -Format 'yyyyMMdd_HHmmss')+'_'+[guid]::NewGuid().ToString('N').Substring(0,8))}
$target=[IO.Path]::GetFullPath($OutputDirectory)
if($target.TrimEnd('\') -eq $PSScriptRoot.TrimEnd('\')){throw '请选择新的输出子目录。'}
foreach($name in @('models','references','licenses','validation')){
 $protected=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot $name)).TrimEnd('\')
 if($target -eq $protected -or $target.StartsWith($protected+'\',[StringComparison]::OrdinalIgnoreCase)){throw '输出目录不能位于发行包资产目录内。'}
}
if((Test-Path -LiteralPath $target) -and ((-not (Test-Path -LiteralPath $target -PathType Container)) -or @(Get-ChildItem -LiteralPath $target -Force).Count -gt 0)){throw '输出目录须为空，已有结果不覆盖。'}
$requestPath=Join-Path ([IO.Path]::GetTempPath()) ('feiyu_request_'+[guid]::NewGuid().ToString('N')+'.json')
try{
 $request=@{text=$Text;style=$Style;output_dir=(Convert-ToWslPath $target)} | ConvertTo-Json -Depth 5
 [IO.File]::WriteAllText($requestPath,$request,[Text.UTF8Encoding]::new($false))
 & wsl.exe -d $runtime.distribution -- env $envArg $runtime.python -B $worker --request (Convert-ToWslPath $requestPath)
 if($LASTEXITCODE -ne 0){throw '肥鱼TTS生成失败，请保留输出目录中的job与日志排查。'}
 $audio=Join-Path $target 'speech.wav'
 if(-not (Test-Path -LiteralPath $audio)){throw '生成进程没有返回speech.wav。'}
 Write-Output ('音频：'+$audio)
 if($Play){$player=New-Object System.Media.SoundPlayer $audio;$player.PlaySync()}
}finally{
 if(Test-Path -LiteralPath $requestPath){Remove-Item -LiteralPath $requestPath -Force}
}
