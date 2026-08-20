//go:build darwin && !nowebview

package main

import (
	"os/exec"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/webview/webview_go"
)

func openBrowser(url string) {
	_ = exec.Command("open", url).Start()
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
		chatURL = requestChatTokenForRoom(0)
	}
	if chatURL == "" {
		showOSNotification(trayDisplayName(cfg)+" Chat", "Could not open chat. Please try again shortly.")
		return
	}
	if openWithChatShell(chatURL, cfg) {
		return
	}
	w := webview.New(false)
	defer w.Destroy()
	w.SetTitle(trayDisplayName(cfg) + " Chat")
	w.SetSize(900, 650, webview.HintNone)
	w.Navigate(chatURL)
	w.Run()
}

func showOSNotification(title, body string) {
	script := `display notification "` + body + `" with title "` + title + `"`
	_ = exec.Command("osascript", "-e", script).Start()
}
