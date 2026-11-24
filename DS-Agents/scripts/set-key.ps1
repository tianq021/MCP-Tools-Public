# Sets the key only in this PowerShell process; never writes it to disk.
$dsSecret = Read-Host 'DeepSeek API Key (hidden input)' -AsSecureString
$dsPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($dsSecret)
try {
    $env:DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($dsPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($dsPointer)
    $dsSecret.Dispose()
    Remove-Variable dsPointer,dsSecret -ErrorAction SilentlyContinue
}
Write-Output 'DeepSeek key is set for this terminal only. Its value was not printed or saved.'
