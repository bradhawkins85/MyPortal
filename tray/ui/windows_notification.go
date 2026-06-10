//go:build windows

package main

import (
	"encoding/base64"
	"os/exec"
	"strings"
	"syscall"
	"unicode/utf16"
)

func windowsToastEncodedCommand(title, body string) string {
	iconURL := windowsToastIconURL()
	template := "ToastText02"
	imageScript := ""
	if iconURL != "" {
		template = "ToastImageAndText02"
		imageScript = `$xml.GetElementsByTagName('image')[0].SetAttribute('src', '` + powershellSingleQuotedString(iconURL) + `')
`
	}
	script := `[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::` + template + `)
` + imageScript + `$xml.GetElementsByTagName('text')[0].InnerText = '` + powershellSingleQuotedString(title) + `'
$xml.GetElementsByTagName('text')[1].InnerText = '` + powershellSingleQuotedString(body) + `'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MyPortal').Show([Windows.UI.Notifications.ToastNotification]::new($xml))`
	return encodePowerShellCommand(script)
}

func windowsPersistentChatToastEncodedCommand(title, body, chatURL string) string {
	iconURL := windowsToastIconURL()
	template := "ToastText02"
	imageScript := ""
	if iconURL != "" {
		template = "ToastImageAndText02"
		imageScript = `$xml.GetElementsByTagName('image')[0].SetAttribute('src', '` + powershellSingleQuotedString(iconURL) + `')
`
	}
	script := `[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::` + template + `)
` + imageScript + `$toast = $xml.GetElementsByTagName('toast')[0]
$toast.SetAttribute('scenario', 'reminder')
$xml.GetElementsByTagName('text')[0].InnerText = '` + powershellSingleQuotedString(title) + `'
$xml.GetElementsByTagName('text')[1].InnerText = '` + powershellSingleQuotedString(body) + `'
$actions = $xml.CreateElement('actions')
$open = $xml.CreateElement('action')
$open.SetAttribute('content', 'Open chat')
$open.SetAttribute('activationType', 'protocol')
$open.SetAttribute('arguments', '` + powershellSingleQuotedString(chatURL) + `')
[void]$actions.AppendChild($open)
$input = $xml.CreateElement('input')
$input.SetAttribute('id', 'snoozeTime')
$input.SetAttribute('type', 'selection')
$input.SetAttribute('defaultInput', '15')
$selection = $xml.CreateElement('selection')
$selection.SetAttribute('id', '15')
$selection.SetAttribute('content', '15 minutes')
[void]$input.AppendChild($selection)
[void]$actions.AppendChild($input)
$snooze = $xml.CreateElement('action')
$snooze.SetAttribute('content', 'Snooze')
$snooze.SetAttribute('activationType', 'system')
$snooze.SetAttribute('arguments', 'snooze')
[void]$actions.AppendChild($snooze)
$dismiss = $xml.CreateElement('action')
$dismiss.SetAttribute('content', 'Dismiss')
$dismiss.SetAttribute('activationType', 'system')
$dismiss.SetAttribute('arguments', 'dismiss')
[void]$actions.AppendChild($dismiss)
[void]$toast.AppendChild($actions)
$notification = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notification.Tag = 'myportal-chat'
$notification.Group = 'myportal'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MyPortal').Show($notification)`
	return encodePowerShellCommand(script)
}

func showChatSessionNotification(title, body, chatURL string) {
	cmd := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", windowsPersistentChatToastEncodedCommand(title, body, chatURL))
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 /* CREATE_NO_WINDOW */}
	_ = cmd.Start()
}

func windowsToastIconURL() string {
	portalURL := strings.TrimSpace(gPortalURL)
	if portalURL == "" {
		return ""
	}
	return strings.TrimRight(portalURL, "/") + "/tray/icon.ico"
}

func powershellSingleQuotedString(value string) string {
	return strings.ReplaceAll(value, `'`, `''`)
}

func encodePowerShellCommand(script string) string {
	encoded := utf16.Encode([]rune(script))
	buf := make([]byte, 0, len(encoded)*2)
	for _, r := range encoded {
		buf = append(buf, byte(r), byte(r>>8))
	}
	return base64.StdEncoding.EncodeToString(buf)
}
