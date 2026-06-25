//go:build windows

package main

import (
	"os/exec"
	"strings"
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

// showWindowsNotification prefers BurntToast when the module is already
// installed for the interactive user, then falls back to a native MessageBoxW
// notification.  BurntToast is invoked with a plain -Command script and
// parameter values rather than -EncodedCommand, generated .lnk files, temporary
// scripts or ExecutionPolicy Bypass, which keeps the notification path away
// from the malware-like patterns that triggered Defender ML detections.
func showWindowsNotification(title, body string) {
	go func() {
		if showBurntToastNotification(title, body) {
			return
		}
		showWindowsMessageBox(title, body)
	}()
}

func showChatSessionNotification(title, body, chatURL string) {
	showWindowsNotification(title, windowsChatNotificationMessage(body, chatURL))
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

func showBurntToastNotification(title, body string) bool {
	title = notificationTitle(title)
	body = notificationBody(title, body)
	cmd := exec.Command(
		"powershell.exe",
		"-NoProfile",
		"-NonInteractive",
		"-Command",
		burntToastPowerShellCommand(),
		"-Title",
		title,
		"-Body",
		body,
	)
	if err := cmd.Run(); err != nil {
		logger.Warn("BurntToast notification failed; falling back to MessageBoxW: %v", err)
		return false
	}
	return true
}

func burntToastPowerShellCommand() string {
	return `param([Parameter(Mandatory=$true)][string]$Title,[Parameter(Mandatory=$true)][string]$Body)
$ErrorActionPreference = 'Stop'
if (-not (Get-Module -ListAvailable -Name BurntToast)) { throw 'BurntToast module is not installed' }
Import-Module BurntToast -ErrorAction Stop
New-BurntToastNotification -Text @($Title, $Body) | Out-Null`
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
