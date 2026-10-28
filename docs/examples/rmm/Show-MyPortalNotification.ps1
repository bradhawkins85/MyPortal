<#
.SYNOPSIS
  Shows a Windows notification to the signed-in user, branded with the MyPortal tray icon.
.DESCRIPTION
  Uses the BurntToast module, installing it for the current user when it is
  missing. The logo is the tray agent's icon (Admin > Tray), downloaded as a
  PNG from MyPortal and cached for a day.

  Run it as the logged-in user: in MyPortal choose "Logged in user" for
  "Run as". Notifications sent as SYSTEM never reach anyone's screen, so the
  script stops with exit code 2 in that case.

  -Mode Timed       disappears by itself after -Duration (Short ~7s, Long ~25s)
                    and then waits in Notification Centre.
  -Mode Persistent  stays on screen until the user dismisses or snoozes it.

  -AllowSnooze adds a snooze time picker with Snooze and Dismiss buttons.
  Windows brings the notification back when the snooze time is up.

  After sending, the script checks that Windows put the notification in
  Notification Centre. If Windows dropped it, which happens silently when it
  cannot load the logo, the notification is sent again without the logo.
  Focus assist (Do not disturb) still hides the popup, leaving the
  notification in Notification Centre.

  Exit codes: 0 notification shown, 1 failed, 2 not running as the logged-in user.
.EXAMPLE
  .\Show-MyPortalNotification.ps1 -PortalUrl https://portal.example.com `
    -Title 'Restart required' -Message 'Please restart your computer before 5pm today.' `
    -Mode Persistent -AllowSnooze
.EXAMPLE
  .\Show-MyPortalNotification.ps1 -PortalUrl https://portal.example.com `
    -Title 'Ticket updated' -Message 'Your ticket #1042 has a new reply.' `
    -ButtonText 'Open ticket' -ButtonUrl https://portal.example.com/tickets/1042
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $PortalUrl,
    [Parameter(Mandatory)] [string] $Title,
    [Parameter(Mandatory)] [string] $Message,
    [ValidateSet('Timed', 'Persistent')] [string] $Mode = 'Timed',
    [ValidateSet('Short', 'Long')] [string] $Duration = 'Long',
    [switch] $AllowSnooze,
    [ValidateRange(1, 1440)] [int[]] $SnoozeMinutes = @(5, 15, 60, 240),
    [ValidateRange(1, 1440)] [int] $DefaultSnoozeMinutes = 15,
    # Removes the notification from Notification Centre after this many minutes; 0 keeps it.
    [ValidateRange(0, 10080)] [int] $ExpiresAfterMinutes = 0,
    [string] $ButtonText,
    [string] $ButtonUrl,
    # Overrides the tray icon with another PNG or JPEG; Windows notifications cannot show SVG or ICO.
    [string] $LogoUrl,
    # Showing again with the same identifier replaces the earlier notification.
    [string] $Identifier = 'MyPortal',
    [switch] $Silent
)

$ErrorActionPreference = 'Stop'
$cacheDir = Join-Path $env:LOCALAPPDATA 'MyPortal\Notifications'

if ([Security.Principal.WindowsIdentity]::GetCurrent().IsSystem) {
    Write-Output 'Running as SYSTEM, so the notification would not be seen. Run this script as the logged-in user.'
    exit 2
}
if (($ButtonText -and -not $ButtonUrl) -or ($ButtonUrl -and -not $ButtonText)) {
    Write-Output 'ButtonText and ButtonUrl must be given together.'
    exit 1
}

function Install-BurntToast {
    if (Get-Module -ListAvailable -Name BurntToast) { return }
    Write-Output 'Installing the BurntToast module for the current user.'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    if (-not (Get-PackageProvider -ListAvailable -Name NuGet -ErrorAction SilentlyContinue)) {
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force | Out-Null
    }
    Install-Module -Name BurntToast -Scope CurrentUser -Repository PSGallery -Force -AllowClobber
}

# Returns the path of a cached logo, refreshing it once a day, or $null if it cannot be fetched.
function Get-LogoPath {
    # /tray/icon.png is the public PNG copy of the icon the tray agent shows.
    $url = if ($LogoUrl) { $LogoUrl } else { $PortalUrl.TrimEnd('/') + '/tray/icon.png' }
    $pngPath = Join-Path $cacheDir 'logo.png'
    $sourcePath = Join-Path $cacheDir 'logo.source'
    $fresh = (Test-Path $pngPath) -and (Test-Path $sourcePath) -and
        ((Get-Content $sourcePath -Raw).Trim() -eq $url) -and
        ((Get-Item $pngPath).LastWriteTime -gt (Get-Date).AddDays(-1))
    if ($fresh) { return $pngPath }

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $downloadPath = Join-Path $cacheDir 'logo.download'
        Invoke-WebRequest -Uri $url -OutFile $downloadPath -UseBasicParsing -TimeoutSec 30
        if (-not (Test-LogoFile $downloadPath)) { throw "$url is not a PNG, JPEG or GIF image under 1 MB." }
        Move-Item $downloadPath $pngPath -Force
        Set-Content -Path $sourcePath -Value $url
        return $pngPath
    } catch {
        Write-Output "Could not fetch the logo, using the previous copy or none: $($_.Exception.Message)"
        if (Test-Path $pngPath) { return $pngPath }
        return $null
    }
}

function Format-SnoozeLabel([int] $Minutes) {
    if ($Minutes % 1440 -eq 0) { $n = $Minutes / 1440; return "$n day$(if ($n -ne 1) { 's' })" }
    if ($Minutes % 60 -eq 0) { $n = $Minutes / 60; return "$n hour$(if ($n -ne 1) { 's' })" }
    return "$Minutes minute$(if ($Minutes -ne 1) { 's' })"
}

# Windows silently drops a notification whose image it cannot load, so only
# small PNG, JPEG and GIF files are used as the logo.
function Test-LogoFile([string] $Path) {
    $file = Get-Item $Path -ErrorAction SilentlyContinue
    if (-not $file -or $file.Length -lt 8 -or $file.Length -gt 1MB) { return $false }
    $stream = [IO.File]::OpenRead($file.FullName)
    try {
        $head = New-Object byte[] 4
        [void] $stream.Read($head, 0, 4)
    } finally { $stream.Close() }
    $hex = [BitConverter]::ToString($head)
    return $hex -eq '89-50-4E-47' -or $hex.StartsWith('FF-D8-FF') -or $hex.StartsWith('47-49-46')
}

# Returns why Windows will not show this user's notifications, or $null when nothing blocks them.
function Get-NotificationBlocker {
    $pushKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications'
    if ((Get-ItemProperty $pushKey -ErrorAction SilentlyContinue).ToastEnabled -eq 0) {
        return 'Notifications are turned off for this user in Settings > System > Notifications.'
    }
    try {
        $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        # BurntToast sends notifications as Windows PowerShell.
        $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        $setting = [string] [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Setting
        if ($setting -and $setting -ne 'Enabled') {
            return "Windows will not show notifications from Windows PowerShell for this user ($setting)."
        }
    } catch {
        # The check needs Windows PowerShell 5.1; PowerShell 7 cannot load the WinRT type.
    }
    return $null
}

# Waits briefly for the notification to reach Notification Centre, where Windows
# puts every notification it accepted. $true when it is there or cannot be checked.
function Test-ToastShown([string] $UniqueIdentifier) {
    if (-not (Get-Command Get-BTHistory -ErrorAction SilentlyContinue)) { return $true }
    for ($i = 0; $i -lt 5; $i++) {
        Start-Sleep -Milliseconds 500
        if (Get-BTHistory -UniqueIdentifier $UniqueIdentifier) { return $true }
    }
    return $false
}

function New-ToastContent([string] $LogoPath) {
    $children = @(
        New-BTText -Text $Title
        New-BTText -Text $Message
    )
    $bindingArgs = @{ Children = $children }
    if ($LogoPath) {
        $bindingArgs.AppLogoOverride = New-BTImage -Source $LogoPath -AppLogoOverride
    }
    $visual = New-BTVisual -BindingGeneric (New-BTBinding @bindingArgs)

    $buttons = @()
    $inputs = @()
    if ($ButtonText) {
        $buttons += New-BTButton -Content $ButtonText -Arguments $ButtonUrl -ActivationType Protocol
    }
    if ($AllowSnooze) {
        $choices = @($SnoozeMinutes + $DefaultSnoozeMinutes | Sort-Object -Unique)
        $items = foreach ($minutes in $choices) {
            New-BTSelectionBoxItem -Id ([string] $minutes) -Content (Format-SnoozeLabel $minutes)
        }
        $inputs += New-BTInput -Id 'SnoozeTime' -DefaultSelectionBoxItemId ([string] $DefaultSnoozeMinutes) -Items $items
        $buttons += New-BTButton -Snooze -Id 'SnoozeTime'
    }
    # Windows only keeps a persistent notification on screen when it has a button.
    if ($AllowSnooze -or $Mode -eq 'Persistent') {
        $buttons += New-BTButton -Dismiss
    }

    $contentArgs = @{
        Visual   = $visual
        Scenario = if ($Mode -eq 'Persistent') { 'Reminder' } else { 'Default' }
        Duration = $Duration
    }
    if ($buttons.Count -gt 0) {
        $actionArgs = @{ Buttons = $buttons }
        if ($inputs.Count -gt 0) { $actionArgs.Inputs = $inputs }
        $contentArgs.Actions = New-BTAction @actionArgs
    }
    if ($Silent) { $contentArgs.Audio = New-BTAudio -Silent }
    New-BTContent @contentArgs
}

try {
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    $blocker = Get-NotificationBlocker
    if ($blocker) {
        Write-Output $blocker
        exit 1
    }
    Install-BurntToast
    Import-Module BurntToast

    $logoPath = Get-LogoPath
    if ($logoPath -and -not (Test-LogoFile $logoPath)) { $logoPath = $null }
    # Clear an earlier copy so Notification Centre only holds this one when it is checked.
    if (Get-Command Remove-BTNotification -ErrorAction SilentlyContinue) {
        Remove-BTNotification -UniqueIdentifier $Identifier -ErrorAction SilentlyContinue
    }
    $submitArgs = @{ Content = (New-ToastContent -LogoPath $logoPath); UniqueIdentifier = $Identifier }
    if ($ExpiresAfterMinutes -gt 0) { $submitArgs.ExpirationTime = (Get-Date).AddMinutes($ExpiresAfterMinutes) }
    Submit-BTNotification @submitArgs

    Remove-Item (Join-Path $cacheDir 'logo.download') -ErrorAction SilentlyContinue
    if (Test-ToastShown $Identifier) {
        Write-Output "Notification '$Title' shown to $env:USERNAME ($Mode$(if ($AllowSnooze) { ', snooze allowed' }))."
        exit 0
    }
    if ($logoPath) {
        Write-Output 'Windows dropped the notification with the logo, so it was sent again without it.'
        $submitArgs.Content = New-ToastContent -LogoPath $null
        Submit-BTNotification @submitArgs
        if (Test-ToastShown $Identifier) {
            Write-Output "Notification '$Title' shown to $env:USERNAME without the logo."
            exit 0
        }
    }
    Write-Output 'Windows accepted the notification but did not show it. Check Settings > System > Notifications for this user.'
    exit 1
} catch {
    Write-Output "Failed to show the notification: $($_.Exception.Message)"
    exit 1
}
