# MyPortal Tray - Microsoft Defender exclusion script for Windows RMMs.
# Run this script as LocalSystem or from an elevated PowerShell session.
[CmdletBinding()]
param(
    [string[]]$ExclusionPath
)

$ErrorActionPreference = 'Stop'

if (-not $ExclusionPath) {
    if (-not $env:ProgramFiles -or -not $env:ProgramData) {
        throw 'The ProgramFiles and ProgramData environment variables are required.'
    }

    $ExclusionPath = @(
        (Join-Path $env:ProgramFiles 'MyPortalTray'),
        (Join-Path $env:ProgramData 'MyPortal\tray')
    )
}

$isAdministrator = ([Security.Principal.WindowsPrincipal]
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
if (-not $isAdministrator) {
    throw 'Administrator privileges are required to configure Microsoft Defender exclusions.'
}

$addMpPreference = Get-Command -Name Add-MpPreference -ErrorAction SilentlyContinue
if (-not $addMpPreference) {
    throw 'Add-MpPreference is unavailable. Confirm that Microsoft Defender Antivirus is installed.'
}

# Normalize the input so repeated RMM runs do not submit duplicate paths.
$requestedPaths = @($ExclusionPath | ForEach-Object {
    if ([string]::IsNullOrWhiteSpace($_)) {
        return
    }

    [Environment]::ExpandEnvironmentVariables($_).TrimEnd('\')
} | Select-Object -Unique)

if ($requestedPaths.Count -eq 0) {
    throw 'At least one non-empty exclusion path is required.'
}

$currentPaths = @()
$getMpPreference = Get-Command -Name Get-MpPreference -ErrorAction SilentlyContinue
if ($getMpPreference) {
    $currentPaths = @((Get-MpPreference).ExclusionPath)
}

$pathsToAdd = @($requestedPaths | Where-Object {
    $requestedPath = $_
    -not ($currentPaths | Where-Object {
        [string]::Equals($_.TrimEnd('\'), $requestedPath, [StringComparison]::OrdinalIgnoreCase)
    })
})

if ($pathsToAdd.Count -eq 0) {
    Write-Host 'Microsoft Defender exclusions for MyPortal Tray are already configured.'
    exit 0
}

Write-Host "Adding Microsoft Defender exclusions for: $($pathsToAdd -join ', ')"
Add-MpPreference -ExclusionPath $pathsToAdd
Write-Host 'Microsoft Defender exclusions for MyPortal Tray were configured successfully.'
