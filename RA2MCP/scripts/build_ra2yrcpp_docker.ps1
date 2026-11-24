#requires -Version 5
<#
.SYNOPSIS
  Build the loopback-only x86 ra2yrcpp bridge inside the local Docker builder image.
.DESCRIPTION
  Runs docker/Dockerfile.builder-built image `ra2mcp-builder:local` against the pinned
  vendor/ra2yrcpp checkout. Outputs land in E:\RA2MCP\build\pkg (libra2yrcpp.dll, addscn.exe, ...).
  No network access to Docker Hub is needed once the image exists; the repo is mounted read-only.
.NOTES
  The container build is a MinGW-w64 i686 cross build on Ubuntu 24.04, mirroring
  vendor/ra2yrcpp/docker/Dockerfile, with protobuf 3.21.12 built from the pinned submodule.
#>
[CmdletBinding()]
param(
  [string]$Repo = 'E:\RA2MCP\vendor\ra2yrcpp',
  [string]$OutDir = 'E:\RA2MCP\build',
  [string]$Image = 'ra2mcp-builder:local'
)

$ErrorActionPreference = 'Stop'
$env:DOCKER_CLI_HINTS = 'false'

$repoFull = (Resolve-Path $Repo).Path
$outFull = if (Test-Path $OutDir) { (Resolve-Path $OutDir).Path } else { New-Item -ItemType Directory -Force -Path $OutDir | Select-Object -Expand FullName }
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "== repo:  $repoFull"
Write-Host "== out:   $outFull"
Write-Host "== image: $Image"

# -v mounts: mount repo read-only so the build cannot dirty the vendored sources.
Copy-Item -Force (Join-Path $scriptDir 'build_ra2yrcpp.sh') (Join-Path $outFull 'build_ra2yrcpp.sh')

# Run through cmd.exe: PowerShell 5.1 turns native stderr into NativeCommandError
# records, which breaks `docker build` logs and can abort the script.
$dockerCmd = 'docker run --rm -v "{0}:/work/repo:ro" -v "{1}:/work/out" -w /work {2} bash /work/out/build_ra2yrcpp.sh' -f $repoFull, $outFull, $Image
& cmd.exe /c "$dockerCmd 2>&1"
if ($LASTEXITCODE -ne 0) { throw "docker build failed with exit code $LASTEXITCODE" }
