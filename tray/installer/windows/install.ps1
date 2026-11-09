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
    [string]$MsiURL = "$PortalURL/static/tray/myportal-tray.msi",
    [switch]$AllowHttp
)

$ErrorActionPreference = 'Stop'

foreach ($url in @($PortalURL, $MsiURL)) {
    if (-not $AllowHttp -and -not $url.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) {
        throw "$url must start with https:// (pass -AllowHttp to override)"
    }
}

# Download into a new folder with a random name that only SYSTEM and
# Administrators can change. A fixed path such as $env:TEMP\myportal-tray.msi
# (C:\Windows\Temp when an RMM runs this as SYSTEM) could be created or swapped
# by another local user before msiexec runs it.
$workDir = Join-Path $env:TEMP ('myportal-tray-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir | Out-Null
& icacls.exe $workDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict access to $workDir"
}
$msiPath = Join-Path $workDir 'myportal-tray.msi'

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
    '/l*v', (Join-Path $workDir 'myportal-tray-install.log')
)
try {
    $proc = Start-Process msiexec.exe -ArgumentList $args -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "MSI install exited with code $($proc.ExitCode). Log: $(Join-Path $workDir 'myportal-tray-install.log')"
    }
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
} catch {
    Remove-Item -LiteralPath $msiPath -Force -ErrorAction SilentlyContinue
    throw
}
Write-Host 'MyPortal Tray installed successfully.'
