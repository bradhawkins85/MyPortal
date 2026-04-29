# MyPortal Tray — Windows one-liner RMM deployment script
# Copy-paste this into your RMM script (SyncroRMM / TacticalRMM) after
# replacing the two variables below, or pass them as script parameters.
#
# Usage:
#   .\install.ps1 -PortalURL 'https://portal.example.com' -EnrolToken 'TOKEN'
#
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PortalURL,
    [Parameter(Mandatory)][string]$EnrolToken,
    [string]$AutoUpdate = 'true',
    [string]$MsiURL = "$PortalURL/static/tray/myportal-tray.msi"
)

$ErrorActionPreference = 'Stop'
$msiPath = Join-Path $env:TEMP 'myportal-tray.msi'

Write-Host "Downloading MyPortal Tray installer from $MsiURL"
Invoke-WebRequest -Uri $MsiURL -OutFile $msiPath -UseBasicParsing

Write-Host 'Installing...'
$args = @(
    '/i', $msiPath,
    "MYPORTAL_URL=$PortalURL",
    "ENROL_TOKEN=$EnrolToken",
    "AUTO_UPDATE=$AutoUpdate",
    '/qn',
    '/norestart',
    '/l*v', (Join-Path $env:TEMP 'myportal-tray-install.log')
)
$proc = Start-Process msiexec.exe -ArgumentList $args -Wait -PassThru
if ($proc.ExitCode -ne 0) {
    throw "MSI install exited with code $($proc.ExitCode)"
}
Write-Host 'MyPortal Tray installed successfully.'
