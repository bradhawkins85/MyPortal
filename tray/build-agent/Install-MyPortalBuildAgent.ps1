#Requires -Version 7.2
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs the MyPortal Tray build agent on a Windows build server.

.DESCRIPTION
    Installs WiX v7 as a local .NET tool, copies MyPortalBuildAgent.ps1,
    writes its configuration (readable only by SYSTEM and Administrators) and
    registers a scheduled task that runs the agent as SYSTEM every two
    minutes. See docs/wiki/getting-started/Tray Build Server.md.

.EXAMPLE
    .\Install-MyPortalBuildAgent.ps1 -PortalUrl https://portal.example.com `
        -ApiKey (Read-Host 'API key') -CertificateThumbprint 0123ABCD... `
        -TimestampServer http://timestamp.digicert.com -RunNow
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PortalUrl,
    [Parameter(Mandatory)][string]$ApiKey,
    [Parameter(Mandatory)][string]$CertificateThumbprint,
    [string]$TimestampServer,
    [string]$GitHubRepo = 'bradhawkins85/MyPortal',
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'MyPortalBuildAgent'),
    [string]$WorkDir = 'C:\MyPortalBuild',
    [string]$WixVersion = '7.*',
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3

if ($PortalUrl -notmatch '^https://') { throw 'PortalUrl must start with https://' }
$CertificateThumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw 'The .NET SDK 8 or later is required. Install it from https://dotnet.microsoft.com/download and run this script again.'
}

$cert = Get-Item -LiteralPath "Cert:\LocalMachine\My\$CertificateThumbprint" -ErrorAction SilentlyContinue
if (-not $cert -or -not $cert.HasPrivateKey) {
    throw "Import the code-signing certificate (with its private key) into Local Machine > Personal first. Thumbprint $CertificateThumbprint was not found."
}

$toolsDir = Join-Path $InstallDir 'tools'
New-Item -ItemType Directory -Force -Path $InstallDir, $toolsDir, $WorkDir | Out-Null

$wix = Join-Path $toolsDir 'wix.exe'
if (Test-Path -LiteralPath $wix) {
    Write-Host 'Updating WiX...'
    dotnet tool update --tool-path $toolsDir wix --version $WixVersion
} else {
    Write-Host 'Installing WiX...'
    dotnet tool install --tool-path $toolsDir wix --version $WixVersion
}
if ($LASTEXITCODE -ne 0) { throw 'Installing WiX failed.' }

Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'MyPortalBuildAgent.ps1') -Destination $InstallDir -Force

$configDir = Join-Path $env:ProgramData 'MyPortalBuildAgent'
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$configPath = Join-Path $configDir 'config.json'
[ordered]@{
    PortalUrl             = $PortalUrl.TrimEnd('/')
    ApiKey                = $ApiKey
    CertificateThumbprint = $CertificateThumbprint
    TimestampServer       = $TimestampServer
    GitHubRepo            = $GitHubRepo
    WorkDir               = $WorkDir
    WixPath               = $wix
} | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding utf8

# The config holds the API key and the work folder briefly holds install
# tokens, so only SYSTEM and Administrators may read them.
foreach ($path in $configDir, $WorkDir) {
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in 'NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators') {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $path -AclObject $acl
}

$pwsh = (Get-Command pwsh).Source
$agent = Join-Path $InstallDir 'MyPortalBuildAgent.ps1'
$action = New-ScheduledTaskAction -Execute $pwsh -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$agent`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable
Register-ScheduledTask -TaskName 'MyPortal Build Agent' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "MyPortal build agent installed. Logs: $(Join-Path $WorkDir 'agent.log')"
if ($RunNow) {
    Start-ScheduledTask -TaskName 'MyPortal Build Agent'
    Write-Host 'Started the first run.'
}
