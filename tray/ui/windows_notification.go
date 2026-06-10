//go:build windows

package main

import (
	"encoding/base64"
	"strings"
	"unicode/utf16"
)

func windowsToastEncodedCommand(title, body string) string {
	script := `[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$xml.GetElementsByTagName('text')[0].InnerText = '` + powershellSingleQuotedString(title) + `'
$xml.GetElementsByTagName('text')[1].InnerText = '` + powershellSingleQuotedString(body) + `'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MyPortal').Show([Windows.UI.Notifications.ToastNotification]::new($xml))`
	return encodePowerShellCommand(script)
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
