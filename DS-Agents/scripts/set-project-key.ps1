# 只在当前 PowerShell 进程设置“本项目 API 专属”的 DeepSeek Key。
# 不写磁盘、不打印、不进入命令历史。关闭终端即失效。
$dsSecret = Read-Host 'Project DeepSeek API Key (hidden input)' -AsSecureString
$dsPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($dsSecret)
try {
    $env:DSH_ENTRY_DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($dsPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($dsPointer)
    $dsSecret.Dispose()
    Remove-Variable dsPointer, dsSecret -ErrorAction SilentlyContinue
}
Write-Output 'DSH_ENTRY_DEEPSEEK_API_KEY is set for this terminal only. Start the API in the same window.'
