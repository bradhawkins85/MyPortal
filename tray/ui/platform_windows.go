//go:build windows && !nowebview

package main

import (
	"os/exec"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/webview/webview_go"
)

func openBrowser(url string) {
	_ = exec.Command("cmd", "/c", "start", url).Start()
}

func showTextWindow(title, text string) {
	w := webview.New(false)
	defer w.Destroy()
	w.SetTitle(title)
	w.SetSize(500, 400, webview.HintNone)
	w.SetHtml("<html><body style='font-family:sans-serif;padding:1rem'>" + text + "</body></html>")
	w.Run()
}

func openChatWindow(chatURL string, cfg *api.ConfigResponse) {
	if chatURL == "" {
		chatURL = buildChatURL(0)
	}
	w := webview.New(false)
	defer w.Destroy()
	w.SetTitle("MyPortal Chat")
	w.SetSize(900, 650, webview.HintNone)
	w.Navigate(chatURL)
	w.Run()
}

func showOSNotification(title, body string) {
	// Use PowerShell toast for Windows 10+.
	script := `[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$xml.GetElementsByTagName('text')[0].InnerText = '` + title + `'
$xml.GetElementsByTagName('text')[1].InnerText = '` + body + `'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MyPortal').Show([Windows.UI.Notifications.ToastNotification]::new($xml))`
	_ = exec.Command("powershell", "-NonInteractive", "-Command", script).Start()
}
