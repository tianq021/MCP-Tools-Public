# Capture a screen region (the game window rectangle) to a PNG.
# Used only as visual evidence that the window is really shown / hidden.
param(
    [Parameter(Mandatory = $true)][int]$Left,
    [Parameter(Mandatory = $true)][int]$Top,
    [Parameter(Mandatory = $true)][int]$Right,
    [Parameter(Mandatory = $true)][int]$Bottom,
    [Parameter(Mandatory = $true)][string]$OutPng
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$l = [math]::Max($Left, $bounds.Left)
$t = [math]::Max($Top, $bounds.Top)
$r = [math]::Min($Right, $bounds.Right)
$b = [math]::Min($Bottom, $bounds.Bottom)
$w = $r - $l
$h = $b - $t
if ($w -le 0 -or $h -le 0) { throw "empty capture region: $l,$t,$r,$b" }
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($l, $t, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$dir = Split-Path -Parent $OutPng
if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$bmp.Save($OutPng, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
Write-Output ("captured {0}x{1} -> {2}" -f $w, $h, $OutPng)
