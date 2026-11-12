//go:build windows

package main

import (
	"os"
	"os/exec"
	"strings"
	"sync/atomic"
	"syscall"
	"unsafe"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

const windowsToastAppID = "MyPortal.Tray"

var (
	user32DLL      = syscall.NewLazyDLL("user32.dll")
	procMessageBox = user32DLL.NewProc("MessageBoxW")
)

const (
	messageBoxOK              = 0x00000000
	messageBoxIconInformation = 0x00000040
	messageBoxSetForeground   = 0x00010000
)

// showWindowsNotification prefers a BurntToast notification for the
// interactive user, installing the module for the current user when it is
// missing, then falls back to a native MessageBoxW notification.  BurntToast is
// invoked with a plain -Command script and parameter values rather than
// -EncodedCommand, generated .lnk files, temporary scripts or ExecutionPolicy
// Bypass, which keeps the notification path away from the malware-like
// patterns that triggered Defender ML detections.
func showWindowsNotification(title, body string) {
	go func() {
		if showBurntToastNotification(title, body, "", "") {
			return
		}
		showWindowsMessageBox(title, body)
	}()
}

// showChatSessionNotification shows a BurntToast notification with an
// "Open chat" button and reports true. When that is not possible it starts a
// MessageBoxW (which blocks until dismissed) in the background and reports
// false, so the caller opens the chat window itself.
func showChatSessionNotification(title, body, chatURL string) bool {
	if strings.TrimSpace(chatURL) == "" {
		showWindowsNotification(title, body)
		return false
	}
	if showBurntToastNotification(title, body, "Open chat", chatURL) {
		return true
	}
	go showWindowsMessageBox(title, windowsChatNotificationMessage(body, chatURL))
	return false
}

func windowsChatNotificationMessage(body, chatURL string) string {
	message := strings.TrimSpace(body)
	if strings.TrimSpace(chatURL) != "" {
		if message != "" {
			message += "\n\n"
		}
		message += "Open MyPortal Tray to view the chat."
	}
	return message
}

// burntToastInstallFailed stops repeated install attempts (and their network
// timeouts) for the rest of this tray session once one has failed.
var burntToastInstallFailed atomic.Bool

func showBurntToastNotification(title, body, buttonText, buttonURL string) bool {
	title = notificationTitle(title)
	body = notificationBody(title, body)
	if buttonText == "" || buttonURL == "" {
		buttonText, buttonURL = "", ""
	}
	cmd := exec.Command("powershell.exe", burntToastArgs()...)
	cmd.Env = append(os.Environ(), burntToastEnv(title, body, buttonText, buttonURL, gPortalURL, !burntToastInstallFailed.Load())...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	output, err := cmd.CombinedOutput()
	if err != nil {
		if strings.Contains(string(output), burntToastInstallFailedMarker) {
			burntToastInstallFailed.Store(true)
		}
		logger.Warn("BurntToast notification failed; falling back to MessageBoxW: %v: %s", err, strings.TrimSpace(string(output)))
		return false
	}
	return true
}

const burntToastInstallFailedMarker = "BurntToast install failed"

// burntToastArgs runs the fixed notification script. PowerShell joins every
// argument after -Command into the script text, so the title, message and
// URLs (which come from technicians and chat users) are never passed as
// arguments: they travel in environment variables and stay plain strings.
func burntToastArgs() []string {
	return []string{
		"-NoProfile",
		"-NonInteractive",
		"-WindowStyle", "Hidden",
		"-Command",
		burntToastPowerShellCommand(),
	}
}

func burntToastEnv(title, body, buttonText, buttonURL, portalURL string, allowInstall bool) []string {
	if buttonText == "" || buttonURL == "" {
		buttonText, buttonURL = "", ""
	}
	install := ""
	if allowInstall {
		install = "1"
	}
	return []string{
		"MYPORTAL_TOAST_TITLE=" + title,
		"MYPORTAL_TOAST_MESSAGE=" + body,
		"MYPORTAL_TOAST_PORTAL_URL=" + strings.TrimSpace(portalURL),
		"MYPORTAL_TOAST_BUTTON_TEXT=" + buttonText,
		"MYPORTAL_TOAST_BUTTON_URL=" + buttonURL,
		"MYPORTAL_TOAST_ALLOW_INSTALL=" + install,
	}
}

func burntToastPowerShellCommand() string {
	return `$ErrorActionPreference = 'Stop'
$Title = [string]$env:MYPORTAL_TOAST_TITLE
$Message = [string]$env:MYPORTAL_TOAST_MESSAGE
$PortalUrl = [string]$env:MYPORTAL_TOAST_PORTAL_URL
$ButtonText = [string]$env:MYPORTAL_TOAST_BUTTON_TEXT
$ButtonUrl = [string]$env:MYPORTAL_TOAST_BUTTON_URL
$AllowInstall = $env:MYPORTAL_TOAST_ALLOW_INSTALL -eq '1'
if (-not $Title) { throw 'No notification title' }
if (-not $Message) { $Message = $Title }
if ([Security.Principal.WindowsIdentity]::GetCurrent().IsSystem) { throw 'Running as SYSTEM, so the notification would not be seen' }
$cacheDir = Join-Path $env:LOCALAPPDATA 'MyPortal\Notifications'
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
if (-not (Get-Module -ListAvailable -Name BurntToast)) {
    if (-not $AllowInstall) { throw 'BurntToast module is not installed' }
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        if (-not (Get-PackageProvider -ListAvailable -Name NuGet -ErrorAction SilentlyContinue)) {
            Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force | Out-Null
        }
        Install-Module -Name BurntToast -Scope CurrentUser -Repository PSGallery -Force -AllowClobber
    } catch {
        throw ('` + burntToastInstallFailedMarker + `: ' + $_.Exception.Message)
    }
}
Import-Module BurntToast -ErrorAction Stop
$logoPath = $null
if ($PortalUrl) {
    $url = $PortalUrl.TrimEnd('/') + '/tray/icon.png'
    $pngPath = Join-Path $cacheDir 'logo.png'
    $sourcePath = Join-Path $cacheDir 'logo.source'
    $fresh = (Test-Path $pngPath) -and (Test-Path $sourcePath) -and ((Get-Content $sourcePath -Raw).Trim() -eq $url) -and ((Get-Item $pngPath).LastWriteTime -gt (Get-Date).AddDays(-1))
    if ($fresh) { $logoPath = $pngPath } else {
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $downloadPath = Join-Path $cacheDir 'logo.download'
            Invoke-WebRequest -Uri $url -OutFile $downloadPath -UseBasicParsing -TimeoutSec 30
            Move-Item $downloadPath $pngPath -Force
            Set-Content -Path $sourcePath -Value $url
            $logoPath = $pngPath
        } catch {
            if (Test-Path $pngPath) { $logoPath = $pngPath }
        }
    }
}
$bindingArgs = @{ Children = @((New-BTText -Content $Title), (New-BTText -Content $Message)) }
if ($logoPath) { $bindingArgs.AppLogoOverride = New-BTImage -Source $logoPath -AppLogoOverride -Crop Circle }
$visual = New-BTVisual -BindingGeneric (New-BTBinding @bindingArgs)
$contentArgs = @{ Visual = $visual; Duration = 'Long' }
if ($ButtonText -and $ButtonUrl) {
    $contentArgs.Actions = New-BTAction -Buttons @((New-BTButton -Content $ButtonText -Arguments $ButtonUrl), (New-BTButton -Dismiss))
}
Submit-BTNotification -Content (New-BTContent @contentArgs) -UniqueIdentifier 'MyPortal'`
}

func notificationTitle(title string) string {
	if strings.TrimSpace(title) == "" {
		return trayDisplayName(gConfig)
	}
	return title
}

func notificationBody(title, body string) string {
	if strings.TrimSpace(body) == "" {
		return title
	}
	return body
}

func showWindowsMessageBox(title, body string) {
	title = notificationTitle(title)
	body = notificationBody(title, body)
	titlePtr, err := syscall.UTF16PtrFromString(title)
	if err != nil {
		logger.Warn("showWindowsNotification: invalid title: %v", err)
		return
	}
	bodyPtr, err := syscall.UTF16PtrFromString(body)
	if err != nil {
		logger.Warn("showWindowsNotification: invalid body: %v", err)
		return
	}
	_, _, _ = procMessageBox.Call(
		0,
		uintptr(unsafe.Pointer(bodyPtr)),
		uintptr(unsafe.Pointer(titlePtr)),
		uintptr(messageBoxOK|messageBoxIconInformation|messageBoxSetForeground),
	)
}
