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
		// Try to get an authenticated popup URL; fall back to the plain chat URL.
		authedURL := requestChatTokenForRoom(0)
		if authedURL != "" {
			chatURL = authedURL
		} else {
			chatURL = buildChatURL(0)
		}
	}
	w := webview.New(false)
	defer w.Destroy()
	w.SetTitle("MyPortal Chat")
	w.SetSize(900, 650, webview.HintNone)
	w.Navigate(chatURL)
	w.Run()
}

func openNewTicketWindow(_ *api.ConfigResponse) {
	_ = exec.Command("open", gPortalURL+"/tickets/new").Start()
}

func showOSNotification(title, body string) {
	script := `display notification "` + body + `" with title "` + title + `"`
	_ = exec.Command("osascript", "-e", script).Start()
}
