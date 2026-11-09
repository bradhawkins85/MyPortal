//go:build windows && !nowebview

package main

import (
	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/webview/webview_go"
	"golang.org/x/sys/windows"
)

// launchURL hands a validated link to the shell's URL handler directly.
// It never goes through cmd.exe, which would treat & | < > in the link as
// command separators.
func launchURL(url string) {
	verb, _ := windows.UTF16PtrFromString("open")
	target, err := windows.UTF16PtrFromString(url)
	if err != nil {
		return
	}
	_ = windows.ShellExecute(0, verb, target, nil, nil, windows.SW_SHOWNORMAL)
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
	w.SetTitle(trayDisplayName(cfg) + " Chat")
	w.SetSize(900, 650, webview.HintNone)
	w.Navigate(chatURL)
	w.Run()
}

func openNewTicketWindow(cfg *api.ConfigResponse) {
	openNewTicketDialog(cfg)
}

func showOSNotification(title, body string) {
	showWindowsNotification(title, body)
}
