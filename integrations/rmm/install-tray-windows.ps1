<#
.SYNOPSIS
    Installs (or upgrades) the MyPortal Tray Agent on Windows from any RMM.

.DESCRIPTION
    Downloads the tray MSI from your MyPortal server, installs it silently with
    the company's enrolment token, then waits for the tray service to enrol.

    Use one copy of this script for every company: only the parameters change.
    Each value can be passed as a parameter or, for RMMs that inject script
    variables as environment variables, as the environment variable shown.

      -PortalURL    / MYPORTAL_URL   Full MyPortal address, e.g. https://portal.example.com
      -EnrolToken   / ENROL_TOKEN    The company's tray install token (Admin > Tray >
                                     Install tokens). Store it as a secret/protected
                                     variable in your RMM.
      -AutoUpdate   / AUTO_UPDATE    true (default) or false.
      -MsiUrl       / MYPORTAL_MSI_URL  Optional: download the MSI from somewhere
                                     else (defaults to <PortalURL>/static/tray/myportal-tray.msi).
      -WaitSeconds  / WAIT_SECONDS   Seconds to wait for enrolment (default 120, 0 skips).
      -Force                         Reinstall even if the agent is already enrolled
                                     with this portal.

    Run as SYSTEM or a local administrator.

    Exit codes: 0 installed/enrolled (or already installed), 1 bad parameters,
    2 download failed, 3 MSI failed, 4 installed but not enrolled in time,
    5 not running elevated.

.EXAMPLE
    .\install-tray-windows.ps1 -PortalURL 'https://portal.example.com' -EnrolToken 'abc123'

.EXAMPLE
    # RMM script variables supplied as environment variables
    $env:MYPORTAL_URL = 'https://portal.example.com'; $env:ENROL_TOKEN = 'abc123'
    .\install-tray-windows.ps1
#>
[CmdletBinding()]
param(
    [string]$PortalURL = $env:MYPORTAL_URL,
    [string]$EnrolToken = $env:ENROL_TOKEN,
    [string]$AutoUpdate = $(if ($env:AUTO_UPDATE) { $env:AUTO_UPDATE } else { 'true' }),
    [string]$MsiUrl = $env:MYPORTAL_MSI_URL,
    [int]$WaitSeconds = $(if ($env:WAIT_SECONDS) { [int]$env:WAIT_SECONDS } else { 120 }),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is far slower with the progress bar.

$ServiceName = 'MyPortalTrayService'
$StatePath = Join-Path $env:ProgramData 'MyPortal\tray\tray-state.json'

function Write-Log([string]$Message) {
    Write-Output ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

function Get-TrayState {
    if (-not (Test-Path $StatePath)) { return $null }
    try { return Get-Content $StatePath -Raw | ConvertFrom-Json } catch { return $null }
}

# --- Validate --------------------------------------------------------------
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Log 'ERROR: run this script as SYSTEM or a local administrator.'
    exit 5
}

$PortalURL = ([string]$PortalURL).Trim().TrimEnd('/')
$EnrolToken = ([string]$EnrolToken).Trim()
if ($PortalURL -notmatch '^https://[A-Za-z0-9.\-]+(:\d+)?(/[A-Za-z0-9._~\-/]*)?$') {
    Write-Log "ERROR: PortalURL must be an https:// address (got '$PortalURL')."
    exit 1
}
if ($EnrolToken -notmatch '^[A-Za-z0-9_\-]{16,128}$') {
    Write-Log 'ERROR: EnrolToken is missing or has an unexpected format.'
    exit 1
}
switch -Regex ([string]$AutoUpdate) {
    '^(1|true|yes)$' { $AutoUpdate = 'true'; break }
    '^(0|false|no)$' { $AutoUpdate = 'false'; break }
    default { Write-Log "ERROR: AutoUpdate must be true or false (got '$AutoUpdate')."; exit 1 }
}
if (-not $MsiUrl) { $MsiUrl = "$PortalURL/static/tray/myportal-tray.msi" }

# --- Skip if already enrolled with this portal ------------------------------
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$state = Get-TrayState
if (-not $Force -and $service -and $state -and $state.device_uid -and
    ([string]$state.portal_url).TrimEnd('/') -eq $PortalURL) {
    if ($service.Status -ne 'Running') {
        Write-Log "Tray service is installed but $($service.Status); starting it."
        Start-Service -Name $ServiceName
    }
    Write-Log "MyPortal Tray is already installed and enrolled (device $($state.device_uid)). Use -Force to reinstall."
    exit 0
}

# --- Download --------------------------------------------------------------
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$workDir = Join-Path $env:TEMP ('myportal-tray-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir -Force | Out-Null
$msiPath = Join-Path $workDir 'myportal-tray.msi'
$logPath = Join-Path $env:TEMP 'myportal-tray-install.log'

try {
    Write-Log "Downloading $MsiUrl"
    Invoke-WebRequest -Uri $MsiUrl -OutFile $msiPath -UseBasicParsing
    $signature = Get-AuthenticodeSignature -FilePath $msiPath
    Write-Log "Downloaded $([math]::Round((Get-Item $msiPath).Length / 1MB, 1)) MB (signature: $($signature.Status))."
} catch {
    Write-Log "ERROR: download failed: $($_.Exception.Message)"
    Remove-Item $workDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 2
}

# --- Install ---------------------------------------------------------------
# Clear stale enrolment from a different portal so the service enrols afresh.
if ($state -and ([string]$state.portal_url).TrimEnd('/') -ne $PortalURL) {
    Write-Log "Existing install is enrolled with '$($state.portal_url)'; re-enrolling with $PortalURL."
    Remove-Item $StatePath -Force -ErrorAction SilentlyContinue
}

$msiArgs = @(
    '/i', "`"$msiPath`"",
    "MYPORTAL_URL=`"$PortalURL`"",
    "ENROL_TOKEN=`"$EnrolToken`"",
    "AUTO_UPDATE=$AutoUpdate",
    '/qn', '/norestart',
    '/l*v', "`"$logPath`""
)
Write-Log "Installing MyPortal Tray for $PortalURL (auto-update $AutoUpdate); log: $logPath"
$proc = Start-Process -FilePath 'msiexec.exe' -ArgumentList $msiArgs -Wait -PassThru
Remove-Item $workDir -Recurse -Force -ErrorAction SilentlyContinue
if ($proc.ExitCode -notin 0, 1641, 3010) {
    Write-Log "ERROR: msiexec exited with code $($proc.ExitCode). See $logPath."
    exit 3
}
if ($proc.ExitCode -in 1641, 3010) { Write-Log 'Installed; Windows reports a restart is pending.' }

# --- Wait for enrolment ----------------------------------------------------
if ($WaitSeconds -le 0) {
    Write-Log 'Installed. Skipping the enrolment check.'
    exit 0
}
$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    $state = Get-TrayState
    if ($state -and $state.device_uid) {
        Write-Log "MyPortal Tray installed and enrolled (device $($state.device_uid))."
        exit 0
    }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
Write-Log "WARNING: installed, but the device did not enrol within $WaitSeconds seconds (service: $(if ($service) { $service.Status } else { 'missing' }))."
Write-Log "Check that the token is valid and this PC can reach $PortalURL. Service log: $env:ProgramFiles\MyPortalTray\logs\service.log"
exit 4
